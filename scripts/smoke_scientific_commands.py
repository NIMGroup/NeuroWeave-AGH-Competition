"""Small synthetic native-command checks. No dataset, database or Slurm access."""
from pathlib import Path
from threading import Event,Timer
import argparse,json,os,sys,time,hashlib,subprocess
from datetime import datetime,timezone
import numpy as np
import nibabel as nib

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--work-dir',type=Path,required=True);parser.add_argument('--record-evidence',action='store_true');args=parser.parse_args()
work=args.work_dir.resolve()
if work.exists() and any(work.iterdir()):raise SystemExit('Use a new empty synthetic work directory')
work.mkdir(parents=True,exist_ok=True);data=work/'inputs';data.mkdir()
sys.path.insert(0,str(ROOT/'bakend'))
from app.execution.runners.scientific_commands import scientific_runners
from app.execution.base import RunnerContext
from app.workflow_models import Workflow,WorkflowStep,InputBinding
from app.method_registry import MethodRegistry
from app.method_validation import execution_fingerprint
os.environ.update(LOCALFLOW_RUN_INPUT_ROOTS=str(work),LOCALFLOW_DATA_ROOTS=str(work),SLURM_CPUS_PER_TASK='1',OMP_NUM_THREADS='1',ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS='1')
rng=np.random.default_rng(37)
def save(name,array):
    path=data/name;nib.save(nib.Nifti1Image(array.astype('float32'),np.eye(4)),path);return path
grid=np.indices((12,12,12));mask_values=((grid-5.5)**2).sum(axis=0)<20
image=save('image.nii.gz',50+10*rng.random((12,12,12)))
mask=save('mask.nii.gz',mask_values)
labels=save('labels.nii.gz',mask_values.astype('int16')*(1+(grid[0]>6)))
series=save('series.nii.gz',50+rng.normal(0,5,(12,12,12,8)))
five=np.zeros((12,12,12,5));five[...,0]=.5;five[...,2]=.5;five=save('five.nii.gz',five)
fod_values=np.zeros((12,12,12,6));fod_values[...,0]=1;fod_values[...,3]=1;fod=save('fod.nii.gz',fod_values)
tracks=data/'tracks.tck';nib.streamlines.save(nib.streamlines.Tractogram([np.array([[x,5+i*.1,5] for x in np.linspace(2,9,10)],dtype='float32') for i in range(8)],affine_to_rasmm=np.eye(4)),tracks)
matrix=data/'identity.mat';np.savetxt(matrix,np.eye(4))
design=data/'design.txt';np.savetxt(design,np.c_[np.ones(8),np.linspace(-1,1,8)])
directions=data/'directions.txt';np.savetxt(directions,np.eye(3))
surface=data/'lh.surface';vertices=np.array([[1,1,1],[9,1,1],[1,9,1],[1,1,9]],dtype=float);faces=np.array([[0,1,2],[0,3,1],[0,2,3],[1,3,2]])
nib.freesurfer.io.write_geometry(surface,vertices,faces)
# Fixture setup uses fixed native argv, limited to files created by this script.
def native_fixture(command,*argv):
    completed=subprocess.run(['/opt/software/mrtrix3-master/bin/'+command,*map(str,argv),'-nthreads','1'],capture_output=True,text=True,timeout=40)
    if completed.returncode:raise RuntimeError(command+' fixture failed: '+completed.stderr[-1200:])
raw_tracks=tracks;tracks=data/'native_tracks.tck';native_fixture('tckedit',raw_tracks,tracks)
tsf=data/'samples.tsf';native_fixture('tcksample',tracks,image,tsf)
vector=np.zeros((12,12,12,3));vector[...,2]=1
peaks=save('peaks.nii.gz',vector)
deform=save('deformation.nii.gz',np.moveaxis(grid,0,-1))
itk=data/'identity.tfm';itk.write_text('#Insight Transform File V1.0\n#Transform 0\nTransform: AffineTransform_double_3_3\nParameters: 1 0 0 0 1 0 0 0 1 0 0 0\nFixedParameters: 0 0 0\n')
gradients=data/'gradients.txt';v=rng.normal(size=(8,3));v/=np.linalg.norm(v,axis=1)[:,None];np.savetxt(gradients,np.c_[v,np.ones(8)*1000])
amp=data/'amplitudes.mif';native_fixture('mrconvert',series,amp,'-grad',gradients)
brain_grid=np.indices((64,64,64));distance=((brain_grid-31.5)**2).sum(axis=0)**.5
brain_values=np.where(distance<16,110+rng.normal(0,2,distance.shape),np.where(distance<23,70+rng.normal(0,2,distance.shape),0))
brain=save('brain.nii.gz',brain_values)
brain_mask=save('brain_mask.nii.gz',distance<23)
try:
    from pydicom.dataset import FileDataset,FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian,MRImageStorage,generate_uid
    meta=FileMetaDataset();meta.TransferSyntaxUID=ExplicitVRLittleEndian;meta.MediaStorageSOPClassUID=MRImageStorage;meta.MediaStorageSOPInstanceUID=generate_uid()
    dicom=data/'synthetic.dcm';ds=FileDataset(str(dicom),{},file_meta=meta,preamble=b'\0'*128)
    ds.SOPClassUID=MRImageStorage;ds.SOPInstanceUID=meta.MediaStorageSOPInstanceUID;ds.PatientName='Synthetic';ds.Modality='MR';ds.Rows=2;ds.Columns=2
    ds.BitsAllocated=16;ds.BitsStored=16;ds.HighBit=15;ds.PixelRepresentation=0;ds.SamplesPerPixel=1;ds.PhotometricInterpretation='MONOCHROME2';ds.PixelData=b'\0'*8
    ds.save_as(dicom,enforce_file_format=True)
except ImportError:dicom=None
results=[]
# Explicit fixtures only. A missing fixture never becomes a guessed file binding.
fixtures={
 'ConvertTransformFile':({'transform':itk},{}),
 'randomise':({'input':series,'mask':mask},{'test':'one_sample','n':4,'seed':7}),
 'amp2response':({'amps':amp,'mask':mask,'directions':peaks},{'isotropic':True}),
 'peaks2fixel':({'directions':peaks},{}),
 'tsfinfo':({'tracks':tsf},{}),'tsfmult':({'input1':tsf,'input1_2':tsf},{}),
 'tsfvalidate':({'tracks':tracks,'tsf':tsf},{}),
 'fslmaths':({'input':image},{'operation':'-add','value':2}),
 'fslstats':({'input':image,'mask':mask},{'statistic':'-M'}),
 'fslroi':({'input':image},{'mode':'spatial','xmin':2,'xsize':4,'ymin':2,'ysize':4,'zmin':2,'zsize':4}),
 'fslmerge':({'input_1':image,'input_2':image},{}), 'fslsplit':({'input':series},{}),
 'fslmeants':({'input':series,'mask':mask},{}),'fslhd':({'input':image},{}),'fslinfo':({'input':image},{}),
 'fslorient':({'input':image},{}),'fslval':({'input':image},{}),'fslchfiletype':({'input':image},{}),
 'fslreorient2std':({'input':image},{}),'fslpspec':({'input':series},{}),'fslcc':({'image1':image,'image2':image},{}),
 'fsl_regfilt':({'input':series,'design':design,'mask':mask},{'f':'2'}),
 'fsl_glm':({'input':series,'design':design,'mask':mask},{'save_residuals':True}),
 'convert_xfm':({'input':matrix},{}),'convertwarp':({'reference':image,'premat':matrix},{}),
 'smoothest':({'mask':mask,'zstat':image},{}),
 'N4BiasFieldCorrection':({'input':image,'mask':mask},{'s':1,'c':'[2,0]'}),
 'DenoiseImage':({'input':image,'mask':mask},{'r':'1','p':'1'}),
 'ThresholdImage':({'input':image},{'mode':'range','lower':52,'upper':56}),
 'SmoothImage':({'input':image},{'sigma':'1'}),'ResampleImage':({'input':image},{'size_or_spacing':'6x6x6','use_size':1}),
 'MultiplyImages':({'image1':image},{'value':2}),'ConvertImagePixelType':({'input':image},{}),
 'ExtractSliceFromImage':({'input':image},{'slice':5}),'ExtractRegionFromImage':({'input':image},{'start':'2x2x2','end':'6x6x6'}),
 'ImageIntensityStatistics':({'input':image,'labels':labels},{}),
 'LabelOverlapMeasures':({'source':labels,'target':labels},{}), 'LabelGeometryMeasures':({'labels':labels,'intensity':image},{}),
 'AverageImages':({'input_1':image,'input_2':image},{}),'PrintHeader':({'input':image},{}),
 'ImageMath':({'input':image},{'operation':'abs'}),'Atropos':({'input':image,'mask':mask},{'classes':2,'c':'[2,0]'}),
 'mri_convert':({'input':image},{}),'mri_mask':({'input':image,'mask':mask},{}),'mri_binarize':({'input':image},{'min':55}),
 'mri_concat':({'input_1':image,'input_2':image},{}),'mri_info':({'input':image},{}),
 'mri_segstats':({'segmentation':labels,'intensity':image},{}),'mri_normalize':({'input':brain,'mask':brain_mask},{'no1d':True,'noconform':True,'n':1}),
 'mri_robust_register':({'moving':brain,'fixed':brain},{'nomulti':True,'highit':2}),
 'mris_convert':({'surface':surface},{}),'mris_smooth':({'surface':surface},{'n':1}),
 'mris_extract_main_component':({'surface':surface},{}),'mri_tessellate':({'labels':labels},{'label':1}),
 'mri_pretess':({'labels':labels,'intensity':image},{'label':1}),
 '5ttcheck':({'input':five},{}),'dirstat':({'dirs':directions},{}),
 'mraverageheader':({'input':image,'input_2':image},{}),'mrcat':({'image1':image,'image2':image},{}),
 'mrcentroid':({'input':image},{}),'mrfilter':({'input':image},{'filter':'smooth'}),
 'mrinfo':({'image':image},{}),'mrmath':({'input':series},{'operation':'mean','axis':3}),
 'mrmetric':({'image1':image,'image2':image},{}),'mrstats':({'image':image},{}),'labelstats':({'input':labels},{}),
 'tckedit':({'tracks_in':tracks},{}),'tckinfo':({'tracks':tracks},{}),'tckconvert':({'tracks':tracks},{}),
 'fod2fixel':({'fod':fod},{'save_afd':True}),'mrcalc':({'input':image},{'operation':'-multiply','value':2}),
 'mtnormalise':({'wm':fod,'mask':mask},{'tissues':1,'order':'0','niter':'2,2'}),
 'mrregister':({'moving':image,'fixed':image},{'type':'rigid','rigid_niter':'1'}),
}
if dicom:fixtures['dcminfo']=({'file':dicom},{})
runners=sorted(scientific_runners(),key=lambda r:(r.entry['command'] not in ['convertwarp','mri_robust_register','fod2fixel'],r.runner_id))
for runner in runners:
    name=runner.entry['command']
    if name=='invwarp':fixtures[name]=({'warp':work/'outputs/convertwarp/output.nii.gz','reference':image},{})
    if name=='mri_vol2vol':fixtures[name]=({'moving':brain,'target':brain,'transform':work/'outputs/mri_robust_register/registration.lta'},{})
    fixels=work/'outputs/fod2fixel/fixel_directory'
    if name=='fixelconnectivity':fixtures[name]=({'fixel_directory':fixels,'tracks':tracks},{})
    if name=='fixelcrop':fixtures[name]=({'input_fixel_directory':fixels,'input_fixel_mask':fixels/'afd.mif'},{})
    if name=='fixelreorient':fixtures[name]=({'fixel_in':fixels,'warp':deform},{})
    if name not in fixtures:
        results.append({'method_id':runner.runner_id,'command':name,'status':'not_tested','reason':'No reviewed synthetic fixture for this interface.'});continue
    bindings,params=fixtures[name];output=work/'outputs'/name;output.mkdir(parents=True)
    step=WorkflowStep(id='test',order=1,title=name,description='',capability='method',status='confirmed',selected_method_id=runner.method.id,
        method_options=[runner.method],selected_parameters=params,input_bindings={key:InputBinding(slot_id=key,artifact_type=next(s.type for s in runner.method.input_slots if s.id==key),source='existing_path',absolute_path=str(path)) for key,path in bindings.items()})
    workflow=Workflow(workflow_id='synthetic-'+name,template_id='synthetic',title=name,summary='',original_request='Synthetic native command check',status='confirmed',steps=[step],created_at='',updated_at='')
    cancel=Event();timer=Timer(60,cancel.set);started=time.monotonic()
    context=RunnerContext(run_id='synthetic-'+name,workflow=workflow,step=step,method=runner.method,output_directory=output,cancel_event=cancel,log=lambda *args:None)
    try:
        status=runner.runtime_status()
        if not status['available']:raise RuntimeError(str(status))
        problems=runner.preflight(context)
        if problems:raise ValueError('; '.join(problems))
        timer.start();result=runner.run(context)
        if name=='fslmaths':np.testing.assert_allclose(nib.load(output/'output.nii.gz').get_fdata(),nib.load(image).get_fdata()+2,rtol=1e-5)
        if name=='ThresholdImage':np.testing.assert_array_equal(nib.load(output/'output.nii.gz').get_fdata(),(nib.load(image).get_fdata()>=52)&(nib.load(image).get_fdata()<=56))
        if name=='fslroi':assert nib.load(output/'output.nii.gz').shape==(4,4,4)
        if name=='ResampleImage':assert nib.load(output/'output.nii.gz').shape==(6,6,6)
        if name=='LabelGeometryMeasures':assert 'Label' in (output/'geometry.csv').read_text()
        item={'method_id':runner.runner_id,'command':name,'status':'passed','artifacts':len(result.artifacts)}
    except Exception as exc:
        item={'method_id':runner.runner_id,'command':name,'status':'failed','error':str(exc)[-1600:]}
    finally:timer.cancel()
    item['seconds']=round(time.monotonic()-started,3);results.append(item)
    print(json.dumps(item,ensure_ascii=False),flush=True)
report={'tested_at':datetime.now(timezone.utc).isoformat(),'environment':'mgr Linux CPU, 1 thread; isolated synthetic inputs','checks':results}
(work/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
if args.record_evidence:
    path=ROOT/'deploy/method-verification/reports/scientific-synthetic.json';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    registry=MethodRegistry(ROOT)
    for item in results:
        if item['status'] not in {'passed','failed'}:continue
        method=registry.method(item['method_id'])
        evidence={'method_id':method.id,'method_version':method.version,'execution_fingerprint':execution_fingerprint(method,ROOT),
            'report_path':path.relative_to(ROOT).as_posix(),'report_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'test_type':'synthetic','tested_at':report['tested_at'],'environment':report['environment'],'result':item['status']}
        (path.parent.parent/(method.id+'.json')).write_text(json.dumps(evidence,indent=2)+'\n')
print(json.dumps({'passed':sum(r['status']=='passed' for r in results),'failed':sum(r['status']=='failed' for r in results),'not_tested':sum(r['status']=='not_tested' for r in results)}))
