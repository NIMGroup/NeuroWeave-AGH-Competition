"""Real Slurm/RunService checks against synthetic inputs and a separate database."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'bakend'))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True);parser.add_argument('--inputs',type=Path,required=True);parser.add_argument('--only',default='')
    args=parser.parse_args();root=args.root.resolve();data=args.inputs.resolve(strict=True)
    base=Path('/home/data/neuroweave/development').resolve()
    if root.parent!=base or root.exists() or not data.is_relative_to(base):raise ValueError('Use a new isolated development directory and synthetic inputs')
    root.mkdir(mode=0o700);(root/'runs').mkdir(mode=0o700)
    os.environ.update(LOCALFLOW_SLURM_ENABLED='true',LOCALFLOW_SLURM_WORKER_ROOT=str(ROOT),LOCALFLOW_SLURM_OUTPUT_ROOT=str(root/'runs'),
        LOCALFLOW_SLURM_PYTHON='/opt/neuroweave/envs/scientific-py311/bin/python',LOCALFLOW_RUN_INPUT_ROOTS=";".join([str(data),str(root)]),
        LOCALFLOW_SKILL_PYTHON='/opt/neuroweave/envs/scientific-py311/bin/python',LOCALFLOW_TORCH_PYTHON='/opt/neuroweave/envs/torch-cu128-py311/bin/python')
    from smoke_slurm_execution import SmokeSuite
    from smoke_reviewed_execution import case_values
    from app.execution.runners.reviewed import specifications
    from app.workflow_models import InputBinding,MethodOption,WorkflowStep
    from app.run_models import RunExecutionOptions
    from app.execution.slurm_backend import SlurmBackend
    from unittest.mock import patch
    original_command=SlurmBackend._command
    def compute_nodes(argv):
        return original_command([*argv[:-1],'--exclude=mgr',argv[-1]] if argv and argv[0]=='sbatch' else argv)
    dispatch=patch.object(SlurmBackend,'_command',staticmethod(compute_nodes));dispatch.start()
    suite=SmokeSuite(root,'all');service=suite.new_service();records=[]
    cases=[('bold_chain',['reviewed.nilearn-tool.denoise','reviewed.nilearn-tool.task-glm'],0,2),
           ('cnn_cpu',['reviewed.cnn3d.train'],0,2),('cnn_gpu',['reviewed.cnn3d.train'],1,2),
           ('eddy_cpu',['reviewed.dwi-skill.dwifslpreproc'],0,8)]
    try:
        for name,runners,gpus,cpus in cases:
            if args.only and name not in args.only.split(","):continue
            record={'case':name,'passed':False};run=None
            try:
                workflow=suite.fixture(name);steps=[]
                for index,rid in enumerate(runners):
                    spec=specifications()[rid];paths,values=case_values(spec,data)
                    if any(p['id']=='device' for p in spec['parameters']):values['device']='auto'
                    definition=next(m for m in suite.capabilities.methods_for('skill.'+spec['skill_id']) if m.runner_id==rid)
                    method=MethodOption(id=definition.method_id,**{k:getattr(definition,k) for k in ('skill_id','label','description','runner_id','execution_mode','parameters','input_types','output_types','input_slots','output_slots','structure_level')})
                    bindings={key:InputBinding(slot_id=key,artifact_type=next(s.type for s in method.input_slots if s.id==key),source='existing_path',absolute_path=str(path)) for key,path in paths.items()}
                    if name=='bold_chain' and index==1:
                        bindings['bold']=InputBinding(slot_id='bold',artifact_type='bold_image',source='upstream_output',upstream_step_id='analysis0',output_slot_id='file_cleaned_bold_nii_gz')
                    steps.append(WorkflowStep(id='analysis'+str(index),order=index+1,title=spec['label'],description='Synthetic Slurm execution',capability='skill.'+spec['skill_id'],status='confirmed',method_options=[method],selected_method_id=method.id,selected_parameters=values,input_bindings=bindings))
                workflow.steps=steps;workflow.requested_capabilities=[s.capability for s in steps]
                suite.store.save_workflow(workflow,event_type='workflow_confirmed')
                options=RunExecutionOptions(backend='slurm',partition='all',cpus=cpus,memory_gb=8,gpus=gpus,time_minutes=20)
                run=service.create(workflow.workflow_id,workflow.revision,options);record['run_id']=run.run_id
                print(json.dumps({'started':name,'run_id':run.run_id}),flush=True)
                deadline=time.monotonic()+1250
                while time.monotonic()<deadline:
                    run=service.get(run.run_id)
                    if run.status in {'succeeded','failed','cancelled'}:break
                    time.sleep(.5)
                if run.status!='succeeded':raise AssertionError(str(run.status)+': '+str(run.error_message))
                record['jobs']=[s.scheduler for s in run.steps]
                record['artifacts']=len(run.artifacts)
                configs=[Path(a.absolute_path) for a in run.artifacts if Path(a.absolute_path).name=='config.json']
                if configs:
                    device=json.loads(configs[0].read_text())['device']
                    metrics=next((Path(a.absolute_path) for a in run.artifacts if Path(a.absolute_path).name=='metrics.json'),None)
                    if metrics:device=json.loads(metrics.read_text()).get('device',device)
                    record['device']=device
                    if device!=('cuda' if gpus else 'cpu'):raise AssertionError('Unexpected training device: '+device)
                if name=='bold_chain':
                    zmap=next(a for a in run.artifacts if Path(a.absolute_path).name=='z_map.nii.gz')
                    record['z_map']=zmap.absolute_path
                record['passed']=True
            except Exception as exc:
                record['error']=str(exc)
                if run and service.get(run.run_id).status not in {'succeeded','failed','cancelled'}:service.cancel(run.run_id)
            records.append(record);(root/'evidence.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
            print(json.dumps(record,ensure_ascii=False),flush=True)
    finally:
        service.shutdown();dispatch.stop()
    return int(any(not r['passed'] for r in records))


if __name__=='__main__':raise SystemExit(main())
