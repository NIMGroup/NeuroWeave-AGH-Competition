"""Small synthetic checks only; no datasets or downloaded models are used."""
from __future__ import annotations

import argparse
import importlib
import json
import os
import socket
import sys
import tempfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=["scientific", "torch"], required=True)
    parser.add_argument("--require-cuda", action="store_true")
    args = parser.parse_args()
    os.environ.setdefault("MPLBACKEND", "Agg")
    modules = (
        "numpy scipy pandas sklearn statsmodels xgboost imblearn optuna lifelines sksurv "
        "pingouin umap hdbscan nibabel nilearn dipy SimpleITK bids nipype templateflow "
        "mne mne_connectivity autoreject pyriemann neurokit2 antropy pyentrp bct networkx "
        "matplotlib seaborn plotly h5py skimage pydicom openpyxl pyarrow"
        if args.profile == "scientific" else
        "numpy scipy pandas sklearn nibabel matplotlib h5py torch torchvision monai "
        "torchio lightning torchmetrics timm einops transformers accelerate safetensors tensorboard"
    )
    failures = {}
    for module in modules.split():
        try:
            importlib.import_module(module)
        except Exception as exc:
            failures[module] = f"{type(exc).__name__}: {exc}"
    report = {"hostname": socket.gethostname(), "python": sys.executable,
              "profile": args.profile, "slurm_job_id": os.getenv("SLURM_JOB_ID"),
              "import_failures": failures}
    if failures:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    import numpy as np
    import nibabel as nib
    from sklearn.linear_model import Ridge
    x = np.arange(20, dtype=float).reshape(10, 2)
    prediction = Ridge().fit(x, x[:, 0]).predict(x)
    assert np.isfinite(prediction).all()
    with tempfile.TemporaryDirectory(prefix="neuroweave-probe-") as tmp:
        path = Path(tmp) / "synthetic.nii.gz"
        nib.save(nib.Nifti1Image(np.zeros((4, 4, 4), dtype=np.float32), np.eye(4)), path)
        assert nib.load(path).shape == (4, 4, 4)
    if args.profile == "scientific":
        import mne
        raw = mne.io.RawArray(np.zeros((2, 100)), mne.create_info(2, 100, "eeg"), verbose=False)
        assert raw.n_times == 100
    else:
        import torch
        report.update(torch=torch.__version__, cuda_build=torch.version.cuda,
                      cuda_available=torch.cuda.is_available(), arch_list=torch.cuda.get_arch_list())
        if args.require_cuda and not torch.cuda.is_available():
            print(json.dumps(report, ensure_ascii=False, indent=2))
            raise SystemExit("CUDA requested but unavailable")
        device = "cuda" if torch.cuda.is_available() else "cpu"
        a = torch.ones((128, 128), device=device)
        assert torch.allclose(a @ a, torch.full_like(a, 128))
        if device == "cuda":
            torch.cuda.synchronize()
            report["gpu"] = torch.cuda.get_device_name()
    report["synthetic_checks"] = "passed"
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
