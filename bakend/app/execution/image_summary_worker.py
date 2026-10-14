"""I/O adapters for existing NiBabel image and FreeSurfer surface readers."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import nibabel as nib
import numpy as np


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--operation',choices=['inspect','surface','save'],required=True)
    parser.add_argument('--image',type=Path)
    parser.add_argument('--surf',type=Path)
    parser.add_argument('--annot',type=Path)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args();out=args.output_dir;out.mkdir(parents=True,exist_ok=True)
    if args.operation=='save':
        # NiBabel handles the format conversion; preserve voxel data and geometry.
        nib.save(nib.load(args.image),out/'image.nii.gz')
        return
    if args.operation=='inspect':
        img=nib.load(args.image)
        info={'shape':list(img.shape),'zooms':list(map(float,img.header.get_zooms())),
              'affine':img.affine.tolist(),'orientation':list(nib.aff2axcodes(img.affine)),'dtype':str(img.get_data_dtype())}
        (out/'inspection.json').write_text(json.dumps(info,indent=2));return
    if args.operation=='surface':
        vertices,faces=nib.freesurfer.read_geometry(args.surf)
        labels,colors,names=nib.freesurfer.read_annot(args.annot)
        if len(vertices)!=len(labels):raise ValueError('Annotation and surface vertex counts differ')
        np.savez_compressed(out/'surface.npz',vertices=vertices,faces=faces,labels=labels,colors=colors,names=np.array([n.decode() for n in names]));return


if __name__=='__main__':main()
