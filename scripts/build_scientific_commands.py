"""Reproducible reviewed argv contracts for the installed scientific software.

Help snapshots are primary documentation, not an execution allowlist. The specs
below explicitly identify ports, scalar grammars, conditions and managed outputs.
No method exposes arbitrary flags, output paths or shell expressions.
"""
from pathlib import Path
import argparse,json,re,sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'bakend'))
from app.method_registry import MethodDefinition

HELP=ROOT/'deploy/software-contracts/scientific'
VERSION={'ants':'2.6.5','fsl':'installed-2412.6','freesurfer':'7.4.1','mrtrix3':'3.0.8'}
ROOTS={'ants':('/opt/software/ants-2.6.5','LOCALFLOW_ANTS_ROOT'),
       'fsl':('/opt/software/fsl','LOCALFLOW_FSL_ROOT'),
       'freesurfer':('/opt/software/freesurfer-7.4.1/usr/local/freesurfer/7.4.1','LOCALFLOW_FREESURFER_HOME'),
       'mrtrix3':('/opt/software/mrtrix3-master','LOCALFLOW_MRTRIX_ROOT')}
URLS={'ants':'https://github.com/ANTsX/ANTs/wiki','fsl':'https://fsl.fmrib.ox.ac.uk/fsl/docs/utilities/fslutils.html',
      'freesurfer':'https://surfer.nmr.mgh.harvard.edu/fswiki/FreeSurferCommands','mrtrix3':'https://mrtrix.readthedocs.io/en/3.0.8/reference/commands/'}
IMAGE=['.nii','.nii.gz','.mif','.mgz','.mgh']
SPECS=[]

def I(key,**kw):return {'input':key,**kw}
def O(key='output',**kw):return {'output':key,**kw}
def P(key,**kw):return {'parameter':key,**kw}


class Spec:
    def __init__(self,family,command,purpose):
        self.family,self.command,self.purpose=family,command,purpose
        self.inputs=[];self.outputs=[];self.parameters=[];self.argv=[];self.patterns={};self.complete=[]
        self.omitted={'other_options':'Only the documented options below are exposed. Consult the installed-version help for other syntax; arbitrary arguments are not accepted.'}
        SPECS.append(self)
    def inp(self,key='input',kind='imaging_input',description='Input image. Choose an existing file; the command never modifies it.',required=True,when=None,folder=False):
        self.inputs.append(dict(id=key,type=kind,label=key,description=description,required=required,
            required_when=when or {},binding='folder' if folder else 'file',formats=[] if folder else IMAGE if kind in {'imaging_input','scalar_image','brain_mask','label_image','fod_image'} else []))
        return I(key)
    def out(self,key='output',kind='imaging_input',filename='output.nii.gz',when=None,directory=False):
        self.outputs.append(dict(id=key,type=kind,label=key,description='Native command output, saved in this run’s output directory. '+self.purpose,
            primary=not self.outputs,format='directory' if directory else ''.join(Path(filename).suffixes),path_template=filename,generated_when=when or {}))
        return O(key)
    def par(self,key,description,kind='string',default=None,choices=None,minimum=None,maximum=None,pattern=None,when=None,required=False,flag=''):
        p=dict(id=key,label=key,argument=flag,description=description,type=kind,default=default,required=required,
               usage=description+' Unset optional values are not passed to the command.',
               software_default='The installed command decides omitted values; see the pinned help snapshot.',documentation_url=URLS[self.family])
        if choices:p['options']=[{'value':v,'label':str(v)} for v in choices];p['control']='select'
        if minimum is not None:p['minimum']=minimum
        if maximum is not None:p['maximum']=maximum
        if pattern:self.patterns[key]=pattern
        if when:p.update(visible_when=when,required_when=when,condition_help='Required only for the selected operation.')
        self.parameters.append(p);return P(key)
    def args(self,*tokens,when=None,optional=False):
        self.argv.append(dict(tokens=list(tokens),when=when or {},optional=optional));return self
    def flag(self,flag,description,default=False):
        key=flag.lstrip('-').replace('-','_');self.par(key,description,'boolean',default,flag=flag);self.args(flag,when={key:[True]});return key
    def option(self,flag,description,kind='number',default=None,choices=None,minimum=None,pattern=None):
        key=flag.lstrip('-').replace('-','_');p=self.par(key,description,kind,default,choices,minimum,pattern=pattern,flag=flag)
        self.args(flag,p,optional=default is None);return key
    def mask(self,flag='-m'):
        self.args(flag,self.inp('mask','brain_mask','Mask in the same grid as the input image.',False),optional=True)
    def dimension(self,flag=None):
        p=self.par('dimension','Image dimensionality. Match the actual input image.','integer',3,[2,3,4])
        if flag:self.args(flag,p)
        else:self.args(p)
    def repeated_inputs(self,min_count=1,max_count=8,kind='imaging_input'):
        for n in range(1,max_count+1):
            self.args(self.inp('input_'+str(n),kind,f'Input {n}, in the desired argument order. Up to {max_count} separately authorized files are supported.',n<=min_count),optional=n>min_count)
        self.omitted['unbounded_inputs']=f'The interface accepts at most {max_count} explicitly bound inputs, without wildcards.'


def fsl_specs():
    s=Spec('fsl','fslmaths','Voxelwise arithmetic, filtering and reductions using native FSL operations.')
    s.args(s.inp())
    unary=['-exp','-log','-sin','-cos','-tan','-asin','-acos','-atan','-sqr','-sqrt','-recip','-abs','-bin','-binv','-nan','-nanm','-dilM','-dilD','-dilF','-ero','-eroF','-fmean','-fmedian','-fillh','-fillh26','-Tmean','-Tstd','-Tmin','-Tmax','-Tmedian','-Tsum','-Tmaxn','-Tminn','-Tar1','-index','-edge']
    scalar=['-add','-sub','-mul','-div','-rem','-max','-min','-thr','-uthr','-thrp','-uthrp','-thrP','-uthrP','-s','-inm','-ing']
    s.par('operation','Choose one native operation. Compose multiple operations as Pipeline steps.',default='-bin',choices=unary+scalar+['-mas'])
    s.args(P('operation'))
    s.par('value','Numeric operand, threshold, target mean, or smoothing sigma in mm.','number',when={'operation':scalar})
    s.args(P('value'),when={'operation':scalar})
    s.args(s.inp('mask','brain_mask','Binary mask used by -mas.',False,{'operation':['-mas']}),when={'operation':['-mas']})
    s.args(s.out())
    s.option('-odt','Output storage datatype.','string',choices=['char','short','int','float','double','input'])

    s=Spec('fsl','fslstats','Image summary statistics. Results are captured in the command report.')
    s.flag('-t','Report each volume of a 4D image separately.');s.args(s.inp());s.mask('-k')
    for flag in ['-l','-u']:s.option(flag,'Exclusive lower/upper intensity threshold.')
    flags=['-r','-R','-e','-E','-v','-V','-m','-M','-s','-S','-w','-x','-X','-c','-C','-p','-P','-h']
    s.par('statistic','Native statistic to print. Uppercase variants often exclude zero voxels; consult help.',default='-M',choices=flags)
    s.args(P('statistic'));s.par('percentile','Percentile in [0,100].','number',50,minimum=0,maximum=100,when={'statistic':['-p','-P']});s.args(P('percentile'),when={'statistic':['-p','-P']})
    s.par('bins','Histogram bin count.','integer',100,minimum=1,when={'statistic':['-h']});s.args(P('bins'),when={'statistic':['-h']});s.out('report','execution_log','command.log')

    s=Spec('fsl','fslroi','Extract a spatial or temporal region of an image; size -1 means all remaining samples.')
    s.args(s.inp(),s.out());s.par('mode','Crop in space, time, or both.',default='spatial',choices=['spatial','temporal','both'])
    for axis in 'xyzt':
        when={'mode':['temporal','both'] if axis=='t' else ['spatial','both']}
        s.args(s.par(axis+'min','Zero-based first voxel/volume.','integer',0,minimum=0,when=when),
               s.par(axis+'size','Number of voxels/volumes; -1 keeps the rest.','integer',-1,minimum=-1,when=when),when=when)
    s=Spec('fsl','fslmerge','Concatenate explicitly selected images along one axis.')
    s.args(s.par('axis','Concatenation axis: time, x, y or z.',default='-t',choices=['-t','-x','-y','-z']),s.out());s.repeated_inputs(2)
    s=Spec('fsl','fslsplit','Split a volume into separate images along one axis.')
    s.args(s.inp(),s.out('volumes','generated_folder','volumes',directory=True)|{'suffix':'vol'},s.par('axis','Split axis.',default='-t',choices=['-t','-x','-y','-z']))
    s=Spec('fsl','fslmeants','Extract mean, weighted, eigenvariate or voxelwise time series.')
    s.args('-i',s.inp(),'-o',s.out('timeseries','roi_timeseries','timeseries.txt'));s.mask('-m')
    for flag,desc in [('--showall','Output every masked voxel.'),('--eig','Output eigenvariates.'),('--transpose','Transpose text output.'),('--usemm','Coordinate inputs use millimetres.')]:s.flag(flag,desc)
    s.option('--order','Eigenvariate order.','integer',minimum=1)
    for command in ['fslhd','fslinfo']:
        s=Spec('fsl',command,'Inspect image header metadata without changing the image.');s.args(s.inp());s.out('report','execution_log','command.log')
    s=Spec('fsl','fslval','Read a named NIfTI/Analyze header field.')
    s.args(s.inp(),s.par('field','Header field to report.',default='dim1',choices=['dim1','dim2','dim3','dim4','pixdim1','pixdim2','pixdim3','pixdim4','datatype','qform_code','sform_code','cal_min','cal_max']));s.out('report','execution_log','command.log')
    s=Spec('fsl','fslorient','Inspect orientation, qform, and sform. In-place editing flags are intentionally excluded.')
    s.args(s.par('query','Read-only orientation query.',default='-getorient',choices=['-getorient','-getqform','-getsform','-getqformcode','-getsformcode']),s.inp());s.out('report','execution_log','command.log')
    s=Spec('fsl','fslreorient2std','Reorient an image to standard label orientation; this is not registration.');s.args(s.inp(),s.out())
    s=Spec('fsl','fslchfiletype','Convert file format using a separate output, never overwrite the source.')
    s.args(s.par('format','Output type; this interface writes NIfTI.',default='NIFTI_GZ',choices=['NIFTI_GZ']),s.inp(),s.out())
    s=Spec('fsl','fslpspec','Compute the temporal power spectrum of an image.');s.args(s.inp(),s.out())
    s=Spec('fsl','fslcc','Cross-correlate image volumes.');s.args(s.inp('image1'),s.inp('image2'));s.out('report','execution_log','command.log')
    s=Spec('fsl','fsl_regfilt','Regress selected design components from a 4D time series.')
    s.args('-i',s.inp(),'-d',s.inp('design','generated_file','Design matrix from MELODIC or another compatible source.'),'-o',s.out())
    s.option('-f','Comma-separated one-based component columns to remove.','string',pattern=r'\d+(,\d+)*');s.parameters[-1]['required']=True;s.argv[-1]['optional']=False;s.mask('-m')
    s.flag('-a','Filter all components.');s.flag('--vn','Variance-normalize the input before regression.')
    s=Spec('fsl','fsl_glm','Fit a temporal/spatial GLM to image data using an explicitly supplied design matrix.')
    s.args('-i',s.inp(),'-d',s.inp('design','generated_file','Design matrix; rows must match observations.'),'-o',s.out('betas','imaging_input','betas.nii.gz'));s.mask('-m')
    s.args('-c',s.inp('contrasts','generated_file','T-contrast matrix.',False),optional=True)
    for flag, description in {
        '--des_norm': 'Normalize each design-matrix column to unit standard deviation.',
        '--dat_norm': 'Normalize each data time series to unit standard deviation.',
        '--vn': 'Apply MELODIC variance normalization to the data.',
        '--demean': 'Remove the mean from both the design matrix and the data before fitting.'
    }.items():s.flag(flag,description)
    s.option('--dof','Explicit residual degrees of freedom.','integer',minimum=1)
    for flag,name,kind in [('--out_res','residuals','imaging_input'),('--out_sigsq','variance','imaging_input')]:
        s.par('save_'+name,'Save '+name+'.','boolean',False);s.args(flag,s.out(name,kind,name+'.nii.gz',{'save_'+name:[True]}),when={'save_'+name:[True]})
    s=Spec('fsl','convert_xfm','Invert or concatenate affine matrices in FSL convention.')
    s.args('-omat',s.out('transform','transform_matrix','transform.mat'));s.par('operation','Matrix operation.',default='inverse',choices=['inverse','concat','fixscaleskew'])
    s.args('-inverse',when={'operation':['inverse']})
    s.inp('second','transform_matrix','Second matrix; for concat this maps B to C.',False,{'operation':['concat','fixscaleskew']})
    for op in ['concat','fixscaleskew']:s.args('-'+op,I('second'),when={'operation':[op]})
    s.args(s.inp('input','transform_matrix','Input matrix; for concat this maps A to B.'))
    s=Spec('fsl','invwarp','Invert a nonlinear warp with an explicit reference image.')
    s.args('-w',s.inp('warp'),'-r',s.inp('reference'),'-o',s.out('inverse_warp'));s.flag('--rel','Input warp contains relative displacements.');s.flag('--abs','Input warp contains absolute coordinates.')
    for p in s.parameters:p['conflicts_with']=['abs' if p['id']=='rel' else 'rel']
    s=Spec('fsl','convertwarp','Compose nonlinear warps and affine matrices in a reference grid.')
    s.args('--ref',s.inp('reference'),'--out',s.out('warp'))
    for flag,kind in [('warp1','imaging_input'),('warp2','imaging_input'),('premat','transform_matrix'),('midmat','transform_matrix'),('postmat','transform_matrix')]:s.args('--'+flag,s.inp(flag,kind,'Optional transform in the declared composition order.',False),optional=True)
    for flag in ['--relout','--absout']:s.flag(flag,'Output displacement convention.')
    for p in s.parameters:p['conflicts_with']=['absout' if p['id']=='relout' else 'relout']


def ants_specs():
    for command,purpose in [('N4BiasFieldCorrection','Correct smooth intensity bias using N4.'),('DenoiseImage','Denoise an image using the native ANTs patch-based algorithm.')]:
        s=Spec('ants',command,purpose);s.dimension('-d');s.args('-i',s.inp(),'-o',s.out());s.mask('-x')
        s.option('-s','Shrink factor for computation.','integer',minimum=1)
        if command=='DenoiseImage':
            s.option('-n','Noise model.','string','Gaussian',['Gaussian','Rician'])
            s.option('-p','Patch radius, scalar or x-separated per-axis radii.','string',pattern=r'\d+(x\d+){0,3}')
            s.option('-r','Search radius, scalar or x-separated per-axis radii.','string',pattern=r'\d+(x\d+){0,3}')
        else:
            s.option('-c','N4 convergence: [iterations-per-level,threshold].','string',pattern=r'\[\d+(x\d+)*,[0-9.eE+-]+\]')
            s.option('-b','B-spline grid: [distance-or-mesh-resolution,spline-order].','string',pattern=r'\[[0-9.]+(x[0-9.]+)*,[1-5]\]')
    s=Spec('ants','ThresholdImage','Inclusive thresholding, Otsu or K-means segmentation.')
    s.dimension();s.args(s.inp(),s.out('labels','label_image'));s.par('mode','Thresholding algorithm.',default='range',choices=['range','Otsu','Kmeans'])
    for key,default in [('lower',0),('upper',1),('inside',1),('outside',0)]:s.args(s.par(key,'Inclusive range threshold or assigned voxel value.','number',default,when={'mode':['range']}),when={'mode':['range']})
    s.args(P('mode'),s.par('classes','Number of thresholds/classes.','integer',2,minimum=1,when={'mode':['Otsu','Kmeans']}),when={'mode':['Otsu','Kmeans']})
    s.args(s.inp('mask','brain_mask','Optional mask for Otsu/K-means.',False),when={'mode':['Otsu','Kmeans']},optional=True)
    s=Spec('ants','SmoothImage','Gaussian smoothing or median filtering with a scalar/per-axis width.')
    s.dimension();s.args(s.inp(),s.par('sigma','Gaussian sigma, or median radius in voxels; x separates axes.',default='1',pattern=r'[0-9.]+(x[0-9.]+){0,3}'),s.out(),s.par('physical_units','Interpret sigma in physical spacing units.','boolean',False),s.par('median','Use median filter instead of Gaussian.','boolean',False))
    s=Spec('ants','ResampleImage','Resample an image to specified voxel spacing or voxel counts.')
    s.dimension();s.args(s.inp(),s.out(),s.par('size_or_spacing','Positive x-separated values, one per image dimension.',default='1x1x1',pattern=r'[0-9.]+(x[0-9.]+){1,3}'),s.par('use_size','Use voxel counts (1), otherwise spacing (0).','integer',0,[0,1]),s.par('interpolation','0 linear; 1 nearest; 2 Gaussian; 3 windowed sinc; 4 B-spline.','integer',0,[0,1,2,3,4]),s.par('pixeltype','Output pixel type: 0 char, 1 uchar, 2 short, 3 ushort, 4 int, 5 uint, 6 float, 7 double.','integer',6,list(range(8))))
    s=Spec('ants','MultiplyImages','Multiply two images or scale an image by a scalar.');s.dimension();s.args(s.inp('image1'));s.par('operand_kind','Second operand source.',default='scalar',choices=['scalar','image'])
    s.args(s.par('value','Multiplication factor.','number',1,when={'operand_kind':['scalar']}),when={'operand_kind':['scalar']})
    s.args(s.inp('image2',required=False,when={'operand_kind':['image']}),when={'operand_kind':['image']});s.args(s.out())
    s=Spec('ants','ConvertImagePixelType','Convert integer pixel type, scaling intensities to the new dynamic range.')
    s.args(s.inp(),s.out(),s.par('pixeltype','0 char, 1 unsigned char, 2 short, 3 unsigned short, 4 int, 5 unsigned int.','integer',2,list(range(6))))
    s=Spec('ants','ExtractSliceFromImage','Extract one slice along a selected axis.');s.dimension();s.args(s.inp(),s.out(),s.par('axis','Zero-based extraction axis.','integer',2,minimum=0,maximum=3),s.par('slice','Zero-based slice index.','integer',0,minimum=0))
    s=Spec('ants','ExtractRegionFromImage','Extract an axis-aligned image region using start and end indices.');s.dimension();s.args(s.inp(),s.out(),s.par('start','x-separated start indices.',default='0x0x0',pattern=r'\d+(x\d+){1,3}'),s.par('end','x-separated end indices; match image bounds.',required=True,pattern=r'\d+(x\d+){1,3}'))
    s=Spec('ants','ImageIntensityStatistics','Summarize image intensities, optionally within labels.');s.dimension();s.args(s.inp());s.args(s.inp('labels','label_image','Optional label image.',False),optional=True);s.out('report','execution_log','command.log')
    s=Spec('ants','LabelOverlapMeasures','Measure agreement between source and target label images.');s.dimension();s.args(s.inp('source','label_image'),s.inp('target','label_image'));s.out('report','execution_log','command.log')
    s=Spec('ants','LabelGeometryMeasures','Measure shape and intensity statistics of labelled regions.');s.dimension();s.args(s.inp('labels','label_image'),s.inp('intensity',required=False)|{'missing':'none'},s.out('table','generated_file','geometry.csv'))
    s=Spec('ants','AverageImages','Average two to eight images, optionally normalizing their intensities.');s.dimension();s.args(s.out(),s.par('normalize','0 none; 1 mean normalization with sharpening; 2 mean normalization without sharpening.','integer',0,[0,1,2]));s.repeated_inputs(2)
    s=Spec('ants','PrintHeader','Inspect image header fields without changing the image.');s.args(s.inp());s.out('report','execution_log','command.log')
    s=Spec('ants','ImageMath','Native ANTs image arithmetic and morphology; choose one operation per method step.')
    s.dimension();s.args(s.out(),s.par('operation','Native ImageMath operator.',default='abs',choices=['abs','Neg','m','+','-','/','^','max','exp','G','MD','ME','MO','MC','GD','GE','GO','GC','FillHoles','Normalize','Laplacian','Grad']))
    s.args(s.inp());binary=['m','+','-','/','^','max'];scalar=['exp','G','MD','ME','MO','MC','GD','GE','GO','GC','Laplacian','Grad']
    s.args(s.inp('image2',required=False,when={'operation':binary}),when={'operation':binary})
    s.args(s.par('value','Operation argument, such as smoothing sigma or morphology radius.','number',1,when={'operation':scalar}),when={'operation':scalar})
    s=Spec('ants','ConvertTransformFile','Convert an ITK transform to a matrix file.');s.dimension();s.args(s.inp('transform','transform_matrix'),s.out('transform','transform_matrix','transform.txt'))


def freesurfer_specs():
    s=Spec('freesurfer','mri_convert','Convert volume format, voxel size and orientation using a new output file.')
    for flag,desc in [('--out_orientation','Target orientation code.'),('--resample_type','Resampling interpolation.')]:
        s.option(flag,desc,'string',choices=['RAS','LPS','LAS','RPI'] if flag=='--out_orientation' else ['nearest','trilinear','cubic','weighted'])
    s.args(s.inp(),s.out())
    s=Spec('freesurfer','mri_mask','Apply a mask volume to an input image without modifying either source.')
    s.option('-T','Mask values at or below this threshold are zero.');s.option('-dilate','Number of mask dilation iterations.','integer',minimum=0);s.option('-oval','Output value outside the mask.')
    for flag, description in {
        '-abs': 'Take absolute mask values before applying the mask threshold.',
        '-invert': 'Invert the mask so the normally excluded region is retained instead.',
        '-samseg': 'Interpret the mask as a SAMSEG segmentation and remove its non-brain labels.'
    }.items():s.flag(flag,description)
    s.args(s.inp(),s.inp('mask','brain_mask'),s.out())
    s=Spec('freesurfer','mri_binarize','Binarize, threshold and morphologically process a volume.')
    s.args('--i',s.inp(),'--o',s.out('mask','brain_mask'));s.option('--min','Minimum included intensity.');s.option('--max','Maximum included intensity.')
    for flag, description in {
        '--abs': 'Take absolute input intensities before thresholding or matching.',
        '--inv': 'Invert the output bin values: matching voxels receive 0 and non-matching voxels receive 1.',
        '--no-count': 'Disable counting voxels in the first frame; useful for large volumes.',
        '--bincol': 'Assign each selected voxel its column number instead of a uniform binary value.',
        '--zero-edges': 'Set the first and last planes along every dimension to 0, or to binvalnot when specified.',
        '--zero-slice-edges': 'Apply edge zeroing only to the first and last slices, using 0 or binvalnot.'
    }.items():s.flag(flag,description)
    for flag in ['--dilate','--erode','--erode2d']:s.option(flag,'Morphological iteration count.','integer',minimum=0)
    s.option('--binval','Value assigned to matching voxels.','integer');s.option('--binvalnot','Value assigned to other voxels.','integer')
    s=Spec('freesurfer','mri_concat','Concatenate two to eight input volumes and optionally reduce across frames.')
    for n in range(1,9):s.args('--i',s.inp('input_'+str(n),required=n<=2),optional=n>2)
    s.args('--o',s.out());s.par('reduction','Optional reduction after concatenation.',default='none',choices=['none','--mean','--sum','--std','--var','--max','--min','--median'])
    s.args(P('reduction'),when={'reduction':['--mean','--sum','--std','--var','--max','--min','--median']})
    s=Spec('freesurfer','mri_info','Inspect volume metadata using a read-only query.')
    s.par('query','Header property; all prints the standard complete header.',default='all',choices=['all','--dims','--res','--vox2ras','--ras2vox','--orientation','--nframes','--type','--tr','--te','--ti'])
    s.args(P('query'),when={'query':['--dims','--res','--vox2ras','--ras2vox','--orientation','--nframes','--type','--tr','--te','--ti']});s.args(s.inp());s.out('report','execution_log','command.log')
    s=Spec('freesurfer','mri_segstats','Summarize segmentation regions with optional image intensity statistics.')
    s.args('--seg',s.inp('segmentation','label_image'),'--sum',s.out('summary','generated_file','segstats.txt'));s.args('--i',s.inp('intensity',required=False),optional=True)
    s.flag('--empty','Include labels with no voxels.');s.flag('--accumulate','Accumulate results across labels.')
    s=Spec('freesurfer','mri_normalize','Intensity normalization using FreeSurfer mri_normalize.')
    s.flag('-no1d','Disable initial one-dimensional intensity normalization; use when its assumptions do not fit the image.')
    s.flag('-noconform','Keep the original voxel grid instead of conforming to 256 cubed.')
    s.option('-n','Number of three-dimensional normalization iterations.','integer',minimum=1)
    s.option('-a','Maximum control-point intensity above the white-matter target.','number',minimum=0)
    s.option('-b','Maximum control-point intensity below the white-matter target.','number',minimum=0)
    s.args('-MASK',s.inp('mask','brain_mask','Optional brain mask in the input image grid.',False),optional=True)
    s.args(s.inp(),s.out())
    s=Spec('freesurfer','mri_robust_register','Robust image registration with an explicit target and transform output.')
    s.args('--mov',s.inp('moving'),'--dst',s.inp('fixed'),'--lta',s.out('transform','transform_matrix','registration.lta'))
    s.flag('--nomulti','Process only the highest resolution; disable the multi-resolution pyramid.')
    s.option('--minsize','Smallest voxel dimension used by the Gaussian pyramid.','integer',minimum=1)
    s.option('--maxit','Maximum iterations at each pyramid resolution.','integer',minimum=1)
    s.option('--highit','Maximum iterations at the highest resolution.','integer',minimum=1)
    s.flag('--satit','Automatically estimate saturation parameter.',True);s.flag('--iscale','Estimate intensity scaling.');s.flag('--affine','Use affine transformation.')
    s=Spec('freesurfer','mri_vol2vol','Resample a moving volume into a target grid with an explicit LTA registration.')
    s.args('--mov',s.inp('moving'),'--targ',s.inp('target'),'--lta',s.inp('transform','transform_matrix'),'--o',s.out('registered','registered_image'))
    s.option('--interp','Resampling interpolation.','string','trilin',['trilin','nearest','cubic']);s.flag('--inv','Invert the supplied registration.')
    s=Spec('freesurfer','mris_convert','Convert a surface to GIFTI without modifying the source.');s.args(s.inp('surface','generated_file','Input FreeSurfer surface or supported surface format.'),s.out('surface','generated_file','surface.gii'))
    s=Spec('freesurfer','mris_smooth','Smooth a surface using the native FreeSurfer algorithm.')
    s.option('-n','Number of smoothing iterations.','integer',10,minimum=0);s.flag('-nw','Do not write curvature and area auxiliary files.',True)
    s.args(s.inp('surface','generated_file','Input surface file.'),s.out('surface','generated_file','surface.smooth'))
    s=Spec('freesurfer','mris_extract_main_component','Keep the largest connected surface component.');s.args(s.inp('surface','generated_file','Input surface file.'),s.out('surface','generated_file','surface.main'))
    s=Spec('freesurfer','mri_tessellate','Tessellate a labelled volume at an explicitly selected label.')
    s.args(s.inp('labels','label_image'),s.par('label','Label value to tessellate.','integer',1,minimum=0),s.out('surface','generated_file','surface'))
    s=Spec('freesurfer','mri_pretess','Prepare a label for tessellation using the corresponding intensity volume.')
    s.args(s.inp('labels','label_image'),s.par('label','Label value to prepare.','integer',1,minimum=0),s.inp('intensity'),s.out('labels','label_image'))


def mrtrix_specs():
    coverage=json.loads((ROOT/'deploy/software-contracts/mrtrix3/coverage.json').read_text(encoding='utf-8'))
    names={'5ttcheck','amp2response','dcminfo','dirstat','fixelconnectivity','fixelcrop','fixelreorient','fod2fixel',
           'labelstats','mraverageheader','mrcat','mrcentroid','mrfilter','mrinfo','mrmath','mrmetric','mrstats',
           'peaks2fixel','tckedit','tckinfo','tsfinfo','tsfmult','tsfvalidate'}
    safe={'INT':'integer','FLOAT':'number','CHOICE':'string','ISEQ':'string','FSEQ':'string'}
    managed={'force','config','help','version','quiet','debug','info'}
    for row in coverage:
        name=row['command']
        if name not in names:continue
        raw=(ROOT/'deploy/software-contracts/mrtrix3'/(name+'.usage.txt')).read_text(encoding='utf-8')
        s=Spec('mrtrix3',name,raw.splitlines()[0]);s.omitted={}
        used=set()
        def arg(a,key,required,option=False,when=None):
            kind=a['kind'];description=a.get('description','') or f'Native {name} argument {a["name"]} ({kind}).'
            if kind in {'IMAGEIN','FILEIN','TRACKSIN','DIRIN'}:
                artifact='tractogram' if kind=='TRACKSIN' else 'generated_folder' if kind=='DIRIN' else 'generated_file' if kind=='FILEIN' else 'imaging_input'
                if name=='amp2response' and key=='mask':artifact='brain_mask'
                if name=='fod2fixel' and key=='fod':artifact='fod_image'
                return s.inp(key,artifact,description,required,when,kind=='DIRIN')
            if kind in {'IMAGEOUT','FILEOUT','TRACKSOUT','DIROUT'}:
                artifact='tractogram' if kind=='TRACKSOUT' else 'generated_folder' if kind=='DIROUT' else 'generated_file' if kind=='FILEOUT' else 'imaging_input'
                if name=='amp2response':artifact='response_functions'
                ext='.mif' if kind=='IMAGEOUT' else '.tck' if kind=='TRACKSOUT' else '.txt' if kind=='FILEOUT' else ''
                return s.out(key,artifact,key+ext,when,kind=='DIROUT')
            if kind in safe:
                choices=a.get('extra') if kind=='CHOICE' else None
                pattern=r'-?\d+(,-?\d+)*' if kind=='ISEQ' else r'[+-]?[0-9.eE]+(,[+-]?[0-9.eE]+)*' if kind=='FSEQ' else None
                return s.par(key,description,safe[kind],choices[0] if choices and required else None,choices,pattern=pattern,required=required,when=when)
            raise ValueError('Unreviewed argument '+name+'/'+key)
        for a in row['positional']:
            key=a['name']
            if key in used:key=key+'_2'
            used.add(key)
            count=4 if a['repeat'] else 1
            for n in range(count):
                k=key if n==0 else key+'_'+str(n+1)
                s.args(arg(a,k,n==0 and not a['optional']),optional=n>0 or a['optional'])
            if a['repeat']:s.omitted[key+'_unbounded']='This interface supports up to four files for this repeated argument.'
        flags_seen=set()
        for option in row['options']:
            if option['name'] in flags_seen:continue
            flags_seen.add(option['name'])
            flag=option['name'];args=option['args'];description=option['description']
            if flag in managed or option['repeat'] or any(a['kind'] not in {*safe,'IMAGEIN','IMAGEOUT','FILEIN','FILEOUT','TRACKSIN','TRACKSOUT','DIRIN','DIROUT'} or a['repeat'] for a in args):
                s.omitted[flag]='Managed by the platform or requires a specialized repeated/text argument contract.';continue
            if not args:
                s.flag('-'+flag,description);continue
            tokens=[];condition=None
            if any(a['kind'].endswith('OUT') for a in args):
                key='save_'+flag;s.par(key,description,'boolean',False);condition={key:[True]}
            for a in args:
                key=flag if len(args)==1 else flag+'_'+a['name']
                if key in used:key='option_'+key
                used.add(key)
                a={**a,'description':description+' '+a.get('description','')}
                token=arg(a,key,False,True,condition);tokens.append(token)
                if flag=='nthreads':
                    s.parameters[-1].update(default=1,required=True,minimum=1,maximum=128)
            s.args('-'+flag,*tokens,when=condition,optional=not condition)
        if not any(not output.get('generated_when') for output in s.outputs):s.out('report','execution_log','command.log')
        if name=='fod2fixel':
            # MRtrix interprets -afd/-peak_amp/-disp relative to its fixel
            # output directory, unlike ordinary image output arguments.
            for output in s.outputs:
                if output['id'] in {'afd','peak_amp','disp'}:
                    output['path_template']='fixel_directory/'+output['path_template']
            for group in s.argv:
                for token in group['tokens']:
                    if isinstance(token,dict) and token.get('output') in {'afd','peak_amp','disp'}:
                        token['relative_to_output']='fixel_directory'
        if name=='tsfmult':
            for output in s.outputs:output['path_template']='output.tsf'
        if name=='mrmath':
            # A single repeated input requires an axis. We expose a required
            # default axis; multi-image reduction remains possible via the UI.
            for p in s.parameters:
                if p['id']=='axis':p.update(default=3,minimum=0,maximum=3)
        if name=='mrfilter':
            filters={'axes':['fft'],'inverse':['fft'],'magnitude':['fft','gradient'],'centre_zero':['fft'],
                'stdev':['gradient','smooth'],'scanner':['gradient'],'extent':['median','normalise','smooth'],
                'fwhm':['smooth'],'zupper':['zclean'],'zlower':['zclean'],'bridge':['zclean'],
                'maskin':['zclean'],'maskout':['zclean']}
            for group in s.argv:
                flag=group['tokens'][0] if group['tokens'] else None
                if isinstance(flag,str) and flag.lstrip('-') in filters:
                    modes=filters[flag.lstrip('-')];group['when']['filter']=modes
                    for token in group['tokens']:
                        if not isinstance(token,dict):continue
                        for parameter in s.parameters:
                            if parameter['id']==token.get('parameter'):
                                parameter['visible_when']={'filter':modes}
                                parameter['description']='For filters '+', '.join(modes)+': '+parameter['description']
                        for output in s.outputs:
                            if output['id']==token.get('output'):output['generated_when']['filter']=modes
            for parameter in s.parameters:
                if parameter['id']=='save_maskout':parameter['visible_when']={'filter':['zclean']}
                if parameter['id']=='stdev':parameter['conflicts_with']=['fwhm']
                if parameter['id']=='fwhm':parameter['conflicts_with']=['stdev']
        if name=='mrmetric':
            for parameter in s.parameters:
                if parameter['id']=='metric':parameter['options']=[{'value':'diff','label':'diff'}]
            s.omitted['metric_cc']='Cross-correlation is only partially implemented upstream; this contract exposes the documented fully implemented squared difference metric.'


def extra_specs():
    s=Spec('fsl','randomise','Permutation-based GLM inference with optional TFCE. Statistical design and exchangeability assumptions require scientific review.')
    s.args('-i',s.inp(),'-o',s.out('statistics','generated_folder','statistics',directory=True)|{'suffix':'stats'});s.mask('-m')
    s.par('test','One-sample mean or a supplied general linear model.',default='glm',choices=['glm','one_sample'])
    s.args('-1',when={'test':['one_sample']})
    s.args('-d',s.inp('design','generated_file','FSL design matrix.',False,{'test':['glm']}),when={'test':['glm']})
    s.args('-t',s.inp('contrasts','generated_file','FSL t-contrast matrix.',False,{'test':['glm']}),when={'test':['glm']})
    s.args('-e',s.inp('exchangeability','generated_file','Exchangeability block labels.',False),optional=True)
    s.option('-n','Number of permutations; 0 requests exhaustive enumeration.','integer',5000,minimum=0)
    s.option('--seed','Random seed.','integer',minimum=0)
    for flag,desc in [('-T','Threshold-Free Cluster Enhancement.'),('-x','Voxelwise corrected p-value images.'),('-D','Demean data temporally.'),('-R','Output raw statistic images.'),('--uncorrp','Output uncorrected p-value images.'),('-P','Save permutation vectors.'),('-N','Save null distributions.')]:s.flag(flag,desc)
    s.option('-c','Cluster-forming threshold.');s.option('-v','Variance smoothing standard deviation in mm.',minimum=0)
    s=Spec('fsl','smoothest','Estimate residual smoothness for cluster inference.')
    s.args('-m',s.inp('mask','brain_mask'));s.par('source','Use residuals or a Z-statistic image.',default='zstat',choices=['zstat','residuals'])
    s.args('-z',s.inp('zstat','z_stat_map','Z-statistic image.',False,{'source':['zstat']}),when={'source':['zstat']})
    s.args('-r',s.inp('residuals',required=False,when={'source':['residuals']}),'-d',s.par('dof','Residual degrees of freedom.','number',minimum=1,when={'source':['residuals']}),when={'source':['residuals']})
    s.out('report','execution_log','command.log')
    s=Spec('ants','Atropos','Classify tissue intensities with native Atropos and an explicit mask.')
    s.dimension('-d');s.args('-a',s.inp(),'-x',s.inp('mask','brain_mask'),'-o',s.out('labels','label_image'))
    s.par('initialization','Initialization without implicit external prior files.',default='KMeans',choices=['KMeans','Otsu','Random'])
    s.par('classes','Number of tissue classes.','integer',3,minimum=2,maximum=50)
    for mode in ['KMeans','Otsu','Random']:s.args('-i',P('classes',prefix=mode+'[',suffix=']'),when={'initialization':[mode]})
    s.option('-c','Convergence [iterations,threshold].','string',pattern=r'\[\d+,[0-9.eE+-]+\]')
    s.option('-m','MRF [smoothingFactor,radius-per-axis].','string',pattern=r'\[[0-9.]+,\d+(x\d+){0,3}\]')
    s.option('-r','Use random seed (1) or deterministic seed (0).','integer',0,[0,1])
    s=Spec('mrtrix3','mtnormalise','Multi-tissue log-domain intensity normalization of one to three tissue compartments.')
    s.par('tissues','Number of ordered tissue input/output pairs; this controls argv construction, not a new algorithm.','integer',3,[1,2,3])
    for index,key in enumerate(['wm','gm','csf'],1):
        when={'tissues':list(range(index,4))}
        s.args(s.inp(key,'fod_image' if key=='wm' else 'scalar_image',f'{key.upper()} tissue compartment.',False,when),
               s.out(key+'_normalized','fod_image' if key=='wm' else 'scalar_image',key+'_normalized.mif',when),when=when)
    s.args('-mask',s.inp('mask','brain_mask','Required brain mask for intensity normalization.'))
    s.option('-order','Maximum polynomial order.','string','3',['0','1','2','3'])
    s.option('-niter','Main-loop iterations and optional balance-loop iterations.','string','15,7',pattern=r'\d+(,\d+)?')
    s.option('-reference','Positive reference intensity.',minimum=0.000001)
    s.flag('-balanced','Apply tissue balancing factors; changes AFD interpretation and requires scientific justification.')
    for flag,kind,suffix in [('check_norm','scalar_image','.mif'),('check_mask','brain_mask','.mif'),('check_factors','generated_file','.txt')]:
        s.par('save_'+flag,'Save '+flag+' diagnostic output.','boolean',False)
        s.args('-'+flag,s.out(flag,kind,flag+suffix,{'save_'+flag:[True]}),when={'save_'+flag:[True]})
    s.option('-nthreads','CPU threads within the allocated scheduler budget.','integer',1,minimum=1)
    s=Spec('mrtrix3','mrregister','Symmetric rigid, affine or nonlinear image/FOD registration.')
    s.args(s.inp('moving'),s.inp('fixed'))
    types=['rigid','affine','nonlinear','rigid_affine','rigid_nonlinear','affine_nonlinear','rigid_affine_nonlinear']
    s.option('-type','Native registration stages.','string','affine_nonlinear',types)
    s.args('-transformed',s.out('registered','registered_image','registered.mif'))
    for flag,choices in [('rigid',[v for v in types if 'rigid' in v]),('affine',[v for v in types if 'affine' in v])]:
        s.args('-'+flag,s.out(flag,'transform_matrix',flag+'.txt',{'type':choices}),when={'type':choices})
    nl=[v for v in types if 'nonlinear' in v]
    s.args('-nl_warp',s.out('forward_warp','imaging_input','forward.mif',{'type':nl}),s.out('inverse_warp','imaging_input','inverse.mif',{'type':nl}),when={'type':nl})
    for flag in ['mask1','mask2']:s.args('-'+flag,s.inp(flag,'brain_mask','Optional registration mask.',False),optional=True)
    s.flag('-no_reorientation','Disable automatic FOD reorientation; use only when scientifically appropriate.')
    for prefix in ['rigid','affine','nl']:
        s.option('-'+prefix+'_niter','Iteration count per resolution level.','string',pattern=r'\d+(,\d+)*')
    s.option('-nthreads','CPU threads within the allocated scheduler budget.','integer',1,minimum=1)
    s=Spec('mrtrix3','mrcalc','Voxelwise arithmetic with one native operator per Method; use Pipeline steps for longer expressions.')
    unary=['-abs','-neg','-sqrt','-exp','-log','-log10','-sin','-cos','-tan','-asin','-acos','-atan','-round','-ceil','-floor','-isnan','-isinf','-finite','-real','-imag','-phase','-conj']
    binary=['-add','-subtract','-multiply','-divide','-pow','-min','-max','-lt','-le','-gt','-ge','-eq','-neq','-and','-or','-xor']
    help_text=(ROOT/'deploy/software-contracts/mrtrix3/mrcalc.usage.txt').read_text(encoding='utf-8')
    available=set(re.findall(r'^OPTION (\S+)',help_text,re.M))
    unary=[v for v in unary if v[1:] in available];binary=[v for v in binary if v[1:] in available]
    s.par('operation','Native voxelwise operator.',default='-abs',choices=unary+binary)
    s.par('operand_kind','Second operand source; relevant only for binary operators.',default='scalar',choices=['scalar','image']);s.parameters[-1]['visible_when']={'operation':binary}
    s.args(s.inp())
    when={'operation':binary,'operand_kind':['scalar']}
    s.args(s.par('value','Scalar second operand.','number',1,when=when),when=when)
    when={'operation':binary,'operand_kind':['image']}
    s.args(s.inp('operand',required=False,when=when),when=when)
    s.args(P('operation'),s.out('output','imaging_input','output.mif'))
    s.option('-nthreads','CPU threads within the allocated scheduler budget.','integer',1,minimum=1)
    s=Spec('mrtrix3','tckconvert','Convert a tractogram to VTK polydata for external visualization.');s.args(s.inp('tracks','tractogram','Input streamline file.'),s.out('tracks','tractogram','tracks.vtk'));s.option('-nthreads','CPU threads.','integer',1,minimum=1)


def build():
    fsl_specs();ants_specs();freesurfer_specs();mrtrix_specs();extra_specs()
    allowlist={};definitions=[]
    for s in SPECS:
        identity=s.family+'.command.'+s.command
        path=ROOT/'deploy/software-contracts/mrtrix3'/(s.command+'.usage.txt') if s.family=='mrtrix3' else HELP/(s.family+'-'+s.command+'.txt')
        help_text=path.read_text(encoding='utf-8')
        if len(help_text)<25:raise ValueError('Missing meaningful installed help: '+s.command)
        root,env=ROOTS[s.family]
        # Every exposed literal flag must be evidenced in the installed help.
        for group in s.argv:
            for token in group['tokens']:
                if isinstance(token,str) and token.startswith('-') and token not in help_text and s.family!='mrtrix3':
                    raise ValueError('Flag absent from installed help: '+s.command+' '+token)
        payload=dict(schema_version=1,id=identity,version='1',kind='method',label={'fsl':'FSL','ants':'ANTs','freesurfer':'FreeSurfer','mrtrix3':'MRtrix3'}[s.family]+' '+s.command,
            description=s.purpose+'\n\nThe interface exposes the typed options listed below; the original installed-version help is retained in source references.',runner_id=identity,
            parameters=s.parameters,input_slots=s.inputs,output_slots=s.outputs,input_types=list(dict.fromkeys(x['type'] for x in s.inputs)),output_types=list(dict.fromkeys(x['type'] for x in s.outputs)),
            source_references=[dict(path=path.relative_to(ROOT).as_posix(),label='Installed-version command help',kind='command')],
            execution=dict(kind='command',contract=dict(software=s.family,command=s.command,version=VERSION[s.family],argv=s.argv,patterns=s.patterns,complete_groups=s.complete,omitted_options=s.omitted)))
        definition=MethodDefinition.model_validate(payload)
        from app.method_validation import validate_contract
        validate_contract(definition)
        definitions.append(definition)
        allowlist[identity]=dict(software=s.family,command=s.command,version=VERSION[s.family],root=root,root_env=env)
    return definitions,allowlist


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true');args=parser.parse_args()
    definitions,allowlist=build()
    files={ROOT/'methods'/allowlist[d.id]['software']/d.id/'method.json':d.model_dump(mode='json') for d in definitions}
    files[ROOT/'bakend/resources/scientific-command-allowlist.json']=allowlist
    for path,data in files.items():
        text=json.dumps(data,ensure_ascii=False,indent=2)+'\n'
        if args.check:
            if not path.is_file() or path.read_text(encoding='utf-8')!=text:raise SystemExit('Stale scientific command: '+str(path))
        else:path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding='utf-8')
    print(str(len(definitions))+' reviewed scientific command definitions '+('checked' if args.check else 'generated'))
