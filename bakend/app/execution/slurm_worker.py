"""Compute-node entry point. Only registered, reviewed runners can be executed."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import sys
import time
from pathlib import Path
from threading import Event
from typing import Any


# Explicit names, never a LOCALFLOW_* wildcard: API keys and service settings
# must not be copied from the API process into shared job files or child tools.
RUNTIME_ENV_ALLOWLIST = frozenset({
    "WORKSPACE_ALLOWED_ROOTS", "WORKSPACE_DEFAULT_ROOT",
    "LOCALFLOW_RUN_INPUT_ROOTS", "LOCALFLOW_SKILL_PYTHON", "LOCALFLOW_DIPY_PYTHON", "LOCALFLOW_TORCH_PYTHON",
    "LOCALFLOW_FSL_ROOT", "LOCALFLOW_MRTRIX_ROOT", "LOCALFLOW_MRTRIX_LIBPNG",
    "LOCALFLOW_ANTS_ROOT", "LOCALFLOW_DCM2NIIX", "LOCALFLOW_FMRIPREP_EXECUTABLE",
    "LOCALFLOW_QSIPREP_EXECUTABLE", "LOCALFLOW_NII2DCM_EXECUTABLE",
    "LOCALFLOW_FREESURFER_HOME", "LOCALFLOW_FREESURFER_LICENSE",
    "LOCALFLOW_FREESURFER_BC", "LOCALFLOW_FREESURFER_LIBGOMP",
    "LOCALFLOW_FREESURFER_LIBQUADMATH",
})


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def validated_artifacts(artifacts: list[Any], output: Path, step_id: str) -> None:
    if not artifacts:
        raise RuntimeError("Runner 未返回任何已验证产物")
    for artifact in artifacts:
        path = Path(artifact.absolute_path).resolve(strict=True)
        path.relative_to(output.resolve(strict=True))
        if artifact.step_id != step_id:
            raise RuntimeError("产物不属于当前步骤")
        if path.is_dir():
            if not any(item.is_file() and item.stat().st_size for item in path.rglob("*")):
                raise RuntimeError(f"输出目录为空：{path}")
        elif not path.is_file() or not path.stat().st_size:
            raise RuntimeError(f"输出文件不存在或为空：{path}")
        elif artifact.sha256:
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
            if digest.hexdigest() != artifact.sha256:
                raise RuntimeError(f"产物 SHA256 不一致：{path}")


def run_payload(payload_path: Path) -> int:
    payload_path = payload_path.resolve(strict=True)
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    token = str(payload.get("submission_token", ""))
    if payload.get("schema_version") != 1 or not re.fullmatch(r"[a-f0-9]{32}", token):
        raise RuntimeError("Slurm payload 格式或提交标记无效")
    output = Path(payload["output_directory"]).resolve(strict=True)
    job_directory = output / ".slurm" / token
    if payload_path != job_directory / "payload.json":
        raise RuntimeError("Slurm payload 不在本次任务目录")
    job_id = os.getenv("SLURM_JOB_ID", "")
    if not re.fullmatch(r"[1-9][0-9]{0,11}", job_id):
        raise RuntimeError("Worker 必须在有效的 Slurm allocation 中运行")

    runtime = payload.get("runtime_environment", {})
    if not isinstance(runtime, dict) or set(runtime) - RUNTIME_ENV_ALLOWLIST:
        raise RuntimeError("payload 包含未经允许的环境变量")
    if any(not isinstance(value, str) or "\x00" in value for value in runtime.values()):
        raise RuntimeError("运行环境变量格式无效")
    # sbatch --export=NONE may load a login environment on older Slurm releases.
    # Clear that environment before importing runners or starting subprocesses.
    preserved = {key: value for key, value in os.environ.items()
                 if key.startswith("SLURM_") or key in {
                     "CUDA_VISIBLE_DEVICES", "GPU_DEVICE_ORDINAL", "ROCR_VISIBLE_DEVICES",
                     "HIP_VISIBLE_DEVICES", "NVIDIA_VISIBLE_DEVICES",
                 }}
    os.environ.clear()
    os.environ.update(preserved)
    os.environ.update({"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
                       "PYTHONNOUSERSITE": "1", "MPLBACKEND": "Agg",
                       "HOME": str(job_directory / "home"),
                       "TMPDIR": str(job_directory / "tmp")})
    os.environ.update(runtime)
    if int(payload['options'].get('gpus', 0)) == 0:
        # CPU jobs on GPU hosts must not let torch's auto mode use an unrequested GPU.
        for name in ('CUDA_VISIBLE_DEVICES', 'GPU_DEVICE_ORDINAL', 'ROCR_VISIBLE_DEVICES', 'HIP_VISIBLE_DEVICES'):
            os.environ[name] = ''
        os.environ['NVIDIA_VISIBLE_DEVICES'] = 'void'
    if os.name == "nt":
        # Enables isolated worker regression tests on Windows development hosts.
        os.environ["USERPROFILE"] = os.environ["HOME"]
    for name in ("HOME", "TMPDIR"):
        Path(os.environ[name]).mkdir(mode=0o700, exist_ok=True)
    cpus = str(payload["options"]["cpus"])
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                 "NUMEXPR_NUM_THREADS", "ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS"):
        os.environ[name] = cpus

    # Direct script execution pins all imports to the deployed worker revision.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app.execution.base import ExecutionCancelled, RunnerContext
    from app.execution.runner_registry import RunnerRegistry
    from app.workflow_models import MethodOption, Workflow, WorkflowStep

    result_path = job_directory / "result.json"
    events_path = job_directory / "events.jsonl"
    cancelled = Event()
    previous_handlers: dict[int, Any] = {}
    for signum in (signal.SIGTERM, signal.SIGINT):
        previous_handlers[signum] = signal.signal(signum, lambda *_: cancelled.set())

    def log(message: str, progress: float | None, level: str) -> None:
        event = {"message": str(message)[:4000], "progress": progress,
                 "level": level if level in {"info", "warning", "error"} else "info",
                 "time": time.time()}
        with events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        print(str(message), flush=True)

    common = {"schema_version": 1, "submission_token": token, "job_id": job_id}
    try:
        workflow = Workflow.model_validate(payload["workflow"])
        if workflow.workspace_root_path:
            from app.workspace_paths import WorkspacePaths
            policy = WorkspacePaths(Path(workflow.workspace_root_path))
            allowed_output = policy.output_root(workflow.workspace_root_path, workflow.output_relative_path)
            policy.resolve(allowed_output, str(output), write=True)
        step = WorkflowStep.model_validate(payload["step"])
        method = MethodOption.model_validate(payload["method"])
        if method.execution_mode != "runner":
            raise RuntimeError("规划型方法没有执行权限")
        runner = RunnerRegistry().get(method.runner_id)
        if runner is None:
            raise RuntimeError(f"Runner 未在可信白名单中注册：{method.runner_id}")
        context = RunnerContext(run_id=payload["run_id"], workflow=workflow, step=step,
                                method=method, output_directory=output,
                                cancel_event=cancelled, log=log)
        # Check ALL data bindings on the node, including internal Python runners.
        from app.execution.external_command import ExternalCommandRunner
        from app.historical_inputs import historical_binding_problems
        inputs = []
        for binding in step.input_bindings.values():
            history_problems = historical_binding_problems(binding, verify_hash=True)
            if history_problems:
                raise RuntimeError("历史输入复核失败：" + "；".join(history_problems))
            for path in ExternalCommandRunner.binding_paths(binding):
                if not path.exists():
                    raise RuntimeError(f"计算节点无法访问输入：{path}")
                if not binding.history:
                    inputs.append(path)
        if workflow.dataset:
            inputs.append(Path(workflow.dataset.absolute_path))
        for path in inputs:
            if not path.exists():
                raise RuntimeError(f"计算节点无法访问输入：{path}")
            if not ExternalCommandRunner._allowed_input(path, output.parent):
                raise RuntimeError(f"输入超出共享数据白名单：{path}")
        problems = runner.preflight(context)
        if problems:
            raise RuntimeError("计算节点预检失败：" + "；".join(problems))
        context.raise_if_cancelled()
        result = runner.run(context)
        context.raise_if_cancelled()
        validated_artifacts(result.artifacts, output, step.id)
        atomic_json(result_path, {**common, "status": "succeeded", "exit_code": 0,
                                 "message": result.message,
                                 "artifacts": [a.model_dump(mode="json") for a in result.artifacts]})
        return 0
    except ExecutionCancelled as exc:
        log(str(exc), None, "warning")
        atomic_json(result_path, {**common, "status": "cancelled", "exit_code": 130,
                                 "message": str(exc), "artifacts": []})
        return 130
    except Exception as exc:
        log(str(exc), None, "error")
        atomic_json(result_path, {**common, "status": "failed", "exit_code": 1,
                                 "message": str(exc), "artifacts": []})
        return 1
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        return run_payload(arguments.payload)
    except Exception as exc:
        print(f"Slurm worker initialization failed: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
