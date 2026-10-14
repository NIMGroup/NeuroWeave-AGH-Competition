from __future__ import annotations

import os
from pathlib import Path

from ..base import RunnerContext, RunnerResult
from ..external_command import ExternalCommandRunner


class Nii2DcmRunner(ExternalCommandRunner):
    runner_id = "nii2dcm.convert"
    label = "NIfTI 转 DICOM"

    def __init__(self) -> None:
        self.executable = Path(os.getenv("LOCALFLOW_NII2DCM_EXECUTABLE", "/opt/neuroclaw/bin/nii2dcm"))

    def runtime_status(self) -> dict[str, object]:
        status = super().runtime_status()
        status.update(
            environment_profile="nii2dcm",
            environment_source="NeuroClaw-compatible executable profile",
        )
        if not status["available"]:
            status["reason"] = (
                f"nii2dcm 环境未配置：{self.executable}；安装到已有环境后设置 "
                "LOCALFLOW_NII2DCM_EXECUTABLE，无需修改 Skill。"
            )
        return status

    def preflight(self, context: RunnerContext) -> list[str]:
        problems = self.common_preflight(context)
        if context.step.input_bindings.get("nifti") is None:
            problems.append("缺少 NIfTI 输入")
        if bool(self.parameter(context, "copy_reference_metadata", False)):
            if context.step.input_bindings.get("reference_dicom") is None:
                problems.append("复制参考元数据时必须绑定一个参考 DICOM 文件")
        return problems

    def run(self, context: RunnerContext) -> RunnerResult:
        output = context.output_directory / "dicom-series"
        output.mkdir(parents=True, exist_ok=True)
        argv = [str(self.executable), str(self.required_binding(context, "nifti")), str(output)]
        dicom_type = str(self.parameter(context, "dicom_type", "MR"))
        if dicom_type != "generic":
            argv.extend(("-d", dicom_type))
        if bool(self.parameter(context, "copy_reference_metadata", False)):
            argv.extend(("-r", str(self.required_binding(context, "reference_dicom"))))
        log = context.output_directory / "nii2dcm.log"
        self.execute(context, argv, log_path=log)
        files = [path for path in output.rglob("*") if path.is_file() and path.stat().st_size]
        if not files:
            raise RuntimeError("nii2dcm 没有生成 DICOM 文件")
        artifacts = [
            self.directory_artifact(context, output, "generated_folder", "DICOM 序列", {"file_count": len(files)}),
            self.artifact(context, log, "execution_log", "nii2dcm 日志", "text/plain"),
        ]
        context.log("DICOM 序列与日志已完成登记", 100.0, "info")
        return RunnerResult(artifacts=artifacts, message=f"已生成 {len(files)} 个 DICOM 文件。")
