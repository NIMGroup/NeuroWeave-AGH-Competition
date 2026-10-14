"""Run only tiny synthetic inputs in this isolated checkout; no dataset or Slurm access."""
from pathlib import Path
from threading import Event
import argparse,json,os,sys,subprocess
import numpy as np
import nibabel as nib
ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--work-dir',type=Path,required=True,help='New empty directory for synthetic inputs, outputs and report')
parser.add_argument('--record-evidence', action='store_true', help='Record successful synthetic checks against the current definitions and adapter code')
args=parser.parse_args()
work=args.work_dir.resolve()
if work.exists() and any(work.iterdir()):raise SystemExit('Refusing to reuse a nonempty work directory')
work.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(ROOT/'bakend'))
from app.execution.runners.mrtrix_commands import command_runners
from app.execution.base import RunnerContext
from app.execution.runners.mrtrix import MrtrixTckgenRunner
from app.workflow_models import Workflow,WorkflowStep,InputBinding

data=work/'synthetic-inputs';data.mkdir(exist_ok=True)
os.environ['LOCALFLOW_RUN_INPUT_ROOTS']=str(data)
os.environ['LOCALFLOW_DATA_ROOTS']=str(data)
os.environ['SLURM_CPUS_PER_TASK']='1'
rng=np.random.default_rng(42)
def save(name,array):
    path=data/name;nib.save(nib.Nifti1Image(array.astype('float32'),np.eye(4)),path);return path
scalar=save('scalar.nii.gz',rng.uniform(0,1,(12,12,12)))
dwi=save('dwi.nii.gz',rng.normal(100,5,(8,8,8,30)))
five=np.zeros((12,12,12,5));five[...,0]=.5;five[...,2]=.5
five_path=save('five.nii.gz',five)
fod=np.zeros((12,12,12,6));fod[...,0]=1;fod_path=save('fod.nii.gz',fod)
tracks=data/'tracks.tck'
streamlines=[np.array([[x,3+i*.1,5] for x in np.linspace(2,9,20)],dtype='float32') for i in range(30)]
nib.streamlines.save(nib.streamlines.Tractogram(streamlines,affine_to_rasmm=np.eye(4)),tracks)
config={
 'mrconvert':({'input':scalar},{'axes':'0,1,2'}),
 'mrcheckerboardmask':({'input':scalar},{}),
 'maskfilter':({'input':scalar},{'filter':'dilate'}),
 '5tt2vis':({'input':five_path},{}),
 'sh2power':({'SH':fod_path},{}),
 'mrhistogram':({'image':scalar},{}),
 'mrthreshold':({'input':scalar},{'mode':'abs','abs':.5}),
 'mrdegibbs':({'in':scalar},{'axes':'0,1'}),
 'dwidenoise':({'dwi':dwi},{'extent':'3,3,3','save_noise':True}),
 '5tt2gmwmi':({'5tt_in':five_path},{}),
 'tckresample':({'in_tracks':tracks},{'mode':'num_points','num_points':10}),
 'tcksift2':({'in_tracks':tracks,'in_fod':fod_path},{'min_iters':1,'max_iters':3,'save_out_mu':True}),
}
results=[]
for runner in command_runners():
    name=runner.contract['command']
    if name not in config:continue  # Coverage is reported explicitly; unknown commands are never guessed.
    bindings,params=config[name]
    output=work/'synthetic-outputs'/name;output.mkdir(parents=True,exist_ok=True)
    params={'nthreads':1,**params}
    step=WorkflowStep(id=name,order=1,title=name,description='',capability='skill.dwi-skill',status='confirmed',
        selected_method_id=runner.method.id,method_options=[runner.method],selected_parameters=params,
        input_bindings={key:InputBinding(slot_id=key,artifact_type=next(s.type for s in runner.method.input_slots if s.id==key),source='existing_path',absolute_path=str(path)) for key,path in bindings.items()})
    workflow=Workflow(workflow_id='synthetic-'+name,template_id='smoke',title=name,summary='',original_request='synthetic test',status='confirmed',steps=[step],created_at='',updated_at='')
    context=RunnerContext(run_id='synthetic-'+name,workflow=workflow,step=step,method=runner.method,output_directory=output,cancel_event=Event(),log=lambda *args:None)
    problems=runner.preflight(context)
    if problems:raise RuntimeError(name+': '+str(problems))
    result=runner.run(context)
    if name=='tckresample':assert all(len(s)==10 for s in nib.streamlines.load(output/'resampled.tck').streamlines)
    if name=='tcksift2':
        weights=np.loadtxt(output/'weights.txt');assert len(weights)==len(streamlines) and np.isfinite(weights).all()
    if name=='mrthreshold':
        converted=output/'validation.nii.gz'
        subprocess.run([str(runner.executable.parent/'mrconvert'),str(output/'threshold.mif'),str(converted),'-quiet'],check=True,env={**os.environ,**MrtrixTckgenRunner._environment()})
        actual=nib.load(converted).get_fdata()
        expected=nib.load(scalar).get_fdata()>=.5
        assert np.array_equal(actual,expected), 'Threshold output does not match the requested absolute threshold'
    results.append(dict(command=name,success=True,artifacts=len(result.artifacts)))

# Independent tracking output is consumed by the separate SIFT2 adapter.
# This is a synthetic execution check, not a scientific/ACT accuracy benchmark.
from app.capability_registry import CapabilityRegistry
from app.workflow_models import MethodOption
definition=CapabilityRegistry(ROOT/'skills').method_for('method','mrtrix.tckgen')
method=MethodOption(id=definition.method_id,**definition.model_dump(exclude={'method_id'}))
seed=save('seed.nii.gz',np.ones((12,12,12)))
output=work/'synthetic-outputs'/'tracking';output.mkdir(parents=True)
step=WorkflowStep(id='tracking',order=1,title='tracking',description='',capability='dwi.tractography',status='confirmed',
    selected_method_id=method.id,method_options=[method],selected_parameters={'act_enabled':False,'threads':1,
      'seeding_strategy':'roi','streamline_count':20,'min_length':1,'max_length':10,'algorithm':'iFOD2'},
    input_bindings={key:InputBinding(slot_id=key,artifact_type=kind,source='existing_path',absolute_path=str(path))
      for key,kind,path in [('fod','fod_image',fod_path),('seed_roi','seed_mask',seed)]})
workflow=Workflow(workflow_id='synthetic-chain',template_id='smoke',title='chain',summary='',original_request='synthetic test',status='confirmed',steps=[step],created_at='',updated_at='')
context=RunnerContext(run_id='synthetic-chain',workflow=workflow,step=step,method=method,output_directory=output,cancel_event=Event(),log=lambda *args:None)
tracking=MrtrixTckgenRunner()
assert not tracking.preflight(context)
result=tracking.run(context)
actual_tracks=output/'tracks.tck'
assert len(nib.streamlines.load(actual_tracks).streamlines)==20
assert len(result.artifacts)==1 and not (output/'weights.txt').exists()
sift=next(r for r in command_runners() if r.contract['command']=='tcksift2')
sift_output=work/'synthetic-outputs'/'tracking-weights';sift_output.mkdir()
sift_step=WorkflowStep(id='weights',order=2,title='weights',description='',capability='dwi.tractography',status='confirmed',
    selected_method_id=sift.method.id,method_options=[sift.method],selected_parameters={'nthreads':1,'min_iters':1,'max_iters':3},
    input_bindings={'in_tracks':InputBinding(slot_id='in_tracks',artifact_type='tractogram',source='upstream_output',
        upstream_step_id='tracking',output_slot_id='tractogram',absolute_path=str(actual_tracks)),
      'in_fod':InputBinding(slot_id='in_fod',artifact_type='fod_image',source='existing_path',absolute_path=str(fod_path))})
# Use the same run root for upstream-output authorization as the production runner.
from app.execution.run_service import RunService
from app.run_models import RunStep
from types import SimpleNamespace
RunService._annotate_output_slots(method,result.artifacts)
completed=RunStep(step_id='tracking',title='tracking',runner_id=method.runner_id,method_id=method.id,status='succeeded',artifacts=result.artifacts)
sift_step=RunService._resolve_runtime_bindings(sift_step,SimpleNamespace(steps=[completed]))
workflow.steps.append(sift_step)
sift_context=RunnerContext(run_id='synthetic-chain',workflow=workflow,step=sift_step,method=sift.method,
    output_directory=sift_output,cancel_event=Event(),log=lambda *args:None)
problems=sift.preflight(sift_context)
assert not problems, problems
sift.run(sift_context)
weights=np.loadtxt(sift_output/'weights.txt')
assert len(weights)==20 and np.isfinite(weights).all()
results.append(dict(command='tckgen -> independent tcksift2',success=True,streamlines=20,weights=len(weights)))

print(json.dumps(results,ensure_ascii=False),flush=True)
(work/'synthetic-smoke.json').write_text(json.dumps(results,indent=2),encoding='utf-8')

if args.record_evidence:
    from datetime import datetime, timezone
    import hashlib
    from app.method_registry import MethodRegistry
    from app.method_validation import execution_fingerprint
    store = MethodRegistry(ROOT); store.load()
    tested_at = datetime.now(timezone.utc).isoformat()
    report_dir = ROOT/'deploy/method-verification/reports'
    report_dir.mkdir(parents=True, exist_ok=True)
    report = report_dir/'mrtrix-synthetic.json'
    version = subprocess.run([str(tracking.executable), '-version'], capture_output=True, text=True, check=True).stdout.strip()
    environment = version.splitlines()[0] + '; Linux CPU, 1 thread'
    report.write_text(json.dumps(dict(tested_at=tested_at, environment=environment, checks=results), indent=2)+'\n', encoding='utf-8')
    report_hash = hashlib.sha256(report.read_bytes()).hexdigest()
    identities = ['mrtrix.command.'+name for name in config] + ['mrtrix.tckgen']
    for identity in identities:
        method = store.method(identity)
        record = dict(method_id=identity, method_version=method.version,
            execution_fingerprint=execution_fingerprint(method, ROOT),
            report_path=report.relative_to(ROOT).as_posix(), report_sha256=report_hash,
            test_type='synthetic', tested_at=tested_at, environment=environment, result='passed')
        (report_dir.parent/(identity+'.json')).write_text(json.dumps(record, indent=2)+'\n', encoding='utf-8')
    print('Recorded version-bound synthetic execution evidence:', len(identities), flush=True)
