"""Isolated synthetic regression for documented FSL/FreeSurfer flags.

No dataset, database, Slurm or service operations. A new empty work directory is
required; only explicit registered Runner parameters are passed to native tools.
"""
import argparse,hashlib,json,os,sys
from datetime import datetime,timezone
from pathlib import Path
from threading import Event,Timer
import numpy as np
import nibabel as nib

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'bakend'))
from app.execution.runners.scientific_commands import scientific_runners
from app.execution.base import RunnerContext
from app.workflow_models import Workflow,WorkflowStep,InputBinding
from app.method_registry import MethodRegistry
from app.method_validation import execution_fingerprint

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--work-dir',type=Path,required=True);p.add_argument('--record-evidence',action='store_true');args=p.parse_args()
 work=args.work_dir.resolve()
 if work.exists():raise SystemExit('Use a new synthetic work directory')
 work.mkdir(parents=True);data=work/'inputs';data.mkdir()
 os.environ.update(LOCALFLOW_RUN_INPUT_ROOTS=str(work),LOCALFLOW_DATA_ROOTS=str(work),SLURM_CPUS_PER_TASK='1',OMP_NUM_THREADS='1',ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS='1')
 rng=np.random.default_rng(19)
 def save(name,array):
  path=data/name;nib.save(nib.Nifti1Image(array.astype('float32'),np.eye(4)),path);return path
 volume=save('image.nii.gz',rng.uniform(-60,60,(8,8,8)))
 mask=save('mask.nii.gz',(np.indices((8,8,8))[0]>3).astype(float))
 series=save('series.nii.gz',50+rng.normal(0,3,(8,8,8,10)))
 design=data/'design.txt';np.savetxt(design,np.column_stack([np.linspace(-1,1,10),rng.normal(size=10)]))
 cases={
  'fsl.command.fsl_glm':({'input':series,'design':design,'mask':mask},{'save_residuals':True},['des_norm','dat_norm','vn','demean']),
  'freesurfer.command.mri_binarize':({'input':volume},{'min':30},['abs','inv','no_count','bincol','zero_edges','zero_slice_edges']),
  'freesurfer.command.mri_mask':({'input':volume,'mask':mask},{},['abs','invert','samseg']),
 }
 report={'tested_at':datetime.now(timezone.utc).isoformat(),'environment':'mgr Linux CPU, 1 thread; isolated synthetic inputs','checks':[]}
 for runner in scientific_runners():
  if runner.runner_id not in cases:continue
  files,base,flags=cases[runner.runner_id]
  for flag in [None,*flags]:
   parameters={**base,**({flag:True} if flag else {})};out=work/'outputs'/runner.runner_id/(flag or 'default');out.mkdir(parents=True)
   step=WorkflowStep(id='test',order=1,title=runner.runner_id,description='',capability='method',status='confirmed',selected_method_id=runner.method.id,method_options=[runner.method],selected_parameters=parameters,
      input_bindings={key:InputBinding(slot_id=key,artifact_type=next(s.type for s in runner.method.input_slots if s.id==key),source='existing_path',absolute_path=str(value)) for key,value in files.items()})
   workflow=Workflow(workflow_id='synthetic-flags',template_id='test',title='Synthetic flags',summary='',original_request='Synthetic regression',status='confirmed',steps=[step],created_at='',updated_at='')
   cancel=Event();timer=Timer(45,cancel.set)
   ctx=RunnerContext(run_id='synthetic-flags',workflow=workflow,step=step,method=runner.method,output_directory=out,cancel_event=cancel,log=lambda *args:None)
   row={'method_id':runner.runner_id,'variant':flag or 'default'}
   try:
    problems=runner.preflight(ctx)
    if problems:raise ValueError('; '.join(problems))
    timer.start();result=runner.run(ctx)
    images=list(out.glob('*.nii.gz'));assert images
    for image in images:assert np.isfinite(nib.load(image).get_fdata()).all()
    if runner.runner_id.endswith('mri_binarize') and flag in (None,'abs','inv','zero_edges','zero_slice_edges'):
     source=nib.load(volume).get_fdata();expected=(np.abs(source) if flag=='abs' else source)>=30
     if flag=='inv':expected=~expected
     if flag in ('zero_edges','zero_slice_edges'):
      expected[:,:,0]=False;expected[:,:,-1]=False
      if flag=='zero_edges':expected[0,:,:]=False;expected[-1,:,:]=False;expected[:,0,:]=False;expected[:,-1,:]=False
     np.testing.assert_array_equal(nib.load(images[0]).get_fdata(),expected)
    row.update(status='passed',artifacts=len(result.artifacts))
   except Exception as exc:row.update(status='failed',error=str(exc)[-1200:])
   finally:timer.cancel()
   report['checks'].append(row);print(json.dumps(row),flush=True)
 (work/'report.json').write_text(json.dumps(report,indent=2)+'\n')
 if args.record_evidence:
  path=ROOT/'deploy/method-verification/reports/catalog-flags-synthetic.json';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,indent=2)+'\n')
  registry=MethodRegistry(ROOT)
  for identity in cases:
   method=registry.method(identity);checks=[c for c in report['checks'] if c['method_id']==identity]
   evidence={'method_id':identity,'method_version':method.version,'execution_fingerprint':execution_fingerprint(method,ROOT),'report_path':path.relative_to(ROOT).as_posix(),'report_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'test_type':'synthetic','tested_at':report['tested_at'],'environment':report['environment'],'result':'passed' if checks and all(c['status']=='passed' for c in checks) else 'failed'}
   (path.parent.parent/(identity+'.json')).write_text(json.dumps(evidence,indent=2)+'\n')
 if any(c['status']!='passed' for c in report['checks']):raise SystemExit(1)
 print(json.dumps({'methods':len(cases),'variants_passed':len(report['checks'])}))
if __name__=='__main__':main()
