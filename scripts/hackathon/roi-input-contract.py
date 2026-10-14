"""AGH acceptance/oracle input contract, independent of the business ROI script.

The 3003 method implementation exposes roi_stats only. Keep verifier validation
here so copying that implementation does not remove the AGH scientific checks.
"""
import gzip
import math
from pathlib import Path

import nibabel as nib
import numpy as np

DEFAULT_LIMITS = {'file_bytes': 512 * 1024 * 1024, 'expanded_bytes': 512 * 1024 * 1024,
                  'voxels': 64_000_000, 'labels': 4096}
COLUMNS = ['label', 'label_name', 'n_vox', 'mean', 'median', 'std', 'p05', 'p95']


def bounded_image(path: Path, *, limits=None):
    limits = DEFAULT_LIMITS if limits is None else limits
    if not path.is_file():
        raise ValueError('missing_file')
    if path.stat().st_size > limits['file_bytes']:
        raise ValueError('file_too_large')
    opener = gzip.open if path.name.endswith('.nii.gz') else open
    try:
        with opener(path, 'rb') as stream:
            raw = stream.read(limits['expanded_bytes'] + 1)
        if len(raw) > limits['expanded_bytes']:
            raise ValueError('expanded_file_too_large')
        image = nib.Nifti1Image.from_bytes(raw)
        if len(image.shape) != 3 or not all(image.shape) or math.prod(image.shape) > limits['voxels']:
            raise ValueError('volume_limit_or_not_3d')
        if not np.isfinite(image.affine).all() or abs(np.linalg.det(image.affine[:3, :3])) < 1e-12:
            raise ValueError('invalid_affine')
        return image, image.get_fdata(dtype=np.float64)
    except (OSError, EOFError, nib.filebasedimages.ImageFileError, nib.wrapstruct.WrapStructError, ValueError) as exc:
        raise ValueError(f'invalid_nifti: {exc}') from exc


def validated_arrays(metric_path: Path, roi_path: Path, *, limits=None):
    limits = DEFAULT_LIMITS if limits is None else limits
    metric_image, metric = bounded_image(metric_path, limits=limits)
    roi_image, roi = bounded_image(roi_path, limits=limits)
    if metric.shape != roi.shape:
        raise ValueError('shape_mismatch')
    if not np.allclose(metric_image.affine, roi_image.affine, rtol=0, atol=1e-5):
        raise ValueError('affine_mismatch: no implicit resampling is permitted')
    if not np.isfinite(roi).all() or np.any(roi < 0) or np.any(roi != np.floor(roi)) or np.any(roi > 2**31 - 1):
        raise ValueError('invalid_noninteger_or_negative_labels')
    if len(np.unique(roi)) > limits['labels']:
        raise ValueError('too_many_labels')
    return metric_image, metric, roi.astype(np.int32)
