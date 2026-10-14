from __future__ import annotations

import os
import unittest
import tempfile
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

from app.execution.runners.freesurfer import FreeSurferReconAllRunner
from app.execution.base import RunnerContext
from app.execution.runners.fsl import FslEddyRunner
from app.execution.runners.mrtrix_pipeline import MrtrixDwi2FodRunner, MrtrixFiveTtGenRunner


class RunnerEnvironmentTests(unittest.TestCase):
    def test_remote_gpu_deferral_is_only_allowed_during_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runner = FslEddyRunner()
            runner.cuda_executable = Path(temporary) / "eddy_cuda"
            runner.cuda_executable.write_bytes(b"placeholder")
            step = SimpleNamespace(selected_parameters={"backend": "cuda"}, input_bindings={
                name: object() for name in ("dwi", "mask", "acqp", "index", "bvecs", "bvals")
            })
            with patch.object(runner, "common_preflight", return_value=[]), \
                    patch.object(runner, "optional_binding", return_value=None), \
                    patch.object(runner, "runtime_status", return_value={"cuda_available": False}):
                for run_id, remote, should_pass in (("dry-run", True, True),
                                                   ("dry-run", False, False),
                                                   ("run_actual", True, False)):
                    with self.subTest(run_id=run_id, remote=remote):
                        context = RunnerContext(
                            run_id=run_id, workflow=SimpleNamespace(), step=step,
                            method=SimpleNamespace(), output_directory=Path(temporary),
                            cancel_event=Event(), log=lambda *_: None,
                            remote_gpu_available=remote, is_dry_run=run_id == "dry-run",
                        )
                        self.assertEqual(not runner.preflight(context), should_pass)

    def test_freesurfer_environment_contains_delegated_tool_paths(self) -> None:
        home = Path("/opt/test/freesurfer")
        with patch.dict(
            os.environ,
            {
                "LOCALFLOW_FREESURFER_HOME": str(home),
                "LOCALFLOW_FSL_ROOT": "/opt/test/fsl",
                "LOCALFLOW_SKILL_PYTHON": "/opt/test/scientific/bin/python",
                "PATH": "/usr/local/bin:/usr/bin:/bin",
            },
            clear=False,
        ):
            runner = FreeSurferReconAllRunner()
            environment = runner.execution_environment(Path("/outputs/subjects"))

        path_parts = environment["PATH"].split(os.pathsep)
        self.assertEqual(path_parts[0], str(home / "bin"))
        self.assertIn(str(home / "fsfast" / "bin"), path_parts)
        self.assertIn(str(home / "tktools"), path_parts)
        self.assertIn(str(home / "mni" / "bin"), path_parts)
        self.assertEqual(environment["MINC_BIN_DIR"], str(home / "mni" / "bin"))
        self.assertEqual(environment["MINC_LIB_DIR"], str(home / "mni" / "lib"))
        self.assertEqual(environment["MNI_DIR"], str(home / "mni"))
        self.assertEqual(environment["SUBJECTS_DIR"], str(Path("/outputs/subjects")))
        self.assertEqual(environment["FREESURFER"], str(home))

    def test_mrtrix_five_tt_environment_contains_mrtrix_and_fsl(self) -> None:
        with patch.dict(
            os.environ,
            {
                "LOCALFLOW_MRTRIX_ROOT": "/opt/test/mrtrix",
                "LOCALFLOW_FSL_ROOT": "/opt/test/fsl",
                "LOCALFLOW_SKILL_PYTHON": "/opt/test/scientific/bin/python",
                "PATH": "/usr/local/bin:/usr/bin:/bin",
            },
            clear=False,
        ):
            environment = MrtrixFiveTtGenRunner.execution_environment()

        path_parts = environment["PATH"].split(os.pathsep)
        self.assertEqual(path_parts[0], str(Path("/opt/test/mrtrix/bin")))
        self.assertEqual(path_parts[1], str(Path("/opt/test/scientific/bin")))
        self.assertIn(str(Path("/opt/test/fsl/bin")), path_parts)
        self.assertEqual(environment["FSLDIR"], str(Path("/opt/test/fsl")))

    def test_mrtrix_fod_lmax_matches_tissue_count(self) -> None:
        runner = MrtrixDwi2FodRunner()
        with tempfile.TemporaryDirectory() as temporary:
            for algorithm, expected in (("csd", "6"), ("msmt_csd", "6,0,0")):
                with self.subTest(algorithm=algorithm):
                    context = SimpleNamespace(output_directory=Path(temporary),
                        step=SimpleNamespace(selected_parameters={"algorithm": algorithm, "lmax": 6}),
                        method=SimpleNamespace(parameters=[]), prepared_options={'gradient_args': []}, log=lambda *_: None)
                    with patch.object(runner, "required_binding", side_effect=lambda _, slot: Path(temporary) / slot), \
                            patch.object(runner, "artifact", return_value=None), \
                            patch.object(runner, "execute") as execute:
                        runner.run(context)
                    argv = execute.call_args.args[1]
                    self.assertEqual(argv[argv.index("-lmax") + 1], expected)


if __name__ == "__main__":
    unittest.main()
