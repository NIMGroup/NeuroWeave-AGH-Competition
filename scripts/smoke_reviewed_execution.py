"""Synthetic, isolated execution checks; never opens the production database."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'bakend'))
sys.path.insert(0,str(ROOT))


def fixtures(root):
    import nibabel as nib
    import mne
    import numpy as np
    import pandas as pd
    root.mkdir(parents=True,exist_ok=True)
    rng=np.random.default_rng(123); shape=(12,12,12)
    grid=np.indices(shape); mask=np.sum((grid-5.5)**2,axis=0)<20
    affine=np.diag([2.,2.,2.,1.]); affine[:3,3]=-11
    def save(name,data):
        path=root/name; path.parent.mkdir(parents=True,exist_ok=True)
        nib.save(nib.Nifti1Image(data.astype(np.float32),affine),path)
        return path
    save('mask.nii.gz',mask)
    save('atlas.nii.gz',mask*(1+(grid[0]>=6)))
    phantom=mask*(100+20*np.exp(-np.sum((grid-5.5)**2,axis=0)/10))
    save('t1.nii.gz',phantom)
    save('warp.nii.gz',np.zeros((*shape,3)))
    (root/'identity.txt').write_text('#Insight Transform File V1.0\n#Transform 0\nTransform: AffineTransform_double_3_3\nParameters: 1 0 0 0 1 0 0 0 1 0 0 0\nFixedParameters: 0 0 0\n')
    bold=phantom[...,None]+mask[...,None]*rng.normal(size=(*shape,80))*3
    bold+=mask[...,None]*np.sin(np.arange(80)[None,None,None,:]/6)*8
    save('bold.nii.gz',bold)

    bvals=np.r_[np.zeros(3),np.full(29,1000)]
    bvecs=rng.normal(size=(3,32)); bvecs/=np.linalg.norm(bvecs,axis=0); bvecs[:,:3]=0
    np.savetxt(root/'bval',bvals[None,:]);np.savetxt(root/'bvec',bvecs)
    dgrid=np.indices((32,32,32)); dmask=np.sum((dgrid-15.5)**2,axis=0)<140
    baseline=1000*(1+.1*np.cos(dgrid[0]/8))*dmask
    attenuation=np.exp(-bvals*(.0003+.0007*bvecs[0]**2))
    dwi=baseline[...,None]*attenuation+rng.normal(size=(32,32,32,32))*2*dmask[...,None]
    nib.save(nib.Nifti1Image(dwi.astype(np.float32),np.diag([2.,2.,2.,1.])),root/'dwi.nii.gz')
    (root/'bold-list.txt').write_text(str(root/'bold.nii.gz')+'\n'+str(root/'bold.nii.gz')+'\n')
    images=[]
    for i in range(24):
        path=save('images/sub-'+str(i)+'.nii.gz',phantom+rng.normal(size=shape)*mask+(i%2)*grid[0]*mask)
        images.append(str(path))
    (root/'images-list.txt').write_text('\n'.join(images)+'\n')
    ids=np.array(['sub-'+str(i).zfill(3) for i in range(24)],dtype='U')
    frame=pd.DataFrame(rng.normal(size=(24,4)),columns=['f1','f2','f3','f4']);frame.insert(0,'subject_id',ids)
    frame.to_csv(root/'features.csv',index=False)
    frame['score']=frame.f1*.4+frame.f2*.2+rng.normal(size=24)*.2
    frame['diagnosis']=np.arange(24)%2;frame['age']=40+frame.f1*5+rng.normal(size=24)
    frame['duration']=rng.uniform(1,10,24);frame['event']=np.arange(24)%2
    frame['treatment']=np.arange(24)%2;frame['outcome']=frame.f1+frame.treatment+rng.normal(size=24)
    frame.to_csv(root/'training.csv',index=False)
    frame[['subject_id','f1','f2','duration','event']].to_csv(root/'survival.csv',index=False)
    frame[['subject_id','f1','f2','treatment','outcome']].to_csv(root/'causal.csv',index=False)
    frame[['subject_id','score','diagnosis']].to_csv(root/'labels.csv',index=False)
    pd.DataFrame({'intercept':np.ones(24),'group':np.arange(24)%2}).to_csv(root/'design.csv',index=False)
    pd.DataFrame({'subject_id':ids,'intercept':1,'group':np.arange(24)%2}).to_csv(root/'roi-design.csv',index=False)
    pd.DataFrame({'onset':[10,35,60,85,110],'duration':[8]*5,'trial_type':['task']*5}).to_csv(root/'events.csv',index=False)
    pd.DataFrame({'motion':rng.normal(size=80)}).to_csv(root/'confounds.csv',index=False)
    fc=rng.normal(size=(24,8,8)).astype(np.float32);fc=(fc+fc.transpose(0,2,1))/2
    fc[:,np.arange(8),np.arange(8)]=1
    np.savez_compressed(root/'connectomes.npz',X=fc,y=np.arange(24)%2,subject_id=ids,community_ids=np.repeat(np.arange(2),4))
    np.savez_compressed(root/'maps.npz',reference=rng.normal(size=8),candidates=rng.normal(size=(3,8)),target_names=np.array(['a','b','c']))
    nodes={str(i):{'domain_tags':['brain_region']} for i in range(12)}
    edges=[{'source_id':str(i),'target_id':str(j),'relation_type':'associated','confidence':1.} for i in range(12) for j in range(12) if i!=j and (i+j)%3==0]
    (root/'graph.json').write_text(json.dumps({'concepts':nodes,'edges':edges}))
    np.save(root/'matrix.npy',np.array([[1.,.8],[.8,1.]]))
    np.savez_compressed(root/'temporal.npz',X=rng.normal(size=(24,20,4)).astype(np.float32),y=np.arange(24)%2,subject_id=ids)
    np.savez_compressed(root/'cnn.npz',X=rng.normal(size=(24,1,16,16,16)).astype(np.float32),y=np.arange(24)%2,subject_id=ids)
    np.savez_compressed(root/'genetics.npz',genotype=rng.integers(0,3,(24,4)),phenotype=rng.normal(size=24),covariates=rng.normal(size=(24,1)),
        kinship=np.eye(24),X=rng.normal(size=(24,4)),Y=rng.normal(size=(24,3)),weights=rng.normal(size=4),subject_id=ids)
    np.save(root/'harmonize.npy',rng.normal(size=(24,4)))
    pd.DataFrame({'subject_id':ids,'dataset':'synthetic','site':['A']*12+['B']*12,'age':rng.uniform(20,60,24),'sex':np.arange(24)%2}).to_csv(root/'meta.csv',index=False)
    channels=['Fp1','Fp2','F3','F4','C3','C4','O1','O2']
    info=mne.create_info(channels,100,'eeg')
    raw=mne.io.RawArray(rng.normal(scale=1e-6,size=(8,2400)),info,verbose='ERROR')
    raw.set_montage('standard_1020');raw.save(root/'eeg-raw.fif',overwrite=True,verbose='ERROR')
    sphere=mne.make_sphere_model(r0=(0.,0.,0.),head_radius=.09,verbose='ERROR')
    src=mne.setup_volume_source_space(subject=None,pos=40.,sphere=(0.,0.,0.,.06),exclude=10.,verbose='ERROR')
    forward=mne.make_forward_solution(raw.info,trans=None,src=src,bem=sphere,eeg=True,meg=False,verbose='ERROR')
    mne.write_forward_solution(root/'source-fwd.fif',forward,overwrite=True,verbose='ERROR')
    mne.write_cov(root/'noise-cov.fif',mne.make_ad_hoc_cov(raw.info),overwrite=True,verbose='ERROR')
    meg_info=mne.create_info(['MEG'+str(i).zfill(4) for i in range(140)],100,'mag')
    with meg_info._unlock():
        meg_info['dev_head_t']=mne.transforms.Transform('meg','head',np.eye(4))
    for i,ch in enumerate(meg_info['chs']):
        z=1-2*(i+.5)/140; angle=i*np.pi*(3-np.sqrt(5)); v=np.array([np.sqrt(1-z*z)*np.cos(angle),np.sqrt(1-z*z)*np.sin(angle),z])
        x=np.cross(v,[0,0,1]);x/=np.linalg.norm(x);y=np.cross(v,x)
        ch['loc']=np.r_[v*(.11+.03*(i%7)/6)+[0,0,.04],x,y,v]
    meg=mne.io.RawArray(rng.normal(size=(140,400))*1e-13,meg_info,verbose='ERROR')
    meg.save(root/'meg-raw.fif',overwrite=True,verbose='ERROR')
    events=np.array([[x,0,1] for x in range(200,2200,300)])
    mne.write_events(root/'events-eve.fif',events,overwrite=True,verbose='ERROR')
    bids=root/'bids';bids.mkdir(exist_ok=True)
    from PIL import Image
    Image.fromarray(np.zeros((8,8,3),dtype=np.uint8)).save(bids/'scene.png')
    (bids/'dataset_description.json').write_text(json.dumps({'Name':'NeuroWeave synthetic','BIDSVersion':'1.9.0'}))
    cohort=pd.DataFrame({'participant_id':ids,'subject_id':ids,'Subject':ids,'SUB_ID':ids,'participant':ids,'age':40,'sex':'M','SEX':1,'site':'test','SITE_ID':'test','diagnosis':'control','DX_GROUP':2,'eventname':'baseline_year_1_arm_1','src_subject_id':ids,'interview_age':120})
    cohort.to_csv(bids/'participants.tsv',sep='\t',index=False)
    tables=root/'tables';tables.mkdir(exist_ok=True)
    for name in ['participants','phenotype','ABIDE','phenotypic','data','hcpya','hcpa','hcpd','hcpep','demographics','mnd','nifd','tcp','ppmi','pnc','oasis','aibl','cobre','hbn','adhd200']:
        cohort.to_csv(tables/(name+'.csv'),index=False)
    cohort.to_csv(root/'phenotype.csv',index=False)
    (root/'phenotype-list.txt').write_text(str(root/'phenotype.csv')+'\n')
    (bids/'nsd_stiminfo.tsv').write_text('nsdId\tcocoId\n0\t1\n')
    from scipy.io import savemat
    seed=pd.DataFrame(rng.normal(size=(24,4)),columns=['ch0_alpha','ch0_beta','ch1_alpha','ch1_beta']);seed['subject']=np.repeat(np.arange(4),6);seed['label']=np.arange(24)%2;seed.to_csv(root/'seed-classify.csv',index=False)
    (root/'ids.txt').write_text('\n'.join(ids)+'\n')
    for sub in ids[:3]:
        eeg=bids/sub/'eeg';eeg.mkdir(parents=True,exist_ok=True);savemat(eeg/(sub+'_eeg.mat'),{'eeg':rng.normal(size=(4,3200))})
        func=bids/sub/'func';anat=bids/sub/'anat';func.mkdir(parents=True,exist_ok=True);anat.mkdir(exist_ok=True)
        pd.DataFrame({'framewise_displacement':[0,.1,.2]*10,'dvars':[1.,1.,1.]*10}).to_csv(func/(sub+'_task-rest_desc-confounds_timeseries.tsv'),sep='\t',index=False)
        save('bids/'+sub+'/anat/'+sub+'_T1w.nii.gz',phantom)
        save('bids/'+sub+'/anat/'+sub+'_lesion.nii.gz',mask*(grid[0]<5))
    save('structural/sub-001/anat/sub-001_T1w.nii.gz',phantom)
    save('hcp/1001/MNINonLinear/T1w.nii.gz',phantom)
    save('ixi/IXI001-Guys-0828-T1.nii.gz',phantom)
    rawqc=pd.DataFrame({'subject_id':ids,'mean_fd':.1,'max_fd':.2,'coverage':.99,'qc_pass':True})
    rawqc.to_csv(root/'raw-qc.csv',index=False)
    ukb=pd.DataFrame({'eid':np.arange(100,124),'p31':np.arange(24)%2,'p21022':40,'p53_i0':'2010-01-01','p40000_i0':None,'p41202':'G30','p41280':'2015-01-01'})
    ukb.to_csv(root/'ukb.csv',index=False)
    (root/'conversation.json').write_text(json.dumps([{'role':'user','content':'synthetic example'},{'role':'assistant','content':'verified'}]))
    (root/'atlas-labels.txt').write_text('left\nright\n')
    # Simple FreeSurfer mesh, requiring no subject data or downloads.
    nib.freesurfer.write_geometry(root/'surface',np.array([[0,0,0],[1,0,0],[0,1,0],[0,0,1]],dtype=float),np.array([[0,1,2],[0,1,3],[0,2,3],[1,2,3]],dtype=int))
    nib.freesurfer.write_annot(root/'surface.annot',np.array([0,0,1,1]),np.array([[255,0,0,0],[0,255,0,0]]),[b'left',b'right'])
    return root


def case_values(spec,data):
    rid=spec['runner_id']; name=spec['method_id']; inputs={}
    files={'bold':'bold.nii.gz','mask':'mask.nii.gz','atlas':'atlas.nii.gz','image':'t1.nii.gz','metric':'t1.nii.gz','roi':'atlas.nii.gz',
           'reference':'t1.nii.gz','transform':'warp.nii.gz','forward':'source-fwd.fif','noise_cov':'noise-cov.fif','dwi':'dwi.nii.gz','bval':'bval','bvec':'bvec','template':'t1.nii.gz','matrix':'matrix.npy','kg':'graph.json','surf':'surface','annot':'surface.annot',
           'confounds':'confounds.csv','design_matrix':'design.csv','design':'roi-design.csv','contrast_maps':'images-list.txt',
           'images_list':'images-list.txt','features':'training.csv','labels':'labels.csv','events':'events.csv',
           'connectomes':'connectomes.npz','phenotype_file':'phenotype.csv','imaging_ids':'ids.txt','raw_qc':'raw-qc.csv','meta':'meta.csv'}
    for item in spec['inputs']:
        key=item['id']
        if key in {'annot','bval','bvec'} and not item.get('required',True):continue
        if item.get('many_args'):inputs[key]=data/'phenotype-list.txt';continue
        if key=='labels' and rid.startswith(('reviewed.dipy','reviewed.nilearn-tool.connectome')):continue
        if key=='input_list':inputs[key]=data/('images-list.txt' if name=='reviewed-spacenet' else 'bold-list.txt');continue
        if key=='input':
            filename='bids' if item['binding']=='folder' else 'ukb.csv' if 'ukb-skill' in rid else 'conversation.json' if 'beautiful-log' in rid else 'eeg-raw.fif' if 'mne-eeg-tool' in rid else 'temporal.npz' if 'temporal-models' in rid else 'cnn.npz' if 'cnn3d' in rid else 'genetics.npz' if 'imaging-genetics' in rid else 'connectomes.npz' if spec.get('profile')=='torch' else 't1.nii.gz'
            if spec['script'].endswith('native_worker.py'):
                filename='bold.nii.gz' if name in {'reviewed-mcflirt','reviewed-slicetimer'} else 'dwi.nii.gz' if any(x in name for x in ['dwi','mrdegibbs']) else 't1.nii.gz'
            inputs[key]=data/filename;continue
        if item['binding']=='folder':inputs[key]=data/('tables' if key=='phenotype_dir' else 'bids');continue
        if key in files:inputs[key]=data/files[key]
        elif item.get('required',True):raise ValueError('No synthetic fixture yet for required input: '+key)
    if 'mne-eeg-tool' in rid:
        if 'events' in inputs:inputs['events']=data/'events-eve.fif'
    if name=='reviewed-maxwell':inputs['input']=data/'meg-raw.fif'
    if name=='reviewed-fnirt':inputs.pop('transform',None)
    if name=='reviewed-ants-apply':inputs['transform']=data/'identity.txt'
    if 'survival-models' in rid:inputs['features']=data/'survival.csv'
    if 'causal-treatment-models' in rid:inputs['features']=data/'causal.csv'
    if 'harmonization-tool' in rid:inputs['features']=data/'harmonize.npy'
    if name in {'reviewed-svm','reviewed-roi-glm'}:inputs['features']=data/'features.csv'
    if name=='reviewed-searchlight':inputs['features']=data/'labels.csv'
    if name in {'reviewed-atlas-coordinates','reviewed-zalff'}:inputs['labels']=data/'atlas-labels.txt'
    if name=='reviewed-rank':
        inputs['input']=data/'maps.npz'
    if name=='reviewed-surface':inputs['annot']=data/'surface.annot'
    if 'seed' in rid and name=='reviewed-classify':inputs['input']=data/'seed-classify.csv'
    if name in {'reviewed-reorganize-abcd','reviewed-reorganize-aibl','reviewed-reorganize-hbn'}:inputs['input']=data/'structural'
    if name.startswith('reviewed-reorganize') and 'hcp' in rid:inputs['input']=data/'hcp'
    if name=='reviewed-reorganize-ixi':inputs['input']=data/'ixi'
    values={p['id']:p['default'] for p in spec['parameters'] if p.get('default') not in (None,'')}
    for key,value in {'folds':2,'cv':2,'epochs':1,'batch_size':8,'n_components':2,'n_clusters':2,'components':2,'hidden_size':32,'hidden_dim':8,'nhead':1,'n_dummies':0,'fwhm':0.,'device':'cpu'}.items():
        if any(p['id']==key for p in spec['parameters']):values[key]=value
    if any(p['id']=='target' for p in spec['parameters']):values['target']='score' if 'regression' in spec.get('fixed_args',[]) or 'cpm' in rid else 'diagnosis'
    if any(p['id']=='contrast' for p in spec['parameters']):values['contrast']='task' if name=='reviewed-task-glm' else 'group'
    if 'connectome-discovery' in rid:values.update(atlas='synthetic ordered ROIs',space='synthetic',permutations=20)
    for key,value in {'duration_col':'duration','event_col':'event','treatment_col':'treatment','outcome_col':'outcome','disease':'alzheimers'}.items():
        if any(p['id']==key for p in spec['parameters']):values[key]=value
    return inputs,values


def run_one(spec,data,root,variant=None):
    from app.execution.base import RunnerContext
    from app.execution.runners.reviewed import ReviewedRunner
    from app.workflow_models import InputBinding,MethodOption,Workflow,WorkflowStep
    runner=ReviewedRunner(spec); start=time.monotonic(); token=spec['runner_id']+('-'+str(variant) if variant else '')
    record={'runner_id':spec['runner_id'],'variant':variant,'case':token}
    output=root/'runs'/token; output.mkdir(parents=True,exist_ok=True)
    cancel=threading.Event(); timer=threading.Timer(900 if 'dwifslpreproc' in spec['runner_id'] else 180,cancel.set); timer.daemon=True
    try:
        inputs,parameters=case_values(spec,data)
        if variant is not None:parameters['operation']=variant
        method=MethodOption(id=token,skill_id=spec['skill_id'],label=spec['label'],description='synthetic',runner_id=runner.runner_id)
        step=WorkflowStep(id='analysis',order=0,title='Synthetic',description='Synthetic',capability='skill.'+spec['skill_id'],status='confirmed',selected_parameters=parameters,
            input_bindings={key:InputBinding(slot_id=key,artifact_type='analysis_input',source='existing_path',absolute_path=str(path)) for key,path in inputs.items()})
        workflow=Workflow.model_construct(steps=[step],dataset=None)
        context=RunnerContext(run_id=token,workflow=workflow,step=step,method=method,output_directory=output,cancel_event=cancel,log=lambda *a:None)
        timer.start(); result=runner.run(context)
        if not result.artifacts:raise AssertionError('No artifacts')
        import nibabel as nib
        import numpy as np
        checked=[]
        for artifact in result.artifacts:
            path=Path(artifact.absolute_path)
            if path.name.endswith(('.nii','.nii.gz')):
                image=nib.load(path)
                if not np.isfinite(image.get_fdata()).all():raise AssertionError('Non-finite image: '+str(path))
                checked.append({'path':path.name,'shape':list(image.shape)})
            if path.suffix=='.npy':
                array=np.load(path,allow_pickle=False)
                if array.dtype.kind in 'fc' and not np.isfinite(array).all():raise AssertionError('Non-finite array')
        record.update(passed=True,artifacts=len(result.artifacts),checked_images=checked)
    except Exception as exc:
        record.update(passed=False,error=str(exc))
        (output/'failure.txt').write_text(traceback.format_exc())
    finally:
        timer.cancel()
    record['seconds']=round(time.monotonic()-start,3)
    print(json.dumps(record,ensure_ascii=False),flush=True)
    return record


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True);parser.add_argument('--only',default='');parser.add_argument('--skip',default='');parser.add_argument('--variants',action='store_true');parser.add_argument('--reuse-data',type=Path)
    args=parser.parse_args(); root=args.root.resolve();root.mkdir(parents=True,exist_ok=True)
    os.environ['LOCALFLOW_RUN_INPUT_ROOTS']=str(root if not args.reuse_data else args.reuse_data.parent)
    os.environ['LOCALFLOW_SKILL_PYTHON']='/opt/neuroweave/envs/scientific-py311/bin/python'
    os.environ['LOCALFLOW_TORCH_PYTHON']='/opt/neuroweave/envs/torch-cu128-py311/bin/python'
    data=args.reuse_data or fixtures(root/'inputs')
    from app.execution.runners.reviewed import specifications
    specs=list(specifications().values())
    records=[]
    for spec in specs:
        if args.only and not any(x in spec['runner_id'] for x in args.only.split(',')):continue
        if args.skip and any(x in spec['runner_id'] for x in args.skip.split(',')):continue
        variants=next(([o['value'] for o in p['options']] for p in spec['parameters'] if p['id']=='operation'),[None]) if args.variants else [None]
        for variant in variants:
            records.append(run_one(spec,data,root,variant))
            (root/'evidence.json').write_text(json.dumps({'results':records},ensure_ascii=False,indent=2))
    print(json.dumps({'total':len(records),'passed':sum(x['passed'] for x in records),'failed':sum(not x['passed'] for x in records)}),flush=True)
    return int(any(not x['passed'] for x in records))


if __name__=='__main__':raise SystemExit(main())
