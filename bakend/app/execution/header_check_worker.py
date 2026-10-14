"""Bounded header-only NiBabel reader; no voxel analysis or new algorithm."""
import json,sys
try:
    import nibabel as nib
    import numpy as np
except ImportError:
    print(json.dumps({'status':'not_checked','code':'reader_unavailable'}));raise SystemExit(0)
try:
    image=nib.load(sys.argv[1],mmap=True)
    shape=list(image.shape);zooms=list(map(float,image.header.get_zooms()));affine=image.affine
    if len(shape)<3 or any(n<=0 for n in shape) or not np.isfinite(affine).all() or any(not np.isfinite(v) or v<=0 for v in zooms):
        raise ValueError('Invalid image geometry')
    if abs(float(np.linalg.det(affine[:3,:3])))<1e-12:raise ValueError('Singular affine')
    print(json.dumps(dict(status='passed',code='header_readable',shape=shape,zooms=zooms,
                         affine=affine.tolist(),dtype=str(image.get_data_dtype()))))
except Exception as exc:
    print(json.dumps(dict(status='error',code='invalid_header',error_type=type(exc).__name__)))
