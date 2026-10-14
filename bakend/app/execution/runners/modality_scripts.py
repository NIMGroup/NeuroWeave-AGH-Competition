from __future__ import annotations

import json
import os
import subprocess
from functools import lru_cache
from pathlib import Path

from ..base import RunnerContext, RunnerResult
from ..external_command import ExternalCommandRunner


def _scientific_python() -> Path:
    return Path(os.getenv("LOCALFLOW_SKILL_PYTHON", "/opt/software/fsl/bin/python"))


@lru_cache(maxsize=8)
def _probe_scientific_python(python: str) -> tuple[bool, str]:
    executable = Path(python)
    if not executable.is_file() or not os.access(executable, os.X_OK):
        return False, f"未找到 Scientific Python：{executable}"
    probe = (
        "import importlib.util,json;"
        "m=['numpy','nibabel'];"
        "print(json.dumps([x for x in m if importlib.util.find_spec(x) is None]))"
    )
    try:
        completed = subprocess.run(  # noqa: S603 - fixed interpreter and dependency probe
            [python, "-c", probe], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, errors="replace", timeout=20, check=False,
        )
        missing = json.loads(completed.stdout.strip() or "[]")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return False, f"无法探测 Scientific Python：{exc}"
    if completed.returncode != 0:
        return False, "Scientific Python 探测失败：" + (completed.stderr.strip()[-300:] or str(completed.returncode))
    if missing:
        return False, "Scientific Python 缺少模块：" + ", ".join(missing)
    return True, "NeuroClaw 兼容的 Scientific Python 环境可用"


class _ReviewedModalityScriptRunner(ExternalCommandRunner):
    script_relative = ""

    def __init__(self) -> None:
        self.executable = _scientific_python()
        self.project_root = Path(__file__).resolve().parents[4]
        self.script = self.project_root / self.script_relative

    def runtime_status(self) -> dict[str, object]:
        available, reason = _probe_scientific_python(str(self.executable))
        if available and not self.script.is_file():
            available, reason = False, f"已审核脚本不存在：{self.script}"
        return {
            "available": available,
            "reason": reason,
            "executable": str(self.executable),
            "script": str(self.script),
            "environment_profile": "scientific-python",
            "environment_source": "NeuroClaw-compatible profile",
            "required_modules": ["numpy", "nibabel"],
        }

    @staticmethod
    def environment() -> dict[str, str]:
        return {"PYTHONNOUSERSITE": "1", "MPLBACKEND": "Agg"}


class AslCbfRunner(_ReviewedModalityScriptRunner):
    runner_id = "asl.compute_cbf"
    label = "ASL CBF 定量"
    script_relative = "skills/asl-skill/scripts/compute_cbf.py"

    def preflight(self, context: RunnerContext) -> list[str]:
        problems = self.common_preflight(context)
        if context.step.input_bindings.get("difference_image") is None:
            problems.append("缺少 ASL difference image")
        if not bool(self.parameter(context, "relative_cbf", False)) and context.step.input_bindings.get("m0_image") is None:
            problems.append("绝对 CBF 定量必须绑定 M0 影像")
        if bool(self.parameter(context, "roi_summary", False)) and context.step.input_bindings.get("roi_atlas") is None:
            problems.append("生成 ROI 汇总时必须绑定 ROI atlas")
        return problems

    def run(self, context: RunnerContext) -> RunnerResult:
        cbf = context.output_directory / "cbf.nii.gz"
        log = context.output_directory / "compute-cbf.log"
        argv = [
            str(self.executable), str(self.script),
            "--diff", str(self.required_binding(context, "difference_image")),
            "--output", str(cbf),
            "--label-strategy", str(self.parameter(context, "label_strategy", "pcasl")),
            "--field-strength", str(float(self.parameter(context, "field_strength", 3.0))),
        ]
        m0 = self.optional_binding(context, "m0_image")
        if bool(self.parameter(context, "relative_cbf", False)):
            argv.append("--no-m0")
        elif m0 is not None:
            argv.extend(("--m0", str(m0)))
        for name, flag in (("alpha", "--alpha"), ("tau", "--tau"), ("pld", "--pld")):
            value = self.parameter(context, name, 0)
            if value and float(value) > 0:
                argv.extend((flag, str(float(value))))
        summary = context.output_directory / "cbf-roi-summary.csv"
        if bool(self.parameter(context, "roi_summary", False)):
            argv.extend(
                (
                    "--roi-atlas", str(self.required_binding(context, "roi_atlas")),
                    "--roi-summary", str(summary),
                )
            )
        self.execute(context, argv, environment=self.environment(), log_path=log)
        artifacts = [
            self.artifact(context, cbf, "generated_file", "CBF 影像", "application/gzip"),
            self.artifact(context, log, "execution_log", "CBF 定量日志", "text/plain"),
        ]
        if summary.is_file() and summary.stat().st_size:
            artifacts.append(self.artifact(context, summary, "generated_file", "CBF ROI 汇总", "text/csv"))
        context.log("CBF 输出与日志已通过完整性检查", 100.0, "info")
        return RunnerResult(artifacts=artifacts, message="ASL CBF 定量完成。")


class PetSuvrRunner(_ReviewedModalityScriptRunner):
    runner_id = "pet.compute_suvr"
    label = "PET SUVR 定量"
    script_relative = "skills/pet-skill/scripts/compute_suvr.py"

    def preflight(self, context: RunnerContext) -> list[str]:
        problems = self.common_preflight(context)
        for slot_id in ("pet_image", "reference_mask", "target_mask"):
            if context.step.input_bindings.get(slot_id) is None:
                problems.append(f"缺少必需输入：{slot_id}")
        return problems

    def run(self, context: RunnerContext) -> RunnerResult:
        table = context.output_directory / "suvr.csv"
        image = context.output_directory / "suvr-map.nii.gz"
        log = context.output_directory / "compute-suvr.log"
        argv = [
            str(self.executable), str(self.script),
            "--pet", str(self.required_binding(context, "pet_image")),
            "--ref-mask", str(self.required_binding(context, "reference_mask")),
            "--target-mask", str(self.required_binding(context, "target_mask")),
            "--output", str(table),
            "--suvr-map-output", str(image),
        ]
        frame = int(self.parameter(context, "frame", -1))
        if frame >= 0:
            argv.extend(("--frame", str(frame)))
        self.execute(context, argv, environment=self.environment(), log_path=log)
        artifacts = [
            self.artifact(context, table, "generated_file", "SUVR 汇总", "text/csv"),
            self.artifact(context, image, "generated_file", "SUVR 影像", "application/gzip"),
            self.artifact(context, log, "execution_log", "SUVR 定量日志", "text/plain"),
        ]
        context.log("SUVR 输出与日志已通过完整性检查", 100.0, "info")
        return RunnerResult(artifacts=artifacts, message="PET SUVR 定量完成。")


class NiftiInspectRunner(_ReviewedModalityScriptRunner):
    runner_id = "nibabel.inspect"
    label = "Nibabel NIfTI 检查"
    script_relative = "skills/nibabel-skill/scripts/nifti_inspection_reference.py"

    def preflight(self, context: RunnerContext) -> list[str]:
        problems = self.common_preflight(context)
        if context.step.input_bindings.get("image") is None:
            problems.append("缺少 NIfTI 输入影像")
        return problems

    def run(self, context: RunnerContext) -> RunnerResult:
        log = context.output_directory / "nifti-inspection.log"
        argv = [
            str(self.executable), str(self.script),
            "--image", str(self.required_binding(context, "image")),
        ]
        copied = context.output_directory / "nifti-copy.nii.gz"
        if bool(self.parameter(context, "save_copy", False)):
            argv.extend(("--copy-output", str(copied)))
        self.execute(context, argv, environment=self.environment(), log_path=log)
        artifacts = [
            self.artifact(context, log, "execution_log", "NIfTI 头信息", "text/plain")
        ]
        if copied.is_file() and copied.stat().st_size:
            artifacts.append(
                self.artifact(context, copied, "generated_file", "NIfTI 副本", "application/gzip")
            )
        context.log("NIfTI 检查结果已登记", 100.0, "info")
        return RunnerResult(artifacts=artifacts, message="NIfTI 头、维度、数据类型和仿射矩阵检查完成。")
