"""Build reviewed MRtrix command contracts from a pinned local help snapshot.

Only the six explicitly reviewed entrypoints below are executable. This is not
runtime command discovery. Source option descriptions/arity are preserved; all
paths are separately bound and output filenames are owned by the platform.
"""
from pathlib import Path
import argparse
import json
import math
import re

ROOT=Path(__file__).resolve().parents[1]
SKILL=ROOT/'skills/dwi-skill'
SOURCES=SKILL/'references/mrtrix-3.0.8'
COMMANDS={
 'tcksift2':('SIFT2 独立流线赋权', ['tractogram','fod_image'], 'streamline_weights', 'weights.txt'),
 'dwidenoise':('MP-PCA DWI 去噪', ['raw_dwi'], 'preprocessed_dwi', 'denoised.mif'),
 'mrdegibbs':('Gibbs 振铃校正', ['imaging_input'], 'imaging_input', 'degibbs.mif'),
 '5tt2gmwmi':('从已有 5TT 生成灰白质界面种子', ['five_tt_image'], 'seed_mask', 'gmwmi.mif'),
 'mrthreshold':('影像阈值处理', ['scalar_image'], 'scalar_image', 'threshold.mif'),
 'tckresample':('流线顶点重采样', ['tractogram'], 'tractogram', 'resampled.tck'),
}
MANAGED={'force':'运行输出目录独立分配，不允许覆盖输入或其他运行。',
 'config':'不开放任意软件配置键，以免绕过路径、线程及运行环境管理。',
 'help':'通过命令说明面板提供，不作为计算参数。', 'version':'由运行环境探测提供。',
 'output_debug':'调试影像没有稳定输出槽位，暂未接入。'}
LABELS={'nthreads':'CPU 线程数','min_iters':'最少迭代次数','max_iters':'最多迭代次数',
 'reg_tv':'总变分正则化系数','reg_tikhonov':'Tikhonov 正则化系数','fd_scale_gm':'结合 ACT 缩放灰质纤维密度',
 'abs':'绝对强度阈值','percentile':'百分位阈值','top':'保留最高值体素数','bottom':'保留最低值体素数',
 'axes':'处理平面轴编号','extent':'去噪窗口大小','estimator':'噪声估计器','datatype':'输出数据类型',
 'num_points':'每条流线的顶点数','step_size':'重采样步长','upsample':'上采样倍数','downsample':'下采样倍数',
 'mask':'掩膜','act':'ACT 五组织影像','proc_mask':'处理权重掩膜','mask_in':'限制界面种子的掩膜',
 'min_factor':'最小流线权重','min_coeff':'最小对数系数','max_factor':'最大流线权重','max_coeff':'最大对数系数'}


def parse(name):
    lines=(SOURCES/(name+'.usage.txt')).read_text(encoding='utf-8').splitlines()
    pos=[]; options=[]; introduction=[]; current=None; described=None
    for line in lines:
        if line.startswith('OPTION '):
            tokens=line.split(); current=dict(name=tokens[1], repeat=tokens[3]=='1', args=[], description='')
            options.append(current)
            described=current
        elif line.startswith('ARGUMENT '):
            tokens=line.split(); arg=dict(name=tokens[1], optional=tokens[2]=='1', repeat=tokens[3]=='1', kind=tokens[4], extra=tokens[5:])
            (current['args'] if current else pos).append(arg)
            described=arg
        elif line.strip():
            if described is None:
                introduction.append(line.strip())
            else:
                described['description']=(described.get('description','')+'\n'+line.strip()).strip()
    return pos,options,'\n'.join(introduction)


def parameter(identifier, arg, description, flag):
    kind=arg['kind']
    p=dict(id=identifier,argument=flag,label=LABELS.get(identifier,identifier),description=description,
        required=False,default=None,type={'INT':'integer','FLOAT':'number'}.get(kind,'string'),
        software_default='未设置时不传入该选项；默认行为以 MRtrix3 3.0.8 原始说明为准。',
        documentation_url='https://mrtrix.readthedocs.io/en/3.0.8/reference/commands/'+CURRENT+'.html')
    if kind=='CHOICE':
        p.update(control='select',options=[dict(value=v,label=v) for v in arg['extra']])
    if kind in {'INT','FLOAT'}:
        for key,value in zip(('minimum','maximum'),arg['extra']):
            number=float(value)
            if math.isfinite(number) and abs(number)<=9007199254740991:
                p[key]=int(number) if kind=='INT' else number
    if kind in {'ISEQ','FSEQ'}:
        p['usage']='逗号分隔的数值；本接口仅接受明确列表，不接受路径或任意表达式。'
    return p


def build(name):
    global CURRENT
    CURRENT=name
    label,in_types,out_type,filename=COMMANDS[name]
    pos,options,introduction=parse(name)
    inputs=[]; outputs=[]; parameters=[]; bindings=[]; positional=[]; omitted={}
    for index,arg in enumerate(pos):
        if arg['kind'].endswith('OUT'):
            outputs.append(dict(id=arg['name'],type=out_type,label='输出：'+label,description=arg.get('description','')+' 文件名由平台固定在本次输出目录内。',
                format=Path(filename).suffix,primary=True,path_template=filename))
            positional.append(dict(source='output',id=arg['name']))
        else:
            inputs.append(dict(id=arg['name'],type=in_types[index],label=arg['name'],description=arg.get('description',''),
                required=True,binding='file',formats=['.tck'] if arg['kind']=='TRACKSIN' else ['.mif','.nii','.nii.gz']))
            positional.append(dict(source='input',id=arg['name']))
    for option in options:
        flag=option['name']; args=option['args']; desc=option['description']
        if flag in MANAGED or option['repeat']:
            omitted[flag]=MANAGED.get(flag,'重复选项尚未接入。');continue
        binding=dict(flag='-'+flag,values=[])
        if len(args)==1 and args[0]['kind'].endswith('IN'):
            slot_type='five_tt_image' if flag=='act' else 'brain_mask'
            inputs.append(dict(id=flag,type=slot_type,label=LABELS.get(flag,flag),description=desc,required=False,binding='file',formats=['.mif','.nii','.nii.gz']))
            binding['values']=[dict(source='input',id=flag)]
        elif len(args)==1 and args[0]['kind'].endswith('OUT'):
            enable='save_'+flag
            parameters.append(dict(id=enable,argument='-'+flag,label='保存 '+flag,description=desc,type='boolean',required=False,default=False))
            output_name=flag+('.mif' if args[0]['kind']=='IMAGEOUT' else '.csv' if flag=='csv' else '.txt')
            outputs.append(dict(id=flag,type='scalar_image' if args[0]['kind']=='IMAGEOUT' else 'generated_file',label=flag,description=desc,format=Path(output_name).suffix,
                path_template=output_name,generated_when={enable:[True]}))
            binding.update(values=[dict(source='output',id=flag)],when={enable:[True]})
        elif not args:
            parameters.append(dict(id=flag,argument='-'+flag,label=LABELS.get(flag,flag),description=desc,type='boolean',required=False,default=False))
            binding.update(when={flag:[True]})
        else:
            for arg in args:
                if arg['kind'] not in {'INT','FLOAT','ISEQ','FSEQ','CHOICE'}:
                    raise ValueError(f'Unreviewed argument type {name}/{flag}: {arg}')
                key=flag if len(args)==1 else flag+'_'+arg['name']
                p=parameter(key,arg,desc,'-'+flag)
                if arg['kind'] in {'ISEQ','FSEQ'}: p['usage']+=' 坐标向量应有三个数值；轴编号从 0 开始。'
                parameters.append(p)
                binding['values'].append(dict(source='parameter',id=key,kind=arg['kind']))
        bindings.append(binding)
    by_id={p['id']:p for p in parameters}
    if name=='tcksift2':
        for group in [('min_factor','min_coeff'),('max_factor','max_coeff')]:
            for key in group: by_id[key]['conflicts_with']=[other for other in group if other!=key]
        next(s for s in inputs if s['id']=='act')['required_when']={'fd_scale_gm':[True]}
        by_id['fd_scale_gm']['condition_help']='启用后必须提供 ACT 5TT 影像；不会自动生成 5TT。'
    if name in {'tckresample','mrthreshold'}:
        choices=['upsample','downsample','step_size','num_points','endpoints','line','arc'] if name=='tckresample' else ['auto','abs','percentile','top','bottom']
        default='num_points' if name=='tckresample' else 'auto'
        parameters.insert(0,dict(id='mode',argument='',label='重采样方式' if name=='tckresample' else '阈值确定方式',
            description='只启用所选方式对应的原软件选项；其他方式的草稿值不会传入命令。',type='string',required=True,default=default,
            control='select',options=[dict(value=v,label=LABELS.get(v,v)) for v in choices]))
        for binding in bindings:
            flag=binding['flag'][1:]
            if flag not in choices:continue
            binding['when']={'mode':[flag]}
            for value in binding['values']:
                if value['source']=='parameter':
                    p=by_id[value['id']]
                    p.update(visible_when={'mode':[flag]},required_when={'mode':[flag]},condition_help='当前所选方式需要此参数。')
            if flag=='endpoints':parameters=[p for p in parameters if p['id']!='endpoints']
        if name=='tckresample':by_id['num_points']['default']=100
        else:next(s for s in inputs if s['id']=='mask')['required_when']={'out_masked':[True]}
    # Numeric/string values are omitted unless explicitly set. nthreads is explicit
    # so the scheduler can validate it against the assigned CPU budget.
    by_id['nthreads'].update(default=1,required=True,group='计算资源',level='advanced')
    method=dict(capability='skill.dwi-skill',method_id='command-'+name,label='MRtrix3 '+label,
        description=f'独立调用 MRtrix3 3.0.8 的 {name}；保留原软件计算，参数按原版本帮助定义。\n\n'+introduction,
        runner_id='mrtrix.command.'+name,execution_mode='runner',input_types=list(dict.fromkeys(s['type'] for s in inputs)),
        output_types=list(dict.fromkeys(s['type'] for s in outputs)),input_slots=inputs,output_slots=outputs,parameters=parameters,
        source_references=[dict(path=f'skills/dwi-skill/references/mrtrix-3.0.8/{name}.md',label='安装版本原始命令帮助',kind='command')])
    for key in ('capability','method_id','execution_mode'):method.pop(key)
    return dict(schema_version=1,id=method['runner_id'],version='1',kind='method',**method,
        requires_features=[],included_capabilities=[],command_references=[],
        execution=dict(kind='command',contract=dict(command=name,version='3.0.8',positional=positional,options=bindings,
            total_options=len(options),omitted_options=omitted)))



if __name__=='__main__':
    import sys
    sys.path.insert(0,str(ROOT/'bakend'))
    from app.method_registry import MethodDefinition
    parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true');args=parser.parse_args()
    for name in COMMANDS:
        value=MethodDefinition.model_validate(build(name)).model_dump(mode='json'); path=ROOT/'methods/mrtrix3'/('mrtrix.command.'+name)/'method.json'
        if args.check:
            if not path.is_file() or MethodDefinition.model_validate_json(path.read_text(encoding='utf-8')).model_dump(mode='json')!=value:raise SystemExit('Stale command: '+name)
        else:
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'{len(COMMANDS)} reviewed command contracts checked' if args.check else f'{len(COMMANDS)} reviewed command contracts generated')
