"""Fixed neuroimaging command adapters. No shell or user-provided executable."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import subprocess

FSL=Path('/opt/software/fsl')
MR=Path('/opt/software/mrtrix3-master/bin')
ANTS=Path('/opt/software/ants-2.6.5/bin')


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--operation',required=True,choices=['bet','fast','mcflirt','slicetimer','fnirt','applywarp','dwidenoise','mrdegibbs','mrconvert','dwibiascorrect','dwifslpreproc','ants-apply'])
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--reference',type=Path)
    parser.add_argument('--transform',type=Path)
    parser.add_argument('--bval',type=Path)
    parser.add_argument('--bvec',type=Path)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--fraction',type=float,default=.5)
    parser.add_argument('--tr',type=float,default=2)
    parser.add_argument('--slice-axis',type=int,default=3)
    parser.add_argument('--slice-order',choices=['ascending','descending','interleaved'],default='ascending')
    parser.add_argument('--pe-dir',choices=['AP','PA','LR','RL','IS','SI'],default='AP')
    parser.add_argument('--readout',type=float,default=.05)
    parser.add_argument('--classes',type=int,default=3)
    parser.add_argument('--interp',choices=['linear','nearest'],default='linear')
    args=parser.parse_args(argv)
    out=args.output_dir; out.mkdir(parents=True,exist_ok=True)
    op=args.operation
    src=str(args.input); result=str(out/'image.nii.gz')
    mrtrix=op in {'dwidenoise','mrdegibbs','mrconvert','dwibiascorrect','dwifslpreproc'}
    if mrtrix:result=str(out/'working.mif')
    if op=='bet': cmd=[str(FSL/'bin/bet'),src,result,'-m','-f',str(args.fraction)]
    elif op=='fast': cmd=[str(FSL/'bin/fast'),'-n',str(args.classes),'-o',str(out/'tissue'),src]
    elif op=='mcflirt': cmd=[str(FSL/'bin/mcflirt'),'-in',src,'-out',result,'-plots','-mats']
    elif op=='slicetimer':
        cmd=[str(FSL/'bin/slicetimer'),'-i',src,'-o',result,'-r',str(args.tr),'-d',str(args.slice_axis)]
        if args.slice_order=='descending':cmd.append('--down')
        if args.slice_order=='interleaved':cmd.append('--odd')
    elif op in {'fnirt','applywarp'}:
        if not args.reference: raise ValueError('Reference image required')
        cmd=[str(FSL/'bin'/op),'--in='+src,'--ref='+str(args.reference)]
        if op=='fnirt':
            cmd+=['--iout='+result,'--cout='+str(out/'warp.nii.gz')]
            if args.transform:cmd+=['--aff='+str(args.transform)]
        else:
            if not args.transform: raise ValueError('Warp image required')
            cmd+=['--out='+result,'--warp='+str(args.transform),'--interp='+('nn' if args.interp=='nearest' else 'trilinear')]
    elif op=='ants-apply':
        if not args.reference or not args.transform: raise ValueError('Reference and transform required')
        cmd=[str(ANTS/'antsApplyTransforms'),'-d','3','-i',src,'-r',str(args.reference),'-t',str(args.transform),'-o',result,'-n','NearestNeighbor' if args.interp=='nearest' else 'Linear']
    else:
        cmd=[str(MR/op)]
        if op=='dwibiascorrect':cmd+=['ants']
        cmd+=[src,result,'-nthreads',os.getenv('SLURM_CPUS_PER_TASK','1')]
        if args.bval or args.bvec:
            if not args.bval or not args.bvec: raise ValueError('Both bval and bvec required')
            cmd+=['-fslgrad',str(args.bvec),str(args.bval)]
        if op=='dwifslpreproc':
            cmd+=['-rpe_none','-pe_dir',args.pe_dir,'-readout_time',str(args.readout)]
        if op in {'dwifslpreproc','dwibiascorrect'}:cmd+=['-scratch',str(out/'scratch')]
    env={**os.environ,'FSLDIR':str(FSL),'FSLOUTPUTTYPE':'NIFTI_GZ','ANTSPATH':str(ANTS),
         'PATH':os.pathsep.join([str(FSL/'bin'),str(MR),str(ANTS),os.environ.get('PATH','')]),
         'ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS':os.getenv('SLURM_CPUS_PER_TASK','1')}
    print('Running:',cmd,flush=True)
    subprocess.run(cmd,check=True,env=env,cwd=out)
    if mrtrix:
        convert=[str(MR/'mrconvert'),result,str(out/'image.nii.gz')]
        if args.bval:
            if op!='dwifslpreproc':
                # These operations do not rotate volumes. N4/mrcalc can drop
                # the gradient header, so restore the unchanged input scheme.
                convert+=['-fslgrad',str(args.bvec),str(args.bval)]
            convert+=['-export_grad_fsl',str(out/'dwi.bvec'),str(out/'dwi.bval')]
        subprocess.run(convert,check=True,env=env,cwd=out)
        (out/'working.mif').unlink()


if __name__=='__main__': main()
