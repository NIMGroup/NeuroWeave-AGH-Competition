"""Additional fixed calls documented by the original Skills.

No command, executable, shell fragment or output path is accepted from the model.
FSL's own shell backend runs its slices inside the allocated Slurm job.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess

FSL = Path('/opt/software/fsl')
MR = Path('/opt/software/mrtrix3-master/bin')


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--operation', choices=['sift', 'fsl-anat', 'bedpostx', 'probtrackx'], required=True)
    for name in ['input', 'fod', 'five-tt', 'mask', 'bval', 'bvec', 'samples', 'seed']:
        p.add_argument('--' + name, type=Path)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--term-number', type=int, default=1000)
    p.add_argument('--fibres', type=int, default=2)
    p.add_argument('--burnin', type=int, default=1000)
    p.add_argument('--jumps', type=int, default=1250)
    p.add_argument('--sample-every', type=int, default=25)
    p.add_argument('--nsamples', type=int, default=5000)
    p.add_argument('--nsteps', type=int, default=2000)
    p.add_argument('--step-length', type=float, default=.5)
    p.add_argument('--curvature', type=float, default=.2)
    a = p.parse_args(argv)
    out = a.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    cpus = str(max(1, int(os.getenv('SLURM_CPUS_PER_TASK', '1'))))
    env = {**os.environ, 'FSLDIR': str(FSL), 'FSLOUTPUTTYPE': 'NIFTI_GZ',
           'PATH': os.pathsep.join([str(FSL/'bin'), str(MR), os.environ.get('PATH', '')]),
           'OMP_NUM_THREADS': cpus, 'FSLSUB_PARALLEL': cpus}
    if a.operation in {'fsl-anat', 'bedpostx'}:
        # Both upstream workflows delegate to fsl_sub. Keep those subprocesses
        # inside this allocation, so cancellation and resource limits still apply.
        config = out/'fsl_sub.yml'
        config.write_text('method: shell\n', encoding='utf-8')
        env['FSLSUB_CONF'] = str(config)
        queues = subprocess.run([str(FSL/'bin/fsl_sub'), '--has_queues'], env=env, capture_output=True)
        if queues.returncode != 1:
            raise RuntimeError('FSL shell execution could not be verified')
    if a.operation == 'sift':
        if not a.input or not a.fod:
            raise ValueError('SIFT requires an existing tractogram and FOD')
        cmd = [str(MR/'tcksift'), str(a.input), str(a.fod), str(out/'tracks.tck'),
               '-term_number', str(a.term_number), '-nthreads', cpus]
        if a.five_tt:
            cmd += ['-act', str(a.five_tt)]
    elif a.operation == 'fsl-anat':
        if not a.input:
            raise ValueError('fsl_anat requires a T1w image')
        cmd = [str(FSL/'bin/fsl_anat'), '-i', str(a.input), '-o', str(out/'structural')]
    elif a.operation == 'bedpostx':
        if not all([a.input, a.mask, a.bval, a.bvec]):
            raise ValueError('bedpostx requires DWI, mask, bval and bvec')
        # All normalization/copies are I/O only; FSL retains its original model.
        import nibabel as nib
        data = out/'data'
        data.mkdir()
        nib.save(nib.load(a.input), data/'data.nii.gz')
        nib.save(nib.load(a.mask), data/'nodif_brain_mask.nii.gz')
        shutil.copyfile(a.bval, data/'bvals')
        shutil.copyfile(a.bvec, data/'bvecs')
        cmd = [str(FSL/'bin/bedpostx'), str(data), '-n', str(a.fibres), '-b', str(a.burnin),
               '-j', str(a.jumps), '-s', str(a.sample_every)]
    else:
        if not a.samples or not a.mask or not a.seed:
            raise ValueError('probtrackx requires existing bedpostx samples, brain mask and seed mask')
        samples = a.samples/'data.bedpostX' if (a.samples/'data.bedpostX').is_dir() else a.samples
        for name in ['merged_th1samples', 'merged_ph1samples', 'merged_f1samples']:
            if not any((samples/(name+ext)).is_file() for ext in ['.nii.gz', '.nii']):
                raise ValueError('Incomplete bedpostx output: ' + name)
        cmd = [str(FSL/'bin/probtrackx2'), '-s', str(samples/'merged'), '-m', str(a.mask),
               '-x', str(a.seed), '--dir='+str(out), '--forcedir', '--opd', '--loopcheck',
               '--nsamples='+str(a.nsamples), '--nsteps='+str(a.nsteps),
               '--steplength='+str(a.step_length), '--cthr='+str(a.curvature)]
    print('Running:', cmd, flush=True)
    subprocess.run(cmd, check=True, env=env, cwd=out)
    # FSL may link its own copied inputs. Materialize only in-bound file links so
    # exported historical results are self-contained and never follow user paths.
    for path in list(out.rglob('*')):
        if not path.is_symlink():
            continue
        target = path.resolve(strict=True)
        if not target.is_relative_to(out) or not target.is_file():
            raise RuntimeError('FSL output link escapes its workspace')
        path.unlink()
        shutil.copyfile(target, path)


if __name__ == '__main__':
    main()
