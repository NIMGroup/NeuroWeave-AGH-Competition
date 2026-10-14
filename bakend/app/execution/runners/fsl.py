from __future__ import annotations

import os
from pathlib import Path

from ..base import RunnerContext, RunnerResult
from ..external_command import ExternalCommandRunner


def _fsl_root() -> Path:
    return Path(os.getenv("LOCALFLOW_FSL_ROOT", "/opt/software/fsl"))


def _fsl_environment() -> dict[str, str]:
    root = _fsl_root()
    previous_library_path = os.getenv("LD_LIBRARY_PATH", "")
    return {
        "FSLDIR": str(root),
        "FSLOUTPUTTYPE": "NIFTI_GZ",
        "PATH": str(root / "bin") + ":" + os.getenv("PATH", ""),
        "LD_LIBRARY_PATH": str(root / "lib")
        + ((":" + previous_library_path) if previous_library_path else ""),
    }


class FslFlirtRunner(ExternalCommandRunner):
    runner_id = "fsl.flirt"
    label = "FSL FLIRT 图像配准"

    def __init__(self) -> None:
        self.executable = _fsl_root() / "bin" / "flirt"

    def preflight(self, context: RunnerContext) -> list[str]:
        problems = self.common_preflight(context)
        for slot_id in ("moving_image", "reference_image"):
            if context.step.input_bindings.get(slot_id) is None:
                problems.append(f"缺少必需输入：{slot_id}")
        if bool(self.parameter(context, "use_masks", False)):
            for slot_id in ("moving_mask", "reference_mask"):
                if context.step.input_bindings.get(slot_id) is None:
                    problems.append(f"启用权重 Mask 时必须绑定：{slot_id}")
        return problems

    def run(self, context: RunnerContext) -> RunnerResult:
        moving = self.required_binding(context, "moving_image")
        reference = self.required_binding(context, "reference_image")
        output = context.output_directory / "registered.nii.gz"
        matrix = context.output_directory / "transform.mat"
        transform = str(self.parameter(context, "transform", "rigid"))
        interpolation = str(self.parameter(context, "interpolation", "trilinear"))
        cost = str(self.parameter(context, "cost_function", "corratio"))
        search = str(float(self.parameter(context, "search_degrees", 90)))
        argv = [
            str(self.executable), "-in", str(moving), "-ref", str(reference),
            "-out", str(output), "-omat", str(matrix),
            "-dof", "6" if transform == "rigid" else "12",
            "-interp", interpolation, "-cost", cost,
            "-searchrx", f"-{search}", search,
            "-searchry", f"-{search}", search,
            "-searchrz", f"-{search}", search,
            "-bins", str(int(self.parameter(context, "bins", 256))),
        ]
        dtype = str(self.parameter(context, "output_dtype", "float"))
        if dtype != "input":
            argv.extend(("-datatype", dtype))
        if bool(self.parameter(context, "use_masks", False)):
            argv.extend(("-inweight", str(self.required_binding(context, "moving_mask"))))
            argv.extend(("-refweight", str(self.required_binding(context, "reference_mask"))))
        self.execute(context, argv, environment=_fsl_environment())
        artifacts = [
            self.artifact(context, output, "registered_image", "FLIRT 配准影像", "application/gzip"),
            self.artifact(context, matrix, "transform_matrix", "FLIRT 变换矩阵", "text/plain"),
        ]
        context.log("FLIRT 输出已通过存在性和校验和检查", 100.0, "info")
        return RunnerResult(artifacts=artifacts, message="图像配准完成，生成配准影像和变换矩阵。")


class FslDtifitRunner(ExternalCommandRunner):
    runner_id = "fsl.dtifit"
    label = "FSL dtifit 张量拟合"

    def __init__(self) -> None:
        self.executable = _fsl_root() / "bin" / "dtifit"

    def preflight(self, context: RunnerContext) -> list[str]:
        problems = self.common_preflight(context)
        for slot_id in ("dwi", "mask", "bvecs", "bvals"):
            if context.step.input_bindings.get(slot_id) is None:
                problems.append(f"缺少必需输入：{slot_id}")
        return problems

    def run(self, context: RunnerContext) -> RunnerResult:
        output_base = context.output_directory / "dti"
        argv = [
            str(self.executable),
            "--data=" + str(self.required_binding(context, "dwi")),
            "--out=" + str(output_base),
            "--mask=" + str(self.required_binding(context, "mask")),
            "--bvecs=" + str(self.required_binding(context, "bvecs")),
            "--bvals=" + str(self.required_binding(context, "bvals")),
        ]
        if bool(self.parameter(context, "weighted_least_squares", True)):
            argv.append("--wls")
        if bool(self.parameter(context, "save_tensor", False)):
            argv.append("--save_tensor")
        if bool(self.parameter(context, "save_sse", True)):
            argv.append("--sse")
        if bool(self.parameter(context, "kurtosis", False)):
            argv.append("--kurt")
        self.execute(context, argv, environment=_fsl_environment())
        required = {
            "FA": context.output_directory / "dti_FA.nii.gz",
            "MD": context.output_directory / "dti_MD.nii.gz",
            "L1": context.output_directory / "dti_L1.nii.gz",
            "L2": context.output_directory / "dti_L2.nii.gz",
            "L3": context.output_directory / "dti_L3.nii.gz",
        }
        artifacts = [
            self.artifact(
                context,
                path,
                "diffusion_metrics",
                f"DTI {name}",
                "application/gzip",
                {"metric": name},
            )
            for name, path in required.items()
        ]
        context.log("DTI 指标输出已通过完整性检查", 100.0, "info")
        return RunnerResult(artifacts=artifacts, message=f"张量拟合完成，登记 {len(artifacts)} 个指标影像。")


class FslEddyRunner(ExternalCommandRunner):
    runner_id = "fsl.eddy"
    label = "FSL eddy 畸变与运动校正"

    def __init__(self) -> None:
        self.cpu_executable = _fsl_root() / "bin" / "eddy_cpu"
        self.cuda_executable = _fsl_root() / "bin" / "eddy_cuda"
        self.executable = self.cpu_executable

    def runtime_status(self) -> dict[str, object]:
        status = super().runtime_status()
        status["cpu_executable"] = str(self.cpu_executable)
        status["cuda_executable"] = str(self.cuda_executable)
        status["cuda_available"] = bool(
            self.cuda_executable.is_file() and Path("/dev/nvidia0").exists()
        )
        return status

    def allowed_executables(self) -> set[Path]:
        return {self.cpu_executable, self.cuda_executable}

    def preflight(self, context: RunnerContext) -> list[str]:
        problems = self.common_preflight(context)
        for slot_id in ("dwi", "mask", "acqp", "index", "bvecs", "bvals"):
            if context.step.input_bindings.get(slot_id) is None:
                problems.append(f"缺少必需输入：{slot_id}")
        backend = str(self.parameter(context, "backend", "cpu"))
        if backend == "cuda" and not self.cuda_executable.is_file():
            problems.append("缺少 CUDA eddy 可执行文件")
        elif (backend == "cuda" and not self.runtime_status().get("cuda_available")
              and not (context.run_id == "dry-run" and context.remote_gpu_available)):
            problems.append("选择了 CUDA eddy，但当前 Worker 未检测到 NVIDIA 设备")
        if str(self.parameter(context, "susceptibility_correction", "auto")) == "topup":
            if context.step.input_bindings.get("topup_fieldcoef") is None:
                problems.append("强制使用 topup 时必须绑定 topup fieldcoef 输出")
        topup = (
            None if context.input_is_deferred("topup_fieldcoef")
            else self.optional_binding(context, "topup_fieldcoef")
        )
        if topup is not None:
            base = self._topup_base(topup)
            if not Path(str(base) + "_movpar.txt").is_file():
                problems.append("topup fieldcoef 缺少同前缀的 _movpar.txt 配套文件")
        return problems

    def run(self, context: RunnerContext) -> RunnerResult:
        backend = str(self.parameter(context, "backend", "cpu"))
        executable = self.cuda_executable if backend == "cuda" else self.cpu_executable
        output_base = context.output_directory / "eddy_corrected"
        argv = [
            str(executable),
            "--imain=" + str(self.required_binding(context, "dwi")),
            "--mask=" + str(self.required_binding(context, "mask")),
            "--acqp=" + str(self.required_binding(context, "acqp")),
            "--index=" + str(self.required_binding(context, "index")),
            "--bvecs=" + str(self.required_binding(context, "bvecs")),
            "--bvals=" + str(self.required_binding(context, "bvals")),
            "--out=" + str(output_base),
            "--niter=" + str(int(self.parameter(context, "iterations", 5))),
            "--flm=" + (
                "quadratic"
                if self.parameter(context, "spatial_model", "quadratic") == "movement_by_susceptibility"
                else str(self.parameter(context, "spatial_model", "quadratic"))
            ),
            "--verbose",
        ]
        if bool(self.parameter(context, "outlier_replacement", True)):
            argv.extend(("--repol", "--ol_type=both"))
        mporder = int(self.parameter(context, "slice_to_volume", 0))
        if mporder > 0:
            argv.extend((f"--mporder={mporder}", "--s2v_niter=5", "--s2v_lambda=1"))
        if bool(self.parameter(context, "data_is_shelled", True)):
            argv.append("--data_is_shelled")
        if self.parameter(context, "spatial_model", "quadratic") == "movement_by_susceptibility":
            argv.extend(("--estimate_move_by_susceptibility", "--mbs_niter=10"))
        topup = self.optional_binding(context, "topup_fieldcoef")
        correction = str(self.parameter(context, "susceptibility_correction", "auto"))
        if topup is not None and correction != "none":
            argv.append("--topup=" + str(self._topup_base(topup)))
        threads = max(1, int(self.parameter(context, "threads", 4)))
        log_path = context.output_directory / "eddy.log"
        self.execute(
            context,
            argv,
            environment={**_fsl_environment(), "OMP_NUM_THREADS": str(threads)},
            log_path=log_path,
        )
        artifacts = [
            self.artifact(context, context.output_directory / "eddy_corrected.nii.gz", "preprocessed_dwi", "Eddy 校正 DWI", "application/gzip"),
            self.artifact(context, context.output_directory / "eddy_corrected.eddy_rotated_bvecs", "gradient_table", "旋转后的 bvec", "text/plain"),
            self.artifact(context, context.output_directory / "eddy_corrected.eddy_parameters", "preprocess_qc", "Eddy 运动参数", "text/plain"),
            self.artifact(context, log_path, "execution_log", "Eddy 执行日志", "text/plain"),
        ]
        for name, label in (
            ("eddy_corrected.eddy_outlier_report", "Eddy 离群值报告"),
            ("eddy_corrected.eddy_outlier_map", "Eddy 离群值图"),
        ):
            path = context.output_directory / name
            if path.is_file() and path.stat().st_size:
                artifacts.append(self.artifact(context, path, "preprocess_qc", label, "text/plain"))
        context.log("Eddy 输出、旋转梯度表与 QC 已通过完整性检查", 100.0, "info")
        return RunnerResult(artifacts=artifacts, message="FSL eddy 校正完成。")

    @staticmethod
    def _topup_base(fieldcoef: Path) -> Path:
        name = fieldcoef.name
        suffix = "_fieldcoef.nii.gz"
        if not name.endswith(suffix):
            raise RuntimeError("topup 输入必须选择 *_fieldcoef.nii.gz")
        return fieldcoef.with_name(name[: -len(suffix)])
