from __future__ import annotations

import os
import re
from pathlib import Path

from ..base import RunnerContext, RunnerResult
from ..external_command import ExternalCommandRunner


class Dcm2niixRunner(ExternalCommandRunner):
    runner_id = "dcm2niix.convert"
    label = "dcm2niix DICOM 转换"

    def __init__(self) -> None:
        fsl_root = Path(os.getenv("LOCALFLOW_FSL_ROOT", "/opt/software/fsl"))
        self.executable = Path(
            os.getenv("LOCALFLOW_DCM2NIIX", str(fsl_root / "bin" / "dcm2niix"))
        )

    def preflight(self, context: RunnerContext) -> list[str]:
        problems = self.common_preflight(context)
        if context.step.input_bindings.get("dicom_directory") is None:
            problems.append("缺少 DICOM 输入目录")
        template = str(self.parameter(context, "filename_template", "%p_%s"))
        if not re.fullmatch(r"[A-Za-z0-9_.%+-]{1,120}", template) or ".." in template:
            problems.append("文件名模板只能包含字母、数字、点、下划线、连字符和 dcm2niix % 占位符")
        return problems

    def run(self, context: RunnerContext) -> RunnerResult:
        source = self.required_binding(context, "dicom_directory")
        if not source.is_dir():
            raise RuntimeError("DICOM 输入必须是目录")
        output = context.output_directory / "converted-nifti"
        output.mkdir(parents=True, exist_ok=True)
        log_path = context.output_directory / "dcm2niix.log"
        compress = str(self.parameter(context, "compression", "y"))
        argv = [
            str(self.executable),
            "-b", "y" if bool(self.parameter(context, "bids_sidecar", True)) else "n",
            "-ba", "y" if bool(self.parameter(context, "anonymize", True)) else "n",
            "-z", compress,
            "-f", str(self.parameter(context, "filename_template", "%p_%s")),
            "-o", str(output),
            str(source),
        ]
        self.execute(context, argv, log_path=log_path)
        files = [path for path in output.rglob("*") if path.is_file() and path.stat().st_size]
        if not files:
            raise RuntimeError("dcm2niix 没有生成文件；请确认所选目录包含可读取的 DICOM 序列")
        artifacts = [
            self.directory_artifact(
                context,
                output,
                "generated_folder",
                "dcm2niix 转换结果",
                {"file_count": len(files)},
            ),
            self.artifact(context, log_path, "execution_log", "dcm2niix 执行日志", "text/plain"),
        ]
        context.log(f"dcm2niix 已生成 {len(files)} 个文件", 100.0, "info")
        return RunnerResult(artifacts=artifacts, message=f"DICOM 转换完成，共生成 {len(files)} 个文件。")
