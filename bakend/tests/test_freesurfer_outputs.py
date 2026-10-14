from pathlib import Path
import tempfile
import unittest

from app.execution.runners.freesurfer import FreeSurferReconAllRunner


class FreeSurferOutputTests(unittest.TestCase):
    def test_stage_contracts_require_both_hemispheres_and_nonempty_files(self):
        contracts = {
            "autorecon1": ["mri/T1.mgz", "mri/brainmask.mgz"],
            "autorecon2": ["mri/aseg.presurf.mgz", "mri/wm.mgz", "mri/filled.mgz",
                           "surf/lh.white.preaparc", "surf/rh.white.preaparc",
                           "surf/lh.inflated", "surf/rh.inflated"],
            "autorecon3": ["mri/aseg.mgz", "stats/aseg.stats", "surf/lh.white",
                           "surf/rh.white", "surf/lh.pial", "surf/rh.pial",
                           "stats/lh.aparc.stats", "stats/rh.aparc.stats"],
        }
        for stage, names in contracts.items():
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                for name in names:
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"test")
                FreeSurferReconAllRunner.validate_outputs(root, stage)
                for name in names:
                    path = root / name
                    path.write_bytes(b"")
                    with self.subTest(missing=name), self.assertRaises(RuntimeError):
                        FreeSurferReconAllRunner.validate_outputs(root, stage)
                    path.write_bytes(b"test")
                if stage == "autorecon2":
                    for hemi in ("lh", "rh"):
                        (root / f"surf/{hemi}.white.preaparc").rename(root / f"surf/{hemi}.white")
                    FreeSurferReconAllRunner.validate_outputs(root, stage)
                with self.assertRaises(RuntimeError):
                    FreeSurferReconAllRunner.validate_outputs(root, "all")
