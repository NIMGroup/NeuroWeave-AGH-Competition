"""Durable Slurm adapter for trusted runners; no caller-provided shell commands."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from ..run_models import RunArtifact, RunExecutionOptions
from .base import ExecutionCancelled, ExecutionDetached, RunnerContext, RunnerResult
from .slurm_worker import RUNTIME_ENV_ALLOWLIST, atomic_json, validated_artifacts


TERMINAL_STATES = frozenset({"COMPLETED", "CANCELLED", "FAILED", "TIMEOUT", "NODE_FAIL",
                            "OUT_OF_MEMORY", "PREEMPTED", "BOOT_FAIL", "DEADLINE", "REVOKED"})
JOB_ID = re.compile(r"[1-9][0-9]{0,11}")


class SlurmBackend:
    def __init__(self) -> None:
        self.enabled = os.getenv("LOCALFLOW_SLURM_ENABLED", "false").lower() in {"1", "true", "yes"}
        self.worker_root = Path(os.getenv("LOCALFLOW_SLURM_WORKER_ROOT", "/opt/neuroweave/worker/current")).resolve()
        # Preserve the venv interpreter path: resolving its symlink would bypass
        # pyvenv.cfg and silently switch to the base interpreter's site-packages.
        self.python = Path(os.getenv("LOCALFLOW_SLURM_PYTHON", "/opt/neuroweave/envs/scientific-py311/bin/python")).absolute()
        self.output_root = Path(os.getenv("LOCALFLOW_SLURM_OUTPUT_ROOT", "/home/data/neuroweave/runs/hipeson"))
        self.poll_seconds = max(0.1, min(30.0, float(os.getenv("LOCALFLOW_SLURM_POLL_SECONDS", "2"))))
        self.cancel_timeout = max(5.0, min(300.0, float(os.getenv("LOCALFLOW_SLURM_CANCEL_TIMEOUT_SECONDS", "60"))))
        self.missing_timeout = max(5.0, float(os.getenv("LOCALFLOW_SLURM_MISSING_TIMEOUT_SECONDS", "60")))
        self.queue_timeout = max(60.0, float(os.getenv("LOCALFLOW_SLURM_QUEUE_TIMEOUT_SECONDS", "86400")))

    @property
    def worker(self) -> Path:
        return self.worker_root / "bakend" / "app" / "execution" / "slurm_worker.py"

    @staticmethod
    def _command(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(argv, capture_output=True, text=True, errors="replace",
                              timeout=20, check=False, shell=False)

    def status(self) -> dict[str, Any]:
        result: dict[str, Any] = {"enabled": self.enabled, "available": False, "reason": "Slurm 执行未启用",
                                  "partitions": [], "gpu_available": False, "nodes": [],
                                  "worker_root": str(self.worker_root), "python": str(self.python),
                                  "output_root": str(self.output_root)}
        if not self.enabled:
            return result
        missing = [name for name in ("sbatch", "scontrol", "scancel", "sinfo") if not shutil.which(name)]
        if missing:
            result["reason"] = "Slurm 命令缺失：" + ", ".join(missing)
            return result
        if not self.worker.is_file() or not self.python.is_file():
            result["reason"] = "共享 Worker 代码或 Python 入口不存在"
            return result
        if not self.output_root.is_dir() or not os.access(self.output_root, os.W_OK | os.X_OK):
            result["reason"] = "共享输出根目录不存在或不可写"
            return result
        try:
            completed = self._command(["sinfo", "--noheader", "--Node", "--format=%P|%a|%t|%G|%N|%c|%m|%e|%C"])
        except (OSError, subprocess.TimeoutExpired) as exc:
            result["reason"] = f"无法查询 Slurm：{exc}"
            return result
        if completed.returncode:
            result["reason"] = "Slurm 查询失败：" + completed.stderr.strip()[-500:]
            return result
        partitions: set[str] = set()
        for line in completed.stdout.splitlines():
            parts = line.strip().split("|")
            if len(parts) not in (5, 9):
                continue
            partition, availability, state, gres, node = parts[:5]
            partition = partition.rstrip("*")
            if (not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", partition)
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", node)):
                continue
            usable = availability == "up" and "*" not in state and not any(item in state.lower() for item in
                        ("down", "drain", "fail", "maint", "unknown", "inval", "power"))
            if usable:
                partitions.add(partition)
            gpu = bool(re.search(r"(?:^|,)gpu(?::[^:,()]+)?:[1-9][0-9]*(?:\(|,|$)", gres))
            counts = re.findall(r"(?:^|,)gpu(?::[^:,()]+)?:([0-9]+)(?:\([^)]*\))?", gres)
            capacity = {}
            if len(parts) == 9:
                try:
                    capacity = {"cpus": int(parts[5]), "memory_mb": int(parts[6]),
                                "free_memory_mb": max(0, int(parts[7])),
                                "allocated_cpus": int(parts[8].split("/")[0])}
                except ValueError:
                    pass
            result["nodes"].append({"name": node, "partition": partition, "state": state,
                                     "gres": gres, "schedulable": usable,
                                     "gpus": sum(map(int, counts)), **capacity})
            result["gpu_available"] |= usable and gpu
        result["partitions"] = sorted(partitions)
        result["available"] = bool(partitions)
        result["reason"] = "Slurm 与共享 Worker 可用；具体方法将在计算节点预检" if partitions else "当前没有可调度分区"
        return result

    def execute(self, context: RunnerContext, options: dict[str, Any],
                job_state: dict[str, Any] | None, on_job: Callable[[dict[str, Any]], None]) -> RunnerResult:
        latest = dict(job_state or {})

        def persist(state: dict[str, Any]) -> None:
            # Keep in-memory evidence even if the database callback itself fails
            # after sbatch accepted a job. Such errors must not release the run.
            latest.clear()
            latest.update(state)
            on_job(dict(state))

        try:
            return self._execute(context, options, job_state, persist)
        except (ExecutionCancelled, ExecutionDetached):
            raise
        except Exception as exc:
            terminal = latest.get("terminal", {})
            confirmed = (terminal.get("state") in TERMINAL_STATES
                         and terminal.get("job_id") == latest.get("job_id")
                         and terminal.get("submission_token") == latest.get("submission_token")
                         and bool(re.fullmatch(r"\d+:\d+", str(terminal.get("exit_code", "")))))
            if latest and not confirmed:
                self._interrupt(latest, persist, "monitoring_unavailable" if latest.get("job_id") else "submission_uncertain",
                                f"Slurm 作业状态尚未确认，保留运行记录以免重复提交：{exc}")
            raise

    def _execute(self, context: RunnerContext, options: dict[str, Any],
                 job_state: dict[str, Any] | None, on_job: Callable[[dict[str, Any]], None]) -> RunnerResult:
        options = RunExecutionOptions.model_validate(options).model_dump()
        state = dict(job_state or {})
        self._raise_if_detached(context)
        try:
            output = context.output_directory.resolve(strict=False)
            if context.workflow.workspace_root_path:
                from ..workspace_paths import WorkspacePaths
                policy = WorkspacePaths(self.output_root.parent / "workspaces")
                allowed_output = policy.output_root(context.workflow.workspace_root_path, context.workflow.output_relative_path)
                output = policy.resolve(allowed_output, str(context.output_directory), write=True)
            else:
                output.relative_to(self.output_root.resolve(strict=True))
            output.mkdir(parents=True, exist_ok=True, mode=0o700)
            output = output.resolve(strict=True)
        except (OSError, ValueError) as exc:
            if state:
                self._interrupt(state, on_job, "shared_storage_unavailable", f"Slurm 共享输出暂不可用，保留已有作业：{exc}")
            raise
        if state:
            self._validate_state(state, output)
            if state.get("options") and RunExecutionOptions.model_validate(state["options"]).model_dump() != options:
                self._interrupt(state, on_job, "snapshot_mismatch", "恢复请求与原作业资源快照不一致，保留原作业待核查")
            if not state.get("job_id"):
                self._interrupt(state, on_job, "submission_uncertain",
                                "Slurm 提交状态不确定：已有 submission_token 但没有 Job ID；"
                                f"请用 squeue/sacct 按作业名 nw-{state['submission_token']} 排查，禁止自动重复提交")
        else:
            context.raise_if_cancelled()
            if not self.enabled:
                raise RuntimeError("Slurm 执行未启用")
            token = uuid4().hex
            directory = output / ".slurm" / token
            directory.mkdir(parents=True, mode=0o700)
            state = {"submission_token": token, "job_directory": str(directory), "state": "SUBMITTING",
                     "submitted_at": time.time(), "worker_root": str(self.worker_root), "options": options}
            payload = {"schema_version": 1, "submission_token": token, "run_id": context.run_id,
                       "worker_root": str(self.worker_root),
                       "workflow": context.workflow.model_dump(mode="json"),
                       "step": context.step.model_dump(mode="json"), "method": context.method.model_dump(mode="json"),
                       "output_directory": str(output), "options": options,
                       "runtime_environment": {name: os.environ[name] for name in sorted(RUNTIME_ENV_ALLOWLIST)
                                               if name in os.environ}}
            atomic_json(directory / "payload.json", payload)
            state["payload_sha256"] = hashlib.sha256((directory / "payload.json").read_bytes()).hexdigest()
            script = directory / "job.sh"
            script.write_text(self._script(directory / "payload.json"), encoding="utf-8", newline="\n")
            # Commit the submission intent BEFORE sbatch; a crash here is never
            # interpreted as permission to submit a duplicate on restart.
            on_job(dict(state))
            self._raise_if_detached(context)
            context.raise_if_cancelled()
            argv = ["sbatch", "--parsable", "--export=NONE", "--no-requeue",
                    f"--job-name=nw-{token}", f"--partition={options['partition']}", "--nodes=1", "--ntasks=1",
                    f"--cpus-per-task={options['cpus']}", f"--mem={options['memory_gb']}G",
                    f"--time={options['time_minutes']}", f"--chdir={output}",
                    f"--output={directory / 'slurm-%j.out'}", f"--error={directory / 'slurm-%j.err'}"]
            if options["gpus"]:
                argv.append(f"--gres=gpu:{options['gpus']}")
            if options.get("node"):
                argv.append(f"--nodelist={options['node']}")
            argv.append(str(script))
            try:
                completed = self._command(argv)
            except (OSError, subprocess.TimeoutExpired) as exc:
                self._interrupt(state, on_job, "submission_uncertain",
                                f"Slurm 提交结果不确定（nw-{token}），请核查后处理：{exc}")
            if completed.returncode:
                state["submission_error"] = completed.stderr.strip()[-1000:]
                self._interrupt(state, on_job, "submission_uncertain", "sbatch 返回错误，请核查是否已创建作业：" + state["submission_error"])
            identifier = completed.stdout.strip().split(";", 1)[0]
            if not JOB_ID.fullmatch(identifier):
                self._interrupt(state, on_job, "submission_uncertain",
                                f"sbatch 未返回有效 Job ID；请核查作业 nw-{token}，禁止盲目重提")
            state.update(job_id=identifier, state="SUBMITTED")
            on_job(dict(state))
            context.log(f"已提交 Slurm 作业 {identifier}，分区 {options['partition']}", 1.0, "info")
        try:
            return self._monitor(context, state, on_job)
        except (OSError, ValueError) as exc:
            if not state.get("terminal"):
                self._interrupt(state, on_job, "monitoring_unavailable", f"监控 Slurm 作业时共享文件不可读，保留已有作业：{exc}")
            raise

    def _script(self, payload: Path) -> str:
        # env -i also protects against Slurm versions that implicitly load the
        # login environment for --export=NONE. Only scheduler-owned variables survive.
        argv = shlex.join([str(self.python), "-B", "-s", "-u", str(self.worker), "--payload", str(payload)])
        return ("#!/bin/bash\nset -eu\numask 077\n"
                "runtime_env=('PATH=/usr/local/bin:/usr/bin:/bin' 'LANG=C.UTF-8' 'PYTHONNOUSERSITE=1')\n"
                "while IFS= read -r name; do\n"
                "  case \"$name\" in SLURM_*|CUDA_VISIBLE_DEVICES|GPU_DEVICE_ORDINAL|ROCR_VISIBLE_DEVICES|HIP_VISIBLE_DEVICES|NVIDIA_VISIBLE_DEVICES)\n"
                "    runtime_env+=(\"$name=${!name}\");; esac\n"
                "done < <(compgen -e)\n"
                "exec /usr/bin/env -i \"${runtime_env[@]}\" " + argv + "\n")

    @staticmethod
    def _validate_state(state: dict[str, Any], output: Path) -> None:
        token = str(state.get("submission_token", ""))
        if not re.fullmatch(r"[a-f0-9]{32}", token):
            raise RuntimeError("无效的 Slurm 提交标记")
        if Path(state.get("job_directory", "")).resolve() != output / ".slurm" / token:
            raise RuntimeError("Slurm 作业目录不属于当前步骤")
        if state.get("job_id") and not JOB_ID.fullmatch(str(state["job_id"])):
            raise RuntimeError("无效的 Slurm Job ID")
        if state.get("payload_sha256"):
            raw = (output / ".slurm" / token / "payload.json").read_bytes()
            if hashlib.sha256(raw).hexdigest() != state["payload_sha256"]:
                raise ExecutionDetached("Slurm payload 与已提交快照不一致，保留原作业待核查")
            payload = json.loads(raw)
            if (payload.get("submission_token") != token or payload.get("options") != state.get("options")
                    or payload.get("worker_root") != state.get("worker_root")):
                raise ExecutionDetached("Slurm payload 的资源或 Worker 版本与持久记录不一致")

    @staticmethod
    def _raise_if_detached(context: RunnerContext) -> None:
        if context.detach_event is not None and context.detach_event.is_set():
            raise ExecutionDetached("Slurm 作业继续运行，服务重启后恢复监控")

    @staticmethod
    def _interrupt(state: dict[str, Any], on_job: Callable[[dict[str, Any]], None],
                   reason: str, message: str) -> None:
        state.update(reason=reason, monitoring_message=message)
        try:
            on_job(dict(state))
        except Exception as exc:
            # Persistence failure must never downgrade a potentially live job
            # to a completed run. The pre-submission token remains recoverable.
            raise ExecutionDetached(message + f"；监控状态持久化失败：{exc}") from exc
        raise ExecutionDetached(message)

    def _query(self, job_id: str, token: str) -> dict[str, str] | None:
        completed = self._command(["scontrol", "show", "job", "--oneliner", job_id])
        if completed.returncode == 0:
            fields = dict(re.findall(r"(?:^|\s)([A-Za-z][A-Za-z0-9_]*)=(\S+)", completed.stdout))
            if fields.get("JobId") == job_id:
                if fields.get("JobName") != f"nw-{token}":
                    raise RuntimeError(f"Slurm 作业 {job_id} 的提交标记不匹配，拒绝操作")
                state = fields.get("JobState", "").split("+", 1)[0]
                if state:
                    return {"state": state, "exit_code": fields.get("ExitCode", ""),
                            "node_list": fields.get("NodeList", ""), "reason": fields.get("Reason", ""),
                            "source": "scontrol"}
        # sacct is optional (AccountingStorageType=accounting_storage/none on
        # the initial cluster). Never infer successful completion from absence.
        if shutil.which("sacct"):
            completed = self._command(["sacct", "--noheader", "--parsable2", "--jobs", job_id,
                                       "--format=JobIDRaw,JobName%50,State,ExitCode"])
            if not completed.returncode:
                for line in completed.stdout.splitlines():
                    parts = line.strip().split("|")
                    if len(parts) >= 4 and parts[0] == job_id:
                        if parts[1] != f"nw-{token}":
                            raise RuntimeError("sacct 作业提交标记不匹配")
                        return {"state": parts[2].split()[0].split("+")[0], "exit_code": parts[3], "source": "sacct"}
        return None

    def _monitor(self, context: RunnerContext, state: dict[str, Any],
                 on_job: Callable[[dict[str, Any]], None]) -> RunnerResult:
        directory = Path(state["job_directory"])
        job_id, token = str(state["job_id"]), state["submission_token"]
        terminal_path = directory / "terminal.json"
        missing_since: float | None = None
        cancel_started: float | None = None
        log_offset = 0
        terminal = state.get("terminal")
        if terminal is None and terminal_path.is_file():
            terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
        while True:
            self._raise_if_detached(context)
            log_offset = self._read_events(context, directory, log_offset)
            if terminal is not None:
                if (terminal.get("job_id") != job_id or terminal.get("submission_token") != token
                        or terminal.get("state") not in TERMINAL_STATES
                        or not re.fullmatch(r"\d+:\d+", str(terminal.get("exit_code", "")))):
                    raise RuntimeError("持久化的 Slurm 终态证据无效")
                break
            if context.cancel_event.is_set() and cancel_started is None:
                # Validate ownership immediately before scancel (protect Job ID reuse).
                try:
                    latest = self._query(job_id, token)
                except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
                    self._interrupt(state, on_job, "monitoring_unavailable", f"取消前无法查询作业 {job_id}：{exc}")
                if latest and latest["state"] in TERMINAL_STATES:
                    observed = latest
                else:
                    if latest is None:
                        self._interrupt(state, on_job, "monitoring_unavailable", f"无法确认 Slurm {job_id} 的归属和终态，取消待核查")
                    try:
                        response = self._command(["scancel", job_id])
                    except (OSError, subprocess.TimeoutExpired) as exc:
                        self._interrupt(state, on_job, "cancellation_unconfirmed", f"scancel {job_id} 响应不确定：{exc}")
                    if response.returncode:
                        self._interrupt(state, on_job, "cancellation_unconfirmed", f"scancel {job_id} 失败：{response.stderr.strip()[-500:]}")
                    cancel_started = time.monotonic()
                    state["cancel_requested"] = True
                    on_job(dict(state))
                    observed = None
            else:
                try:
                    observed = self._query(job_id, token)
                except RuntimeError as exc:
                    self._interrupt(state, on_job, "monitoring_unavailable", str(exc))
                except (OSError, subprocess.TimeoutExpired):
                    observed = None
            if observed:
                missing_since = None
                if observed["state"] in TERMINAL_STATES:
                    if not re.fullmatch(r"\d+:\d+", observed.get("exit_code", "")):
                        self._interrupt(state, on_job, "monitoring_unavailable", f"Slurm 作业 {job_id} 终态缺少有效 ExitCode")
                    terminal = {**observed, "job_id": job_id, "submission_token": token, "observed_at": time.time()}
                    atomic_json(terminal_path, terminal)
                    state.update(observed, terminal=terminal)
                    on_job(dict(state))
                    continue
                if any(state.get(key) != value for key, value in observed.items()):
                    state.update(observed)
                    on_job(dict(state))
                    context.log(f"Slurm {job_id}: {observed['state']} {observed.get('reason', '')}", None, "info")
            else:
                missing_since = missing_since or time.monotonic()
                if time.monotonic() - missing_since >= self.missing_timeout:
                    self._interrupt(state, on_job, "monitoring_unavailable",
                                    f"无法确认 Slurm 作业 {job_id} 的终态；作业可能仍在运行或记录已过期，请人工核查，未标记成功")
            if cancel_started is not None and time.monotonic() - cancel_started > self.cancel_timeout:
                self._interrupt(state, on_job, "cancellation_unconfirmed",
                                f"已向 Slurm {job_id} 请求取消，但未确认终态；请管理员核查，未宣告取消完成")
            maximum = self.queue_timeout + float(state.get("options", {}).get("time_minutes", 1440)) * 60 + 300
            if time.time() - float(state.get("submitted_at", time.time())) > maximum:
                self._interrupt(state, on_job, "monitoring_unavailable",
                                f"Slurm 作业 {job_id} 超过监控期限；请核查现有作业，禁止重复提交")
            # Small waits let API shutdown detach promptly, without cancelling the job.
            if context.detach_event is not None:
                context.detach_event.wait(self.poll_seconds)
            else:
                time.sleep(self.poll_seconds)
        self._read_events(context, directory, log_offset)
        if context.cancel_event.is_set() or terminal["state"] == "CANCELLED":
            raise ExecutionCancelled(f"Slurm 作业 {job_id} 已结束：{terminal['state']}")
        if terminal["state"] != "COMPLETED" or terminal["exit_code"] != "0:0":
            detail = self._result_error(directory)
            raise RuntimeError(f"Slurm 作业 {job_id} {terminal['state']}，ExitCode={terminal['exit_code']}" + detail)
        result_path = directory / "result.json"
        if not result_path.is_file():
            raise RuntimeError(f"Slurm 作业 {job_id} 已退出，但缺少原子 Worker 结果，不能确认成功")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if result.get("submission_token") != token or result.get("job_id") != job_id:
            raise RuntimeError("Worker 结果不属于当前 Slurm 作业")
        if result.get("status") != "succeeded" or result.get("exit_code") != 0:
            raise RuntimeError("计算节点 Runner 失败：" + str(result.get("message", "结果格式无效")))
        artifacts = [RunArtifact.model_validate(item) for item in result.get("artifacts", [])]
        validated_artifacts(artifacts, context.output_directory, context.step.id)
        return RunnerResult(artifacts=artifacts, message=str(result.get("message", "Slurm 处理完成")))

    @staticmethod
    def _result_error(directory: Path) -> str:
        try:
            value = json.loads((directory / "result.json").read_text(encoding="utf-8"))
            return "；" + str(value.get("message", ""))[:2000]
        except (OSError, ValueError):
            return ""

    @staticmethod
    def _read_events(context: RunnerContext, directory: Path, offset: int) -> int:
        path = directory / "events.jsonl"
        if not path.is_file():
            return offset
        with path.open("rb") as handle:
            handle.seek(offset)
            for _ in range(200):
                position = handle.tell()
                line = handle.readline(64 * 1024)
                if not line or not line.endswith(b"\n"):
                    return position
                try:
                    event = json.loads(line)
                    context.log(str(event.get("message", ""))[:4000], event.get("progress"),
                                event.get("level") if event.get("level") in {"info", "warning", "error"} else "info")
                except (UnicodeError, ValueError):
                    pass
            return handle.tell()
