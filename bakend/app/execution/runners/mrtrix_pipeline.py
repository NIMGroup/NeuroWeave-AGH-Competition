from __future__ import annotations

import os
from pathlib import Path

from ..base import RunnerContext, RunnerResult
from ..external_command import ExternalCommandRunner


def _mrtrix_root() -> Path:
    return Path(os.getenv("LOCALFLOW_MRTRIX_ROOT", "/opt/software/mrtrix3-master"))


class _MrtrixRunner(ExternalCommandRunner):
    command = ""

    def __init__(self) -> None:
        self.executable = _mrtrix_root() / "bin" / self.command

    def runtime_status(self) -> dict[str, object]:
        status = super().runtime_status()
        python = Path(os.getenv("LOCALFLOW_SKILL_PYTHON", "/opt/neuroweave/envs/scientific-py311/bin/python"))
        if self.command in {"dwi2response", "5ttgen"} and not (python.is_file() and os.access(python, os.X_OK)):
            status.update(available=False, reason=f"MRtrix Python 前端缺少公共解释器：{python}")
        libpng = Path(os.getenv("LOCALFLOW_MRTRIX_LIBPNG", "/lib/x86_64-linux-gnu/libpng16.so.16"))
        if status["available"] and not libpng.is_file():
            status.update(available=False, reason=f"缺少 MRtrix 运行库：{libpng}")
        status["environment_profile"] = "mrtrix3"
        return status

    @staticmethod
    def environment() -> dict[str, str]:
        root = _mrtrix_root()
        return {
            # Some MRtrix commands are Python front-ends that delegate to
            # sibling binaries by name.  Starting the front-end by absolute
            # path does not itself make those helpers discoverable.
            "PATH": os.pathsep.join([str(root / "bin"),
                str(Path(os.getenv("LOCALFLOW_SKILL_PYTHON", "/opt/neuroweave/envs/scientific-py311/bin/python")).parent),
                os.getenv("PATH", "")]),
            "LD_LIBRARY_PATH": os.getenv("LD_LIBRARY_PATH", ""),
        }


class MrtrixDwi2ResponseRunner(_MrtrixRunner):
    runner_id = "mrtrix.dwi2response"
    label = "MRtrix3 响应函数估计"
    command = "dwi2response"

    def preflight(self, context: RunnerContext) -> list[str]:
        problems = self.common_preflight(context)
        for slot_id in ("dwi", "mask"):
            if context.step.input_bindings.get(slot_id) is None:
                problems.append(f"缺少必需输入：{slot_id}")
        return problems

    def run(self, context: RunnerContext) -> RunnerResult:
        algorithm = str(self.parameter(context, "algorithm", "dhollander"))
        wm = context.output_directory / "wm_response.txt"
        gm = context.output_directory / "gm_response.txt"
        csf = context.output_directory / "csf_response.txt"
        argv = [
            str(self.executable), algorithm, str(self.required_binding(context, "dwi")), str(wm)
        ]
        if algorithm == "dhollander":
            argv.extend((str(gm), str(csf)))
        argv.extend(
            (
                "-mask", str(self.required_binding(context, "mask")),
                "-nthreads", str(max(1, int(self.parameter(context, "threads", 4)))),
                "-force",
            )
        )
        from ..gradient_policy import gradient_arguments
        argv.extend(context.prepared_options.get('gradient_args',[]) if 'gradient_args' in context.prepared_options
                    else gradient_arguments(context)[0])
        log = context.output_directory / "dwi2response.log"
        self.execute(context, argv, environment=self.environment(), log_path=log)
        artifacts = [
            self.artifact(context, wm, "response_functions", "白质响应函数", "text/plain"),
            self.artifact(context, log, "execution_log", "dwi2response 日志", "text/plain"),
        ]
        for path, label in ((gm, "灰质响应函数"), (csf, "脑脊液响应函数")):
            if path.is_file() and path.stat().st_size:
                artifacts.append(self.artifact(context, path, "response_functions", label, "text/plain"))
        context.log("响应函数与日志已通过完整性检查", 100.0, "info")
        return RunnerResult(artifacts=artifacts, message=f"{algorithm} 响应函数估计完成。")


class MrtrixDwi2FodRunner(_MrtrixRunner):
    runner_id = "mrtrix.dwi2fod"
    label = "MRtrix3 FOD 估计"
    command = "dwi2fod"

    def preflight(self, context: RunnerContext) -> list[str]:
        problems = self.common_preflight(context)
        for slot_id in ("dwi", "mask", "wm_response"):
            if context.step.input_bindings.get(slot_id) is None:
                problems.append(f"缺少必需输入：{slot_id}")
        algorithm = str(self.parameter(context, "algorithm", "csd"))
        if algorithm == "msmt_csd":
            for slot_id in ("gm_response", "csf_response"):
                if context.step.input_bindings.get(slot_id) is None:
                    problems.append(f"MSMT-CSD 必须绑定：{slot_id}")
        return problems

    def run(self, context: RunnerContext) -> RunnerResult:
        algorithm = str(self.parameter(context, "algorithm", "csd"))
        dwi = self.required_binding(context, "dwi")
        wm_fod = context.output_directory / "wm_fod.mif"
        argv = [str(self.executable), algorithm, str(dwi), str(self.required_binding(context, "wm_response")), str(wm_fod)]
        outputs = [(wm_fod, "白质 FOD")]
        if algorithm == "msmt_csd":
            gm = context.output_directory / "gm.mif"
            csf = context.output_directory / "csf.mif"
            argv.extend(
                (
                    str(self.required_binding(context, "gm_response")), str(gm),
                    str(self.required_binding(context, "csf_response")), str(csf),
                )
            )
            outputs.extend(((gm, "灰质成分"), (csf, "脑脊液成分")))
        # MRtrix expects one lmax per response/output pair. GM and CSF are
        # isotropic compartments; the exposed lmax controls the WM FOD only.
        wm_lmax = str(int(self.parameter(context, "lmax", 8)))
        lmax = f"{wm_lmax},0,0" if algorithm == "msmt_csd" else wm_lmax
        argv.extend(
            (
                "-mask", str(self.required_binding(context, "mask")),
                "-lmax", lmax,
                "-nthreads", str(max(1, int(self.parameter(context, "threads", 4)))),
                "-force",
            )
        )
        from ..gradient_policy import gradient_arguments
        argv.extend(context.prepared_options.get('gradient_args',[]) if 'gradient_args' in context.prepared_options
                    else gradient_arguments(context)[0])
        log = context.output_directory / "dwi2fod.log"
        self.execute(context, argv, environment=self.environment(), log_path=log)
        artifacts = [
            self.artifact(context, path, "fod_image", label) for path, label in outputs
        ]
        artifacts.append(self.artifact(context, log, "execution_log", "dwi2fod 日志", "text/plain"))
        context.log("FOD 输出与日志已通过完整性检查", 100.0, "info")
        return RunnerResult(artifacts=artifacts, message=f"{algorithm} FOD 估计完成。")


class MrtrixFiveTtGenRunner(_MrtrixRunner):
    runner_id = "mrtrix.5ttgen"
    label = "MRtrix3 5TT 生成"
    command = "5ttgen"

    def preflight(self, context: RunnerContext) -> list[str]:
        problems = self.common_preflight(context)
        if context.step.input_bindings.get("t1w") is None:
            problems.append("缺少 T1w 输入")
        return problems

    @classmethod
    def execution_environment(cls) -> dict[str, str]:
        environment = cls.environment()
        fsl_root = Path(os.getenv("LOCALFLOW_FSL_ROOT", "/opt/software/fsl"))
        environment.update(
            FSLDIR=str(fsl_root),
            FSL_DIR=str(fsl_root),
            FSLOUTPUTTYPE="NIFTI_GZ",
            PATH=environment["PATH"] + os.pathsep + str(fsl_root / "bin"),
        )
        return environment

    def run(self, context: RunnerContext) -> RunnerResult:
        output = context.output_directory / "5tt.mif"
        algorithm = str(self.parameter(context, "algorithm", "fsl"))
        argv = [
            str(self.executable), algorithm, str(self.required_binding(context, "t1w")), str(output),
            "-nthreads", str(max(1, int(self.parameter(context, "threads", 4)))), "-force",
        ]
        if bool(self.parameter(context, "nocrop", False)):
            argv.append("-nocrop")
        log = context.output_directory / "5ttgen.log"
        environment = self.execution_environment()
        self.execute(context, argv, environment=environment, log_path=log)
        artifacts = [
            self.artifact(context, output, "five_tt_image", "5TT 组织分割"),
            self.artifact(context, log, "execution_log", "5ttgen 日志", "text/plain"),
        ]
        context.log("5TT 输出与日志已通过完整性检查", 100.0, "info")
        return RunnerResult(artifacts=artifacts, message="5TT 组织分割已生成。")


class MrtrixTck2ConnectomeRunner(_MrtrixRunner):
    runner_id = "mrtrix.tck2connectome"
    label = "MRtrix3 结构连接组"
    command = "tck2connectome"

    def preflight(self, context: RunnerContext) -> list[str]:
        problems = self.common_preflight(context)
        for slot_id in ("tractogram", "parcellation"):
            if context.step.input_bindings.get(slot_id) is None:
                problems.append(f"缺少必需输入：{slot_id}")
        if bool(self.parameter(context, "use_weights", False)) and context.step.input_bindings.get("weights") is None:
            problems.append("启用流线权重时必须绑定 SIFT2 权重")
        if str(self.parameter(context, "edge_weight", "count")) == "mean_scalar" and context.step.input_bindings.get("scalar") is None:
            problems.append("选择平均标量边权时必须绑定标量影像")
        return problems

    def run(self, context: RunnerContext) -> RunnerResult:
        output = context.output_directory / "connectome.csv"
        assignments = context.output_directory / "assignments.txt"
        scalar_weight = str(self.parameter(context, "edge_weight", "count")) == "mean_scalar"
        stat_edge = "mean" if scalar_weight else str(self.parameter(context, "stat_edge", "sum"))
        argv = [
            str(self.executable),
            str(self.required_binding(context, "tractogram")),
            str(self.required_binding(context, "parcellation")),
            str(output),
            "-out_assignments", str(assignments),
            "-assignment_radial_search", str(float(self.parameter(context, "radial_search", 2))),
            "-stat_edge", stat_edge,
            "-nthreads", str(max(1, int(self.parameter(context, "threads", 4)))),
            "-force",
        ]
        if bool(self.parameter(context, "symmetric", True)):
            argv.append("-symmetric")
        if bool(self.parameter(context, "zero_diagonal", True)):
            argv.append("-zero_diagonal")
        if bool(self.parameter(context, "use_weights", False)):
            argv.extend(("-tck_weights_in", str(self.required_binding(context, "weights"))))
        if scalar_weight:
            argv.extend(("-scale_file", str(self.required_binding(context, "scalar"))))
        log = context.output_directory / "tck2connectome.log"
        self.execute(context, argv, environment=self.environment(), log_path=log)
        artifacts = [
            self.artifact(context, output, "connectome", "结构连接组矩阵", "text/csv"),
            self.artifact(context, assignments, "generated_file", "流线节点分配", "text/plain"),
            self.artifact(context, log, "execution_log", "tck2connectome 日志", "text/plain"),
        ]
        context.log("连接组矩阵、节点分配与日志已通过完整性检查", 100.0, "info")
        return RunnerResult(artifacts=artifacts, message="结构连接组矩阵已生成。")
