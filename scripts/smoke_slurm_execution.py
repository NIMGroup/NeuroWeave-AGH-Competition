#!/usr/bin/env python3
"""Submit four isolated, real Slurm integration checks when explicitly invoked.

Example (after cluster maintenance has finished):
  /opt/neuroweave/envs/scientific-py311/bin/python \
    scripts/smoke_slurm_execution.py --root /home/data/neuroweave/smoke/20260907T120000Z

The process inherits the non-secret LOCALFLOW_SLURM_* deployment configuration.
It never loads .env files, changes Slurm configuration, or opens the production DB.
Evidence, synthetic inputs and the isolated SQLite DB are retained for inspection.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import signal
import subprocess
import sys
import time
from concurrent.futures import wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SMOKE_BASE = Path("/home/data/neuroweave/smoke")
FINISHED = {"succeeded", "failed", "cancelled"}
CASE_TIMEOUT = 90


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_evidence(path: Path, evidence: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(evidence, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def checked_root(value: Path) -> Path:
    resolved = value.resolve(strict=False)
    base = SMOKE_BASE.resolve(strict=False)
    if resolved.parent != base or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", resolved.name):
        raise ValueError(f"--root 必须是 {SMOKE_BASE} 下全新的单层测试目录")
    if resolved.exists():
        raise ValueError("--root 已存在；请使用新的时间戳目录，不覆盖之前的证据")
    return resolved


class SmokeSuite:
    def __init__(self, root: Path, partition: str) -> None:
        # Imports happen only on explicit execution, never during --help or lint.
        sys.path.insert(0, str(PROJECT_ROOT / "bakend"))
        from app.capability_registry import CapabilityRegistry
        from app.execution.runner_registry import RunnerRegistry
        from app.execution.slurm_backend import SlurmBackend
        from app.local_store import LocalStore
        from app.run_models import RunExecutionOptions

        self.root = root
        self.partition = partition
        self.capabilities = CapabilityRegistry(PROJECT_ROOT / "skills")
        self.registry = RunnerRegistry()
        self.store = LocalStore(root / "smoke.sqlite3")
        self.options = RunExecutionOptions(backend="slurm", partition=partition, cpus=1,
                                           memory_gb=1, gpus=0, time_minutes=2)
        self.original_command = SlurmBackend._command
        self.hold_submissions = False
        self.submitted_tokens: set[str] = set()
        self.jobs: dict[str, str] = {}
        self.submissions: list[dict[str, Any]] = []
        self.services: list[Any] = []
        self.service: Any = None
        self.current_case: dict[str, Any] | None = None
        self.sample_nodes: list[str] = []
        self.sample_concurrency = 1
        self.evidence: dict[str, Any] = {
            "schema_version": 1, "started_at": now(), "root": str(root),
            "database": str(self.store.database_path), "partition": partition,
            "case_timeout_seconds": CASE_TIMEOUT, "cases": [], "cleanup": [],
            "scope": "Synthetic dataset.inspect jobs; production data and database are not used.",
        }

    def command(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        """Only the test adapter adds hold; it does not replace actual Slurm I/O."""
        arguments = list(argv)
        if arguments and arguments[0] == "sbatch":
            name = next((item.split("=", 1)[1] for item in arguments if item.startswith("--job-name=")), "")
            if not re.fullmatch(r"nw-[a-f0-9]{32}", name):
                raise RuntimeError("烟测拒绝没有唯一提交标记的 sbatch")
            working = next((item.split("=", 1)[1] for item in arguments if item.startswith("--chdir=")), "")
            Path(working).resolve(strict=True).relative_to((self.root / "runs").resolve(strict=True))
            token = name.removeprefix("nw-")
            self.submitted_tokens.add(token)
            if self.hold_submissions:
                arguments.insert(1, "--hold")
            submitted = {"token": token, "held": self.hold_submissions, "at": now(), "job_id": None}
            self.submissions.append(submitted)
            result = self.original_command(arguments)
            candidate = result.stdout.strip().split(";", 1)[0]
            if result.returncode == 0 and re.fullmatch(r"[1-9][0-9]{0,11}", candidate):
                self.jobs[candidate] = token
                submitted["job_id"] = candidate
            return result
        if arguments and arguments[0] == "scancel":
            if len(arguments) != 2 or arguments[1] not in self.jobs:
                raise RuntimeError("烟测拒绝取消不属于本次测试的作业")
        return self.original_command(arguments)

    def new_service(self) -> Any:
        from app.execution.run_service import RunService
        service = RunService(self.store, self.capabilities, self.registry, self.root / "local-unused")
        self.services.append(service)
        self.service = service
        return service

    def fixture(self, name: str, *, missing: bool = False) -> Any:
        from app.dataset_models import DatasetInspection, DatasetRecord
        from app.workflow_models import DryRunReport, MethodOption, Workflow, WorkflowStep

        stamp = now()
        dataset_path = self.root / (("missing-dataset-" if missing else "dataset-") + name)
        if not missing and not dataset_path.exists():
            dataset_path.mkdir(mode=0o700)
            (dataset_path / "dataset_description.json").write_text(
                '{"Name":"NeuroWeave Slurm synthetic smoke","BIDSVersion":"1.9.0"}\n', encoding="utf-8")
            (dataset_path / "participants.tsv").write_text("participant_id\tage\nsub-test\t30\n", encoding="utf-8")
        if missing and dataset_path.exists():
            raise RuntimeError("故意缺失的数据集目录意外存在")
        dataset = DatasetRecord(
            dataset_id=f"smoke_dataset_{name}", root_id="slurm_smoke", relative_path=dataset_path.name,
            display_name=f"Synthetic smoke: {name}", absolute_path=str(dataset_path),
            inspection=DatasetInspection(file_count=2, total_bytes=0, is_bids=True,
                                         sample_layout="bids", sample_count=1, scanned_at=stamp),
            created_at=stamp, updated_at=stamp)
        self.store.save_dataset(dataset)
        definition = next(item for item in self.capabilities.methods_for("dwi.inspect")
                          if item.runner_id == "dataset.inspect")
        method = MethodOption(
            id=definition.method_id, skill_id=definition.skill_id, label=definition.label,
            description=definition.description, runner_id=definition.runner_id,
            execution_mode=definition.execution_mode, input_types=definition.input_types,
            output_types=definition.output_types, parameters=definition.parameters,
            input_slots=definition.input_slots, output_slots=definition.output_slots,
            structure_level=definition.structure_level)
        parameters = {item.id: item.default for item in method.parameters if item.default is not None}
        workflow = Workflow(
            workflow_id=f"smoke_workflow_{name}", template_id="dynamic:dwi.inspect", revision=1,
            title=f"Slurm smoke: {name}", summary="Synthetic dataset inspection integration check",
            original_request="Inspect the isolated synthetic smoke dataset", status="confirmed",
            dataset_id=dataset.dataset_id, dataset=dataset, planner_source="integration-smoke",
            execution_mode="runner", plan_kind="atomic", requested_capabilities=["dwi.inspect"],
            steps=[WorkflowStep(id="dwi_inspect", order=1, title="Synthetic dataset inspection",
                                description="Only inspect smoke fixture files", selection="auto", status="confirmed",
                                capability="dwi.inspect", method_options=[method], selected_method_id=method.id,
                                selected_parameters=parameters)],
            dry_run_report=DryRunReport(valid=True, execution_enabled=True, dataset_path_validated=True, checked_at=stamp),
            confirmed_at=stamp, created_at=stamp, updated_at=stamp)
        self.store.save_workflow(workflow, event_type="workflow_confirmed")
        return workflow

    def begin(self, name: str, *, missing: bool = False, hold: bool = False) -> Any:
        self.hold_submissions = hold
        workflow = self.fixture(name, missing=missing)
        run = self.service.create(workflow.workflow_id, workflow.revision, self.options)
        assert self.current_case is not None
        self.current_case["run_id"] = run.run_id
        return run

    def poll(self, run_id: str, deadline: float, predicate: Callable[[Any], bool]) -> Any:
        while time.monotonic() < deadline:
            run = self.service.get(run_id)
            if predicate(run):
                return run
            if run.status in FINISHED:
                raise RuntimeError(f"任务提前结束：{run.status}: {run.error_message}")
            time.sleep(0.25)
        raise TimeoutError(f"case 超过 {CASE_TIMEOUT} 秒：{run_id}")

    def terminal(self, run_id: str, deadline: float, expected: str) -> Any:
        run = self.poll(run_id, deadline, lambda current: current.status in FINISHED)
        if run.status != expected:
            raise AssertionError(f"期望 {expected}，实际 {run.status}: {run.error_message}")
        terminal = run.steps[0].scheduler.get("terminal", {})
        if not terminal.get("state") or not terminal.get("exit_code"):
            raise AssertionError("缺少已确认的 Slurm 终态和 ExitCode")
        return run

    def pending(self, run_id: str, deadline: float) -> tuple[Any, str]:
        run = self.poll(run_id, deadline, lambda current: bool(current.steps[0].scheduler.get("job_id")))
        job_id = str(run.steps[0].scheduler["job_id"])
        while time.monotonic() < deadline:
            record = self.owned_job(job_id)
            if record and record["state"] == "PENDING":
                assert self.current_case is not None
                self.current_case["held_pending"] = record
                return run, job_id
            time.sleep(0.25)
        raise TimeoutError("没有观察到本次 --hold 作业的 PENDING 状态")

    def owned_job(self, job_id: str) -> dict[str, str] | None:
        if job_id not in self.jobs or not re.fullmatch(r"[1-9][0-9]{0,11}", job_id):
            raise RuntimeError("作业不属于本次烟测")
        # _query verifies both the numerical ID and the exact nw-token JobName.
        return self.service.slurm_backend._query(job_id, self.jobs[job_id])

    @staticmethod
    def verified_success(run: Any) -> None:
        if run.steps[0].scheduler["terminal"]["state"] != "COMPLETED":
            raise AssertionError("成功任务的调度终态不是 COMPLETED")
        if run.steps[0].scheduler["terminal"]["exit_code"] != "0:0":
            raise AssertionError("成功任务的 ExitCode 不是 0:0")
        if len(run.artifacts) != 1:
            raise AssertionError("dataset.inspect 应输出一个巡检报告")
        artifact = run.artifacts[0]
        report = json.loads(Path(artifact.absolute_path).read_text(encoding="utf-8"))
        if report.get("runner_id") != "dataset.inspect" or report.get("file_count") != 2:
            raise AssertionError("真实 Worker 未输出预期合成数据报告")
        if not artifact.sha256:
            raise AssertionError("巡检报告缺少校验和")

    def success(self, deadline: float) -> None:
        run = self.begin("success")
        self.verified_success(self.terminal(run.run_id, deadline, "succeeded"))

    def parallel_samples(self, deadline: float) -> None:
        from app.dataset_models import DatasetSample
        from app.run_models import NodeAllocation, RunExecutionOptions
        workflow = self.fixture("parallel_samples")
        root = Path(workflow.dataset.absolute_path)
        samples = []
        for index in range(len(self.sample_nodes) * 2):
            name = f"sample-{index + 1:02d}"
            directory = root / name
            directory.mkdir(mode=0o700)
            (directory / "synthetic.txt").write_text(name, encoding="utf-8")
            samples.append(DatasetSample(sample_id=name, label=name, relative_root=name))
        workflow.dataset.inspection.samples = samples
        workflow.dataset.inspection.sample_count = len(samples)
        self.store.save_workflow(workflow, event_type="workflow_confirmed")
        options = RunExecutionOptions(
            backend="slurm", partition=self.partition, sample_mode="per_sample",
            nodes=[NodeAllocation(node=node, cpus=index + 1, memory_gb=1, time_minutes=2,
                                  parallel_samples=self.sample_concurrency)
                   for index, node in enumerate(self.sample_nodes)],
        )
        run = self.service.create(workflow.workflow_id, workflow.revision, options)
        self.current_case["run_id"] = run.run_id
        run = self.terminal(run.run_id, deadline, "succeeded")
        assert all(node.parallel_samples == self.sample_concurrency for node in run.execution.nodes)
        self.current_case["parallel_samples_per_node"] = self.sample_concurrency
        assert len(run.artifacts) == len(samples)
        assert len({artifact.absolute_path for artifact in run.artifacts}) == len(samples)
        verified = []
        for index, step in enumerate(run.steps):
            requested = options.nodes[index % len(options.nodes)]
            terminal = step.scheduler["terminal"]
            assert terminal["state"] == "COMPLETED" and terminal["exit_code"] == "0:0"
            assert terminal["node_list"] == requested.node
            result = self.original_command(["scontrol", "show", "job", str(step.scheduler["job_id"]), "--oneliner"])
            match = re.search(r"(?:^|\s)NumCPUs=(\d+)(?:\s|$)", result.stdout)
            assert result.returncode == 0 and match and int(match.group(1)) == requested.cpus
            report = json.loads(Path(step.artifacts[0].absolute_path).read_text())
            assert report["file_count"] == 1
            verified.append({"sample": step.sample_id, "node": requested.node, "cpus": requested.cpus,
                             "job_id": step.scheduler["job_id"]})
        self.current_case["sample_allocations"] = verified

    def missing_dataset(self, deadline: float) -> None:
        run = self.begin("missing_dataset", missing=True)
        run = self.terminal(run.run_id, deadline, "failed")
        directory = Path(run.steps[0].scheduler["job_directory"])
        result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
        if result.get("status") != "failed" or "输入" not in result.get("message", ""):
            raise AssertionError("缺少数据的失败必须来自计算节点输入预检")
        if run.artifacts:
            raise AssertionError("预检失败不应登记结果产物")
        assert self.current_case is not None
        self.current_case["worker_failure"] = result["message"]

    def cancel_pending(self, deadline: float) -> None:
        run = self.begin("cancel_pending", hold=True)
        _, job_id = self.pending(run.run_id, deadline)
        self.service.cancel(run.run_id)
        finished = self.terminal(run.run_id, deadline, "cancelled")
        if str(finished.steps[0].scheduler["job_id"]) != job_id:
            raise AssertionError("取消过程中 Job ID 发生变化")

    def restart_resume(self, deadline: float) -> None:
        run = self.begin("restart_resume", hold=True)
        _, job_id = self.pending(run.run_id, deadline)
        before = len(self.submissions)
        events = self.service.events(run.run_id)
        last_event = max(item["event_id"] for item in events)
        old_service = self.service
        old_futures = list(old_service._futures.values())
        old_service.shutdown()
        _, pending_futures = wait(old_futures, timeout=max(0.0, deadline - time.monotonic()))
        if pending_futures:
            raise TimeoutError("旧 RunService 未在期限内停止监控")
        held = self.owned_job(job_id)
        if held is None or held["state"] != "PENDING":
            raise AssertionError("shutdown 不应取消或释放 held Slurm 作业")
        self.new_service()  # Exercises actual constructor recovery against the same isolated SQLite DB.
        recovered = self.poll(run.run_id, deadline, lambda current: any(
            event["event_id"] > last_event and event["event_type"] == "step_started"
            for event in self.service.events(current.run_id)))
        if str(recovered.steps[0].scheduler.get("job_id")) != job_id or len(self.submissions) != before:
            raise AssertionError("恢复必须使用原 Job ID，且不得再次 sbatch")
        if self.owned_job(job_id).get("state") != "PENDING":
            raise AssertionError("释放前原作业必须仍然 PENDING")
        release = self.original_command(["scontrol", "release", job_id])
        if release.returncode:
            raise RuntimeError("无法释放本次 held 作业：" + release.stderr.strip()[-500:])
        finished = self.terminal(run.run_id, deadline, "succeeded")
        self.verified_success(finished)
        if str(finished.steps[0].scheduler["job_id"]) != job_id or len(self.submissions) != before:
            raise AssertionError("恢复后完成的作业 ID 或提交次数不一致")
        assert self.current_case is not None
        self.current_case.update(job_id_before_restart=job_id, job_id_after_restart=job_id,
                                 additional_submissions_after_restart=len(self.submissions) - before)

    def cleanup_jobs(self) -> None:
        from app.execution.slurm_backend import TERMINAL_STATES

        # Recover IDs even if sbatch accepted a job but its response was lost.
        try:
            queue = self.original_command(["squeue", "--noheader", "--user", getpass.getuser(), "--format=%i|%j"])
            if queue.returncode == 0:
                for line in queue.stdout.splitlines():
                    parts = line.strip().split("|")
                    if (len(parts) == 2 and re.fullmatch(r"[1-9][0-9]{0,11}", parts[0])
                            and parts[1].removeprefix("nw-") in self.submitted_tokens
                            and parts[1].startswith("nw-")):
                        self.jobs[parts[0]] = parts[1][3:]
        except Exception as exc:
            self.evidence["cleanup"].append({"error": f"本次提交标记查询失败：{exc}"})
        for job_id in list(self.jobs):
            record: dict[str, Any] = {"job_id": job_id, "token": self.jobs[job_id], "checked_at": now()}
            try:
                observed = self.owned_job(job_id)
                if observed is None:
                    saved_terminal = next((step.scheduler.get("terminal", {})
                                           for run in self.store.list_runs()
                                           for step in run.steps
                                           if str(step.scheduler.get("job_id")) == job_id
                                           and step.scheduler.get("terminal", {}).get("submission_token") == self.jobs[job_id]
                                           and step.scheduler.get("terminal", {}).get("state") in TERMINAL_STATES), None)
                    if saved_terminal:
                        record.update(state=saved_terminal["state"], action="persisted_terminal_after_record_purge")
                    else:
                        record.update(state="unknown", action="未取消：无法确认 JobName 归属；保留 ID 供管理员核查")
                elif observed["state"] in TERMINAL_STATES:
                    record.update(state=observed["state"], action="already_terminal")
                else:
                    cancelled = self.original_command(["scancel", job_id])
                    if cancelled.returncode:
                        raise RuntimeError(cancelled.stderr.strip()[-500:] or "scancel failed")
                    deadline = time.monotonic() + 30
                    while time.monotonic() < deadline:
                        observed = self.owned_job(job_id)
                        if observed and observed["state"] in TERMINAL_STATES:
                            break
                        time.sleep(0.5)
                    record.update(state=observed["state"] if observed else "unknown", action="scancel_requested",
                                  terminal_confirmed=bool(observed and observed["state"] in TERMINAL_STATES))
            except Exception as exc:
                record["error"] = str(exc)
            self.evidence["cleanup"].append(record)

    def run(self) -> int:
        from app.execution.slurm_backend import SlurmBackend

        failed = False
        with patch.object(SlurmBackend, "_command", staticmethod(self.command)):
            try:
                self.new_service()
                status = self.service.slurm_backend.status()
                self.evidence["backend"] = status
                if not status.get("available") or self.partition not in status.get("partitions", []):
                    raise RuntimeError("Slurm 尚未就绪：" + str(status.get("reason")))
                cases = ([("parallel_samples", self.parallel_samples)] if self.sample_nodes else
                         [("success", self.success), ("missing_dataset", self.missing_dataset),
                          ("cancel_pending", self.cancel_pending), ("restart_resume", self.restart_resume)])
                for name, case in cases:
                    started = time.monotonic()
                    self.current_case = {"case": name, "started_at": now()}
                    self.evidence["cases"].append(self.current_case)
                    try:
                        case(started + CASE_TIMEOUT)
                        self.current_case["passed"] = True
                    except Exception as exc:
                        failed = True
                        self.current_case.update(passed=False, error=f"{type(exc).__name__}: {exc}")
                        self.cleanup_jobs()
                    finally:
                        self.current_case.setdefault("passed", False)
                        self.current_case["elapsed_seconds"] = round(time.monotonic() - started, 3)
                        run_id = self.current_case.get("run_id")
                        if run_id:
                            snapshot = self.store.get_run(run_id)
                            if snapshot:
                                self.current_case.update(state=snapshot.status,
                                                         job_ids=[str(s.scheduler["job_id"]) for s in snapshot.steps if s.scheduler.get("job_id")],
                                                         scheduler=[s.scheduler for s in snapshot.steps],
                                                         artifacts=[a.model_dump(mode="json") for a in snapshot.artifacts])
                        self.evidence["submissions"] = list(self.submissions)
                        atomic_evidence(self.root / "evidence.json", self.evidence)
                    print(json.dumps({"case": name, "passed": self.current_case["passed"],
                                      "run_id": self.current_case.get("run_id"),
                                      "job_ids": self.current_case.get("job_ids", []),
                                      "error": self.current_case.get("error")}, ensure_ascii=False), flush=True)
            except (Exception, KeyboardInterrupt) as exc:
                failed = True
                self.evidence["suite_error"] = f"{type(exc).__name__}: {exc}"
            finally:
                for service in self.services:
                    service.shutdown()
                if self.service is not None:
                    self.cleanup_jobs()
                if any(item.get("error") or item.get("state") == "unknown"
                       or item.get("terminal_confirmed") is False for item in self.evidence["cleanup"]):
                    failed = True
                self.evidence.update(finished_at=now(), passed=not failed, submissions=list(self.submissions))
                atomic_evidence(self.root / "evidence.json", self.evidence)
        return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    default = SMOKE_BASE / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8])
    parser.add_argument("--root", type=Path, default=default, help="全新共享测试目录，仅允许 /home/data/neuroweave/smoke 的单层子目录")
    parser.add_argument("--partition", default="all", help="已配置的 CPU Slurm 分区（默认 all）")
    parser.add_argument("--sample-nodes", nargs="+", default=[],
                        help="仅验证多样本分配到指定节点，例如 node01 node02；各节点申请 1、2… 核")
    parser.add_argument("--sample-concurrency", type=int, choices=(1, 2), default=1,
                        help="每节点并行数量；本合成验证每节点分配两个样本（默认 1）")
    args = parser.parse_args()
    if os.name != "posix":
        parser.error("真实 Slurm 烟测只能在 Linux 提交节点执行")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.partition):
        parser.error("分区名格式无效")
    if (len(args.sample_nodes) > 16 or len(set(args.sample_nodes)) != len(args.sample_nodes)
            or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", node) for node in args.sample_nodes)):
        parser.error("节点名无效、重复或过多")
    if os.getenv("LOCALFLOW_SLURM_ENABLED", "").lower() not in {"1", "true", "yes"}:
        parser.error("请从外部提供 LOCALFLOW_SLURM_ENABLED=true 和共享 Worker/Python 路径；脚本不会读取 .env")
    try:
        root = checked_root(args.root)
    except ValueError as exc:
        parser.error(str(exc))
    root.parent.mkdir(parents=True, exist_ok=True)
    root.mkdir(mode=0o700)
    (root / "runs").mkdir(mode=0o700)
    settings = {"LOCALFLOW_SLURM_OUTPUT_ROOT": str(root / "runs"),
                "LOCALFLOW_RUN_INPUT_ROOTS": str(root), "LOCALFLOW_RUN_WORKERS": "1",
                "LOCALFLOW_SLURM_POLL_SECONDS": "0.5"}
    # These overrides affect this isolated process only. No file is edited.
    with patch.dict(os.environ, settings):
        suite = SmokeSuite(root, args.partition)
        suite.sample_nodes = args.sample_nodes
        suite.sample_concurrency = args.sample_concurrency
        def stop_requested(*_: Any) -> None:
            raise KeyboardInterrupt("收到停止信号；正在清理本次烟测作业")

        previous = signal.signal(signal.SIGTERM, stop_requested)
        try:
            result = suite.run()
        finally:
            signal.signal(signal.SIGTERM, previous)
    print(json.dumps({"passed": result == 0, "evidence": str(root / "evidence.json")}, ensure_ascii=False), flush=True)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
