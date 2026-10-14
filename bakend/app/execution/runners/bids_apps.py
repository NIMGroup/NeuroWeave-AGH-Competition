from __future__ import annotations

import os
import re
from pathlib import Path

from ..base import RunnerContext, RunnerResult
from ..external_command import ExternalCommandRunner


class _BidsAppRunner(ExternalCommandRunner):
    app_name = "BIDS App"

    def runtime_status(self) -> dict[str, object]:
        status = super().runtime_status()
        status.update(
            environment_profile=self.runner_id.replace(".", "-"),
            environment_source="NeuroClaw-compatible executable profile",
        )
        if not status["available"]:
            status["reason"] = (
                f"{self.app_name} 本地可执行环境未配置：{self.executable}；"
                "可将已有 NeuroClaw/Conda 环境入口通过对应 LOCALFLOW_*_EXECUTABLE 指向该路径。"
            )
        return status

    def preflight(self, context: RunnerContext) -> list[str]:
        problems = self.common_preflight(context)
        if context.step.input_bindings.get("bids_dataset") is None:
            problems.append("缺少 BIDS 数据集目录")
        label = str(self.parameter(context, "participant_label", "")).removeprefix("sub-")
        if not re.fullmatch(r"[A-Za-z0-9]+", label):
            problems.append("必须指定一个只含字母和数字的 participant label，避免误运行整个数据集")
        return problems

    def _common_argv(self, context: RunnerContext, output: Path) -> list[str]:
        dataset = self.required_binding(context, "bids_dataset")
        if not dataset.is_dir():
            raise RuntimeError("BIDS 输入必须是目录")
        label = str(self.parameter(context, "participant_label", "")).removeprefix("sub-")
        output.mkdir(parents=True, exist_ok=True)
        return [
            str(self.executable), str(dataset), str(output), "participant",
            "--participant-label", label,
            "--nthreads", str(max(1, int(self.parameter(context, "threads", 4)))),
            "--omp-nthreads", str(max(1, int(self.parameter(context, "omp_threads", 2)))),
            "--mem-mb", str(max(1024, int(float(self.parameter(context, "memory_gb", 8)) * 1024))),
            "--work-dir", str(context.output_directory / "work"),
            "--notrack",
        ]


class FmriprepRunner(_BidsAppRunner):
    runner_id = "fmriprep.run"
    label = "fMRIPrep"
    app_name = "fMRIPrep"

    def __init__(self) -> None:
        self.executable = Path(os.getenv("LOCALFLOW_FMRIPREP_EXECUTABLE", "/opt/neuroclaw/bin/fmriprep"))

    def run(self, context: RunnerContext) -> RunnerResult:
        output = context.output_directory / "fmriprep"
        argv = self._common_argv(context, output)
        spaces = str(self.parameter(context, "output_spaces", "MNI152NLin2009cAsym T1w")).split()
        argv.extend(("--output-spaces", *spaces))
        if bool(self.parameter(context, "skip_bids_validation", False)):
            argv.append("--skip-bids-validation")
        if not bool(self.parameter(context, "run_freesurfer", False)):
            argv.append("--fs-no-reconall")
        else:
            license_file = Path(
                os.getenv(
                    "LOCALFLOW_FREESURFER_LICENSE",
                    "/opt/software/freesurfer-7.4.1/usr/local/freesurfer/7.4.1/license.txt",
                )
            )
            argv.extend(("--fs-license-file", str(license_file)))
        log = context.output_directory / "fmriprep.log"
        self.execute(context, argv, log_path=log, progress_end=95.0)
        artifacts = [
            self.directory_artifact(context, output, "generated_folder", "fMRIPrep derivatives"),
            self.artifact(context, log, "execution_log", "fMRIPrep 日志", "text/plain"),
        ]
        context.log("fMRIPrep 输出与日志已完成登记", 100.0, "info")
        return RunnerResult(artifacts=artifacts, message="fMRIPrep 处理完成。")


class QsiprepRunner(_BidsAppRunner):
    runner_id = "qsiprep.run"
    label = "QSIPrep"
    app_name = "QSIPrep"

    def __init__(self) -> None:
        self.executable = Path(os.getenv("LOCALFLOW_QSIPREP_EXECUTABLE", "/opt/neuroclaw/bin/qsiprep"))

    def run(self, context: RunnerContext) -> RunnerResult:
        output = context.output_directory / "qsiprep"
        argv = self._common_argv(context, output)
        argv.extend(
            (
                "--output-resolution", str(float(self.parameter(context, "output_resolution", 2.0))),
                "--distortion-group-merge", str(self.parameter(context, "distortion_group_merge", "none")),
            )
        )
        if bool(self.parameter(context, "denoise", True)):
            argv.append("--denoise-method=dwidenoise")
        else:
            argv.append("--denoise-method=none")
        if bool(self.parameter(context, "unringing", True)):
            argv.append("--unringing-method=mrdegibbs")
        else:
            argv.append("--unringing-method=none")
        if bool(self.parameter(context, "skip_bids_validation", False)):
            argv.append("--skip-bids-validation")
        log = context.output_directory / "qsiprep.log"
        self.execute(context, argv, log_path=log, progress_end=95.0)
        artifacts = [
            self.directory_artifact(context, output, "preprocessed_dwi", "QSIPrep derivatives"),
            self.artifact(context, log, "execution_log", "QSIPrep 日志", "text/plain"),
        ]
        context.log("QSIPrep 输出与日志已完成登记", 100.0, "info")
        return RunnerResult(artifacts=artifacts, message="QSIPrep 处理完成。")
