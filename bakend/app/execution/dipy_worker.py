from __future__ import annotations

import argparse
from pathlib import Path

import nibabel as nib
import numpy as np
from dipy.core.gradients import gradient_table
from dipy.denoise.noise_estimate import estimate_sigma
from dipy.reconst import dti
from dipy.segment.mask import median_otsu


def main() -> None:
    parser = argparse.ArgumentParser(description="NeuroWeave reviewed DIPY tensor worker")
    parser.add_argument("--dwi", type=Path, required=True)
    parser.add_argument("--bvals", type=Path, required=True)
    parser.add_argument("--bvecs", type=Path, required=True)
    parser.add_argument("--mask", type=Path)
    parser.add_argument("--mask-strategy", choices=("existing", "median_otsu", "none"), default="existing")
    parser.add_argument("--fit-method", choices=("WLS", "OLS", "RESTORE"), default="WLS")
    parser.add_argument("--b0-threshold", type=float, default=50)
    parser.add_argument("--min-signal", type=float, default=1)
    parser.add_argument("--restore-sigma", type=float, default=20)
    parser.add_argument("--clip-negative-eigenvalues", action="store_true")
    parser.add_argument("--save-eigenvectors", action="store_true")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    image = nib.load(str(args.dwi))
    data = np.asarray(image.get_fdata(dtype=np.float32))
    bvals = np.loadtxt(args.bvals)
    bvecs = np.loadtxt(args.bvecs)
    if bvecs.ndim == 2 and bvecs.shape[0] == 3:
        bvecs = bvecs.T
    if data.ndim != 4 or data.shape[-1] != len(np.ravel(bvals)):
        raise ValueError("DWI 第四维与 bval 数量不一致")
    if bvecs.shape != (data.shape[-1], 3):
        raise ValueError("bvec 必须为 3×N 或 N×3，且 N 与 DWI 体积数一致")
    gtab = gradient_table(bvals=bvals, bvecs=bvecs, b0_threshold=args.b0_threshold)
    if args.mask_strategy == "existing":
        if args.mask is None:
            raise ValueError("mask_strategy=existing 时必须提供 --mask")
        mask = np.asarray(nib.load(str(args.mask)).get_fdata()) > 0
    elif args.mask_strategy == "median_otsu":
        b0_indices = np.where(gtab.b0s_mask)[0].tolist() or [0]
        _, mask = median_otsu(data, vol_idx=b0_indices, median_radius=4, numpass=4)
    else:
        mask = None
    safe_data = np.maximum(data, args.min_signal)
    model_kwargs = {"fit_method": args.fit_method}
    if args.fit_method == "RESTORE":
        sigma = args.restore_sigma
        if sigma <= 0:
            sigma = float(np.mean(estimate_sigma(safe_data, N=4)))
        model_kwargs["sigma"] = sigma
    print(f"Fitting tensor model: {args.fit_method}")
    fit = dti.TensorModel(gtab, **model_kwargs).fit(safe_data, mask=mask)
    evals = np.asarray(fit.evals)
    if args.clip_negative_eigenvalues:
        evals = np.clip(evals, 0, None)
    metrics = {
        "fa": np.nan_to_num(dti.fractional_anisotropy(evals)),
        "md": np.nan_to_num(dti.mean_diffusivity(evals)),
        "ad": np.nan_to_num(dti.axial_diffusivity(evals)),
        "rd": np.nan_to_num(dti.radial_diffusivity(evals)),
    }
    for name, values in metrics.items():
        path = args.output_dir / f"dti_{name.upper()}.nii.gz"
        nib.save(nib.Nifti1Image(values.astype(np.float32), image.affine, image.header), str(path))
        print(f"Saved {path}")
    if args.save_eigenvectors:
        nib.save(
            nib.Nifti1Image(np.asarray(fit.evecs, dtype=np.float32), image.affine),
            str(args.output_dir / "dti_eigenvectors.nii.gz"),
        )


if __name__ == "__main__":
    main()
