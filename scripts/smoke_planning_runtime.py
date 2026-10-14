"""Explicit synthetic Slurm checks for the planning runtime additions."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
SCIENTIFIC='/opt/neuroweave/envs/scientific-py311/bin/python'
TORCH='/opt/neuroweave/envs/torch-cu128-py311/bin/python'


def images(data):
    import numpy as np
    import nibabel as nib
    import mne
    import pandas as pd
    rng=np.random.default_rng(510)
    n=20
    grid=np.indices((n,n,n))
    radius=np.sqrt(((grid-(n-1)/2)**2).sum(axis=0))
    wm=radius<5; gm=(radius>=5)&(radius<7); csf=(radius>=7)&(radius<8.5)
    mask=wm|gm|csf
    def save(name,values):
        nib.save(nib.Nifti1Image(values.astype('float32'),np.diag([2.,2.,2.,1.])),data/name)
    bvals=np.r_[np.zeros(6),np.full(45,1000),np.full(45,2000)]
    bvecs=rng.normal(size=(3,len(bvals)))
    bvecs/=np.linalg.norm(bvecs,axis=0); bvecs[:,:6]=0
    signal=wm[...,None]*np.exp(-bvals*(.0003+.0014*bvecs[0]**2))
    signal+=gm[...,None]*np.exp(-bvals*.0008)+csf[...,None]*np.exp(-bvals*.003)
    signal=(signal*1000+rng.normal(size=signal.shape)*2)*mask[...,None]
    save('dwi.nii.gz',signal)
    save('mask.nii.gz',mask)
    save('t1.nii.gz',wm*100+gm*70+csf*20)
    save('5tt.nii.gz',np.stack([gm,gm*0,wm,csf,wm*0],axis=-1))
    save('atlas.nii.gz',mask*np.where(grid[0]<n/2,1,2))
    save('seed.nii.gz',wm&(grid[0]==n//2))
    save('bold.nii.gz',(100+rng.normal(size=(n,n,n,100))*5)*mask[...,None])
    np.savetxt(data/'dwi.bval',bvals[None,:]); np.savetxt(data/'dwi.bvec',bvecs)
    (data/'labels.txt').write_text('left\nright\n')
    info=mne.create_info(['Fp1','Fp2','F3','F4','C3','C4','O1','O2'],200,'eeg')
    raw=mne.io.RawArray(rng.normal(scale=1e-6,size=(8,8000)),info,verbose='ERROR')
    raw.set_montage('standard_1020')
    raw.save(data/'eeg-raw.fif',overwrite=True,verbose='ERROR')
    epochs=mne.make_fixed_length_epochs(raw,duration=2,preload=True,verbose='ERROR')
    epochs.save(data/'eeg-epo.fif',overwrite=True)
    events=mne.make_fixed_length_events(raw,duration=2)
    events[:,2]=np.arange(len(events))%2+1
    mne.Epochs(raw,events,event_id={'A':1,'B':2},tmin=0,tmax=1.995,baseline=None,preload=True,verbose='ERROR').save(data/'classes-epo.fif',overwrite=True)
    pd.DataFrame({'subject_id':['001','002','003'],'value':[1,2,3]}).to_csv(data/'left.csv',index=False)
    pd.DataFrame({'subject_id':['001','002','003'],'age':[30,40,50]}).to_csv(data/'right.csv',index=False)
    (data/'mapping.json').write_text(json.dumps({'t1.nii.gz':'sub-001/anat/sub-001_T1w.nii.gz'}))
    np.save(data/'features.npy',rng.normal(size=(24,4)))
    pd.DataFrame({'subject_id':[str(i) for i in range(24)],'site':['A']*12+['B']*12,
        'dataset':'synthetic','age':np.arange(24)+25,'sex':np.arange(24)%2}).to_csv(data/'meta.csv',index=False)


def tensors(data):
    import torch
    import pandas as pd
    folder=data/'fc'/'synthetic';folder.mkdir(parents=True)
    torch.manual_seed(42)
    for i in range(12):
        matrix=torch.randn(8,8)*.2; matrix=(matrix+matrix.T)/2
        torch.save({'fc_matrix':matrix,'n_rois':8,'roi_names':['ROI_'+str(j) for j in range(8)]},folder/f'sub-{i:03d}.pt')
    pd.DataFrame({'subject_id':[f'{i:03d}' for i in range(12)],'label':[i%2 for i in range(12)]}).to_csv(data/'training.csv',index=False)


def run(root, only=''):
    data=root/'inputs'
    os.environ.update(LOCALFLOW_SLURM_ENABLED='true',LOCALFLOW_SLURM_WORKER_ROOT=str(ROOT),
        LOCALFLOW_SLURM_OUTPUT_ROOT=str(root/'runs'),LOCALFLOW_SLURM_PYTHON=SCIENTIFIC,
        LOCALFLOW_RUN_INPUT_ROOTS=';'.join([str(data),str(root)]),LOCALFLOW_DATA_ROOTS=str(data),
        LOCALFLOW_SKILL_PYTHON=SCIENTIFIC,LOCALFLOW_TORCH_PYTHON=TORCH,
        LOCALFLOW_SLURM_POLL_SECONDS='0.5')
    sys.path.insert(0,str(ROOT/'bakend'))
    from smoke_slurm_execution import SmokeSuite
    from app.workflow_models import InputBinding,MethodOption,WorkflowStep
    from app.dataset_models import DatasetSample
    from app.historical_inputs import HistoricalInputs
    from app.run_models import RunExecutionOptions
    suite=SmokeSuite(root,'all'); service=suite.new_service()
    records=json.loads((root/'evidence.json').read_text()) if (root/'evidence.json').is_file() else []
    cases=[
        ('nifti','skill.nibabel-skill','reviewed-save-existing-nibabel-skill',{'image':'t1.nii.gz'},{},0),
        ('evoked','skill.meg-skill','reviewed-evoked-existing-meg-skill',{'input':'eeg-epo.fif'},{},0),
        ('bands','skill.mne-eeg-tool','reviewed-original-bands-mne-eeg-tool',{'input':'eeg-raw.fif'},{},0),
        ('erp','skill.mne-eeg-tool','reviewed-original-erp-mne-eeg-tool',{'input':'eeg-epo.fif'},{},0),
        ('alpha','skill.mne-eeg-tool','reviewed-original-alpha-ratio-mne-eeg-tool',{'input':'eeg-raw.fif'},{},0),
        ('features','skill.mne-eeg-tool','reviewed-original-features-mne-eeg-tool',{'input':'classes-epo.fif'},{},0),
        ('table','skill.dataset-inspector','reviewed-table-inspect-dataset-inspector',{'input':'left.csv'},{},0),
        ('join','skill.scan-skill','reviewed-table-join-scan-skill',{'input':'left.csv','right':'right.csv'},{},0),
        ('bids','skill.bids-organizer','reviewed-explicit-stage-bids-organizer',{'input':'.','mapping':'mapping.json'},{},0),
        ('harmonization','skill.harmonization-tool','reviewed-none-harmonization-tool',{'features':'features.npy','meta':'meta.csv'},{'protected':'age,sex'},0),
        ('regions','skill.brain-visualization','reviewed-original-region-summary-brain-visualization',
         {'bold':'bold.nii.gz','atlas':'atlas.nii.gz','labels':'labels.txt','mask':'mask.nii.gz'},{'tr':2.0},0),
        ('act','dwi.tractography','mrtrix3-act-sift2--via-rebuild-fod-connectome',
         {'dwi':'dwi.nii.gz','mask':'mask.nii.gz','bvals':'dwi.bval','bvecs':'dwi.bvec','five_tt':'5tt.nii.gz','parcellation':'atlas.nii.gz'},
         {'tracking_streamline_count':1000,'threads':2},0),
        ('sift-history','skill.dwi-skill','reviewed-sift-existing-dwi-skill',
         {'five_tt':'5tt.nii.gz'},{'term_number':500},0),
        ('bedpost','dwi.tractography','fsl-probtrackx--via-bedpostx-probtrackx',
         {'input':'dwi.nii.gz','mask':'mask.nii.gz','bval':'dwi.bval','bvec':'dwi.bvec','seed':'seed.nii.gz'},
         {'bedpostx_burnin':0,'bedpostx_jumps':25,'bedpostx_sample_every':5,'tracking_nsamples':10,'tracking_nsteps':100},0),
        ('fsl-anat','skill.fsl-tool','reviewed-fsl-anat-fsl-tool',{'input':'public-template.nii.gz'},{},0),
    ]
    for name in ['bnt','brainnetcnn','combraintf']:
        values={'atlas':'synthetic','n_epochs':1,'kfold':2,'batch_size':2}
        if name=='combraintf':values.update(hidden_size=16,n_clusters=2,n_communities=2)
        cases.append((name,'skill.'+name,'reviewed-original-trainer-'+name,{'fmri_root':'fc','labels_csv':'training.csv'},values,1))
    try:
        for index,(name,cap,mid,files,values,gpus) in enumerate(cases):
            if only and name not in only.split(','):continue
            record={'case':name,'passed':False}; current=None
            try:
                definition=suite.capabilities.method_for(cap,mid)
                method=MethodOption(id=definition.method_id,**definition.model_dump(exclude={'method_id'}))
                workflow=suite.fixture('act' if name=='sift-history' else name)
                workflow.workflow_id='smoke_workflow_'+name
                workflow.dataset.inspection.samples=[DatasetSample(sample_id='sub-test',label='Synthetic subject',relative_root='')]
                parameters={p.id:p.default for p in method.parameters if p.default is not None}
                parameters.update(values)
                bindings={key:InputBinding(slot_id=key,artifact_type=next(s.type for s in method.input_slots if s.id==key),
                    source='existing_path',absolute_path=str((data/path).resolve())) for key,path in files.items()}
                if name=='sift-history':
                    source=next(r for r in records if r['case']=='act' and r['passed'])
                    history=HistoricalInputs(suite.store)
                    for slot_id,filename in [('input','tracks.tck'),('fod','wm_fod.mif')]:
                        slot=next(s for s in method.input_slots if s.id==slot_id)
                        original=suite.store.get_run(source['run_id'])
                        groups=history._groups(original,slot.type)
                        matches=[g for g in groups.values() if any(Path(a.absolute_path).name==filename for _,_,a in g['items'])]
                        if len(matches)!=1:raise AssertionError('Expected one historical group for '+filename)
                        preview,binding=history.preview(workflow,slot,source['run_id'],matches[0]['group_id'])
                        if not preview['can_apply']:raise AssertionError(preview)
                        bindings[slot_id]=binding
                    record['history_source_run']=source['run_id']
                step=WorkflowStep(id='analysis',order=1,title=name,description='Synthetic original-call check',capability=cap,
                    status='confirmed',method_options=[method],selected_method_id=mid,selected_parameters=parameters,input_bindings=bindings)
                workflow.steps=[step];workflow.requested_capabilities=[cap]
                suite.store.save_workflow(workflow,event_type='synthetic_fixture')
                options=RunExecutionOptions(backend='slurm',partition='all',node='node0'+str(index%3+1),
                    cpus=2,memory_gb=8,gpus=gpus,time_minutes=15)
                current=service.create(workflow.workflow_id,workflow.revision,options)
                print(json.dumps({'submitted':name,'run_id':current.run_id,'node':options.node}),flush=True)
                deadline=time.monotonic()+940
                while current.status not in {'succeeded','failed','cancelled'} and time.monotonic()<deadline:
                    time.sleep(.5);current=service.get(current.run_id)
                record.update(run_id=current.run_id,status=current.status,steps=[
                    {'runner':s.runner_id,'status':s.status,'scheduler':s.scheduler,'error':s.error_message} for s in current.steps],
                    artifacts=[{'type':a.artifact_type,'path':a.absolute_path,'sha256':a.sha256} for a in current.artifacts])
                if current.status!='succeeded': raise AssertionError(current.error_message or current.status)
                record['passed']=True
            except Exception as exc:
                record['error']=str(exc)
                if current and service.get(current.run_id).status not in {'succeeded','failed','cancelled'}:
                    service.cancel(current.run_id)
            records=[item for item in records if item['case']!=name]
            records.append(record)
            (root/'evidence.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'case':name,'passed':record['passed'],'error':record.get('error')},ensure_ascii=False),flush=True)
    finally:
        service.shutdown()
    return int(any(not r['passed'] for r in records))


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--generate',choices=['images','tensors'])
    p.add_argument('--only',default='')
    a=p.parse_args();root=a.root.resolve()
    if root.parent!=Path('/home/data/neuroweave/development'):
        p.error('Use an isolated direct child of /home/data/neuroweave/development')
    if a.generate:
        root.mkdir(mode=0o700,exist_ok=True)
        data=root/'inputs';data.mkdir(exist_ok=True)
        (images if a.generate=='images' else tensors)(data)
        return 0
    (root/'runs').mkdir(exist_ok=True)
    return run(root,a.only)


if __name__=='__main__':
    raise SystemExit(main())
