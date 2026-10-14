from __future__ import annotations

import os
from pathlib import Path

from ..base import RunnerContext, RunnerResult
from ..external_command import ExternalCommandRunner


def _ants_root() -> Path:
    return Path(os.getenv("LOCALFLOW_ANTS_ROOT", "/opt/software/ants-2.6.5"))


class AntsRegistrationRunner(ExternalCommandRunner):
    runner_id = "ants.registration"
    label = "ANTs 图像配准"

    def __init__(self) -> None:
        self.executable = _ants_root() / "bin" / "antsRegistration"

    def preflight(self, context: RunnerContext) -> list[str]:
        problems = self.common_preflight(context)
        for slot_id in ("moving_image", "reference_image"):
            if context.step.input_bindings.get(slot_id) is None:
                problems.append(f"缺少必需输入：{slot_id}")
        if bool(self.parameter(context, "use_masks", False)):
            for slot_id in ("moving_mask", "reference_mask"):
                if context.step.input_bindings.get(slot_id) is None:
                    problems.append(f"启用 Mask 时必须绑定：{slot_id}")
        return problems

    def run(self, context: RunnerContext) -> RunnerResult:
        moving = self.required_binding(context, "moving_image")
        reference = self.required_binding(context, "reference_image")
        prefix = context.output_directory / "ants_"
        warped = context.output_directory / "registered.nii.gz"
        inverse = context.output_directory / "inverse_registered.nii.gz"
        transform = str(self.parameter(context, "transform", "syn"))
        interpolation = str(self.parameter(context, "interpolation", "Linear"))
        metric_bins = int(self.parameter(context, "metric_bins", 32))
        sampling = float(self.parameter(context, "sampling_percentage", 0.25))
        argv = [
            str(self.executable),
            "--dimensionality", "3",
            "--float", "1" if bool(self.parameter(context, "float_precision", True)) else "0",
            "--collapse-output-transforms", "1",
            "--output", f"[{prefix},{warped},{inverse}]",
            "--interpolation", interpolation,
            "--use-histogram-matching", "1" if bool(self.parameter(context, "histogram_matching", False)) else "0",
            "--winsorize-image-intensities", "[0.005,0.995]",
            "--initial-moving-transform", f"[{reference},{moving},1]",
        ]
        argv.extend(self._linear_stage(reference, moving, "Rigid", metric_bins, sampling))
        if transform in {"affine", "syn"}:
            argv.extend(self._linear_stage(reference, moving, "Affine", metric_bins, sampling))
        if transform == "syn":
            argv.extend(
                [
                    "--transform", "SyN[0.1,3,0]",
                    "--metric", f"CC[{reference},{moving},1,4]",
                    "--convergence", "[100x70x50x20,1e-6,10]",
                    "--shrink-factors", "8x4x2x1",
                    "--smoothing-sigmas", "3x2x1x0vox",
                ]
            )
        if bool(self.parameter(context, "use_masks", False)):
            fixed_mask = self.required_binding(context, "reference_mask")
            moving_mask = self.required_binding(context, "moving_mask")
            argv.extend(("--masks", f"[{fixed_mask},{moving_mask}]"))
        argv.extend(("--write-composite-transform", "1", "--verbose", "1"))
        threads = max(1, int(self.parameter(context, "threads", 4)))
        self.execute(
            context,
            argv,
            environment={
                "ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS": str(threads),
                "PATH": str(_ants_root() / "bin") + os.pathsep + os.getenv("PATH", ""),
            },
            log_path=context.output_directory / "ants-registration.log",
        )
        composite = context.output_directory / "ants_Composite.h5"
        artifacts = [
            self.artifact(context, warped, "registered_image", "ANTs 配准影像", "application/gzip"),
            self.artifact(context, composite, "transform_matrix", "ANTs 复合变换", "application/x-hdf5"),
            self.artifact(context, context.output_directory / "ants-registration.log", "execution_log", "ANTs 执行日志", "text/plain"),
        ]
        if inverse.is_file() and inverse.stat().st_size:
            artifacts.append(
                self.artifact(context, inverse, "registered_image", "ANTs 反向配准影像", "application/gzip")
            )
        context.log("ANTs 配准结果、复合变换和日志已通过完整性检查", 100.0, "info")
        return RunnerResult(artifacts=artifacts, message=f"ANTs {transform} 配准完成。")

    @staticmethod
    def _linear_stage(
        reference: Path,
        moving: Path,
        transform: str,
        bins: int,
        sampling: float,
    ) -> list[str]:
        return [
            "--transform", f"{transform}[0.1]",
            "--metric", f"MI[{reference},{moving},1,{bins},Regular,{sampling}]",
            "--convergence", "[1000x500x250x100,1e-6,10]",
            "--shrink-factors", "8x4x2x1",
            "--smoothing-sigmas", "3x2x1x0vox",
        ]
