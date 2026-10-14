"""Read generated scientific files without deserializing pickle or model checkpoints."""
from __future__ import annotations
import csv
import json
from pathlib import Path
import sys
import numpy as np


def reject_constant(value):
    raise ValueError('Non-finite JSON value: '+value)


def validate(root):
    import nibabel as nib
    if (root/'dwi.bval').exists() or (root/'dwi.bvec').exists():
        bval=np.loadtxt(root/'dwi.bval').reshape(-1);bvec=np.loadtxt(root/'dwi.bvec')
        image=nib.load(root/'image.nii.gz')
        if len(image.shape)!=4 or len(bval)!=image.shape[3] or bvec.shape!=(3,len(bval)):
            raise ValueError('DWI image and exported gradient dimensions differ')
        if not np.isfinite(bval).all() or not np.isfinite(bvec).all():raise ValueError('Non-finite gradients')
    for path in root.rglob('*'):
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('Unexpected output link')
        if not path.is_file() or not path.stat().st_size:continue
        if path.name.endswith(('.nii','.nii.gz')):
            img=nib.load(path)
            for index in range(img.shape[-1] if len(img.shape)==4 else 1):
                data=np.asarray(img.dataobj[...,index] if len(img.shape)==4 else img.dataobj)
                if not np.isfinite(data).all():raise ValueError('Non-finite image: '+path.name)
        elif path.suffix=='.npy':
            data=np.load(path,allow_pickle=False,mmap_mode='r')
            if data.dtype.kind in 'fc' and not np.isfinite(data).all():raise ValueError('Non-finite array: '+path.name)
        elif path.suffix=='.json':json.loads(path.read_text(encoding='utf-8'),parse_constant=reject_constant)
        elif path.suffix in {'.csv','.tsv'}:
            with path.open(encoding='utf-8-sig',newline='') as handle:
                reader=csv.reader(handle,delimiter='\t' if path.suffix=='.tsv' else ',')
                if not next(reader,None):raise ValueError('Table lacks a header: '+path.name)
    print('Product validation passed')


if __name__=='__main__':validate(Path(sys.argv[1]))
