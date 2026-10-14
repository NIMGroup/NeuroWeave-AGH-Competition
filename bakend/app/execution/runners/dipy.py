from __future__ import annotations

import json
import os
import subprocess
from functools import lru_cache
from pathlib import Path

from ..base import RunnerContext, RunnerResult
from ..external_command import ExternalCommandRunner


@lru_cache(maxsize=8)
def _probe_dipy(python: str) -> tuple[bool, str]:
    executable = Path(python)
    if not executable.is_file() or not os.access(executable, os.X_OK):
        return False, f"未找到 DIPY Python 环境：{executable}"
    code = (
        "import importlib.util,json;"
        "m=['dipy','nibabel','numpy'];"
        "x=[v for v in m if importlib.util.find_spec(v) is None];"
        "print(json.dumps(x))"
    )
    try:
        completed = subprocess.run(  # noqa: S603 - fixed interpreter and dependency probe
            [python, "-c", code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            timeout=20,
            check=False,
        )
        missing = json.loads(completed.stdout.strip() or "[]")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return False, f"无法探测 DIPY 环境：{exc}"
    if completed.returncode != 0:
        return False, "DIPY 环境探测失败：" + (completed.stderr.strip()[-300:] or str(completed.returncode))
    if missing:
        return False, "Scientific Python 缺少模块：" + ", ".join(missing)
    return True, "NeuroClaw 兼容的 DIPY Python 环境可用"


class DipyTensorRunner(ExternalCommandRunner):
    runner_id = "dipy.tensor_fit"
    label = "DIPY TensorModel"

    def __init__(self) -> None:
        self.executable = Path(
            os.getenv("LOCALFLOW_DIPY_PYTHON", os.getenv("LOCALFLOW_SKILL_PYTHON", "/opt/software/fsl/bin/python"))
        )
        self.worker = Path(__file__).resolve().parents[1] / "dipy_worker.py"

    def runtime_status(self) -> dict[str, object]:
        available, reason = _probe_dipy(str(self.executable))
        if available and not self.worker.is_file():
            available, reason = False, f"DIPY Worker 不存在：{self.worker}"
        return {
            "available": available,
            "reason": reason,
            "executable": str(self.executable),
            "environment_profile": "dipy-python",
            "environment_source": "NeuroClaw-compatible profile",
            "required_modules": ["dipy", "nibabel", "numpy"],
        }

    def preflight(self, context: RunnerContext) -> list[str]:
        problems = self.common_preflight(context)
        for slot_id in ("dwi", "bvals", "bvecs"):
            if context.step.input_bindings.get(slot_id) is None:
                problems.append(f"缺少必需输入：{slot_id}")
        if self.parameter(context, "mask_strategy", "existing") == "existing":
            if context.step.input_bindings.get("mask") is None:
                problems.append("选择已有 Mask 时必须绑定 mask")
        return problems

    def run(self, context: RunnerContext) -> RunnerResult:
        metrics_dir = context.output_directory / "metrics"
        argv = [
            str(self.executable), str(self.worker),
            "--dwi", str(self.required_binding(context, "dwi")),
            "--bvals", str(self.required_binding(context, "bvals")),
            "--bvecs", str(self.required_binding(context, "bvecs")),
            "--mask-strategy", str(self.parameter(context, "mask_strategy", "existing")),
            "--fit-method", str(self.parameter(context, "fit_method", "WLS")),
            "--b0-threshold", str(float(self.parameter(context, "b0_threshold", 50))),
            "--min-signal", str(float(self.parameter(context, "min_signal", 1))),
            "--restore-sigma", str(float(self.parameter(context, "restore_sigma", 20))),
            "--output-dir", str(metrics_dir),
        ]
        mask = self.optional_binding(context, "mask")
        if mask is not None:
            argv.extend(("--mask", str(mask)))
        if bool(self.parameter(context, "clip_negative_eigenvalues", True)):
            argv.append("--clip-negative-eigenvalues")
        if bool(self.parameter(context, "save_eigenvectors", False)):
            argv.append("--save-eigenvectors")
        log_path = context.output_directory / "dipy-tensor.log"
        self.execute(
            context,
            argv,
            environment={"PYTHONNOUSERSITE": "1", "MPLBACKEND": "Agg"},
            log_path=log_path,
        )
        artifacts = [
            self.artifact(
                context,
                metrics_dir / f"dti_{metric}.nii.gz",
                "diffusion_metrics",
                f"DIPY {metric}",
                "application/gzip",
                {"metric": metric},
            )
            for metric in ("FA", "MD", "AD", "RD")
        ]
        eigenvectors = metrics_dir / "dti_eigenvectors.nii.gz"
        if eigenvectors.is_file() and eigenvectors.stat().st_size:
            artifacts.append(
                self.artifact(context, eigenvectors, "diffusion_metrics", "DIPY 特征向量", "application/gzip")
            )
        artifacts.append(
            self.artifact(context, log_path, "execution_log", "DIPY 执行日志", "text/plain")
        )
        context.log("DIPY 张量指标和日志已通过完整性检查", 100.0, "info")
        return RunnerResult(artifacts=artifacts, message="DIPY 张量拟合完成。")
