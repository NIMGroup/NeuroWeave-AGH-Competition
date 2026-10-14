"""Thin argv adapters for explicitly reviewed, versioned MRtrix entrypoints."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import subprocess
from functools import lru_cache

from ...workflow_models import MethodOption
from ...parameter_rules import active, required, conflicts, matches, input_required
from ..base import RunnerResult
from ..external_command import ExternalCommandRunner
from .mrtrix import MrtrixTckgenRunner

ROOT=Path(__file__).resolve().parents[4]
COMMANDS=('5tt2gmwmi', '5tt2vis', '5ttedit', 'afdconnectivity', 'amp2sh', 'dirflip', 'dirgen', 'dirmerge', 'dirorder', 'dwi2adc', 'dwi2mask', 'dwi2tensor', 'dwidenoise', 'dwiextract', 'fixel2sh', 'fixel2tsf', 'fixel2voxel', 'fod2dec', 'label2colour', 'label2mesh', 'labelconvert', 'maskdump', 'maskfilter', 'mesh2voxel', 'meshconvert', 'meshfilter', 'mrcheckerboardmask', 'mrcolour', 'mrconvert', 'mrdegibbs', 'mrdump', 'mredit', 'mrgrid', 'mrhistmatch', 'mrhistogram', 'mrthreshold', 'mrtransform', 'peaks2amp', 'sh2amp', 'sh2peaks', 'sh2power', 'sh2response', 'shconv', 'tckdfc', 'tckglobal', 'tckmap', 'tckresample', 'tcksample', 'tcksift', 'tcksift2', 'tckstats', 'tcktransform', 'tensor2metric', 'transformcalc', 'transformconvert', 'tsfdivide', 'tsfsmooth', 'tsfthreshold', 'voxel2mesh', 'warp2metric', 'warpconvert', 'warpcorrect', 'warpinit', 'warpinvert')


def command_runners():
    from ...method_registry import MethodRegistry
    registry=MethodRegistry(ROOT)
    registry.load()
    return [MrtrixCommandRunner(method.model_dump(mode='json')) for method in registry.methods.values()
            if method.id.startswith('mrtrix.') and method.execution.get('kind') == 'command'
            and method.execution.get('contract', {}).get('command') in COMMANDS]


@lru_cache(maxsize=256)
def version_probe(path, mtime):
    try:
        result=subprocess.run([path,'-version'],capture_output=True,text=True,timeout=10,
                              env={**os.environ,**MrtrixTckgenRunner._environment()})
        match=re.search(r'== \S+ (\d+\.\d+\.\d+)',result.stdout+result.stderr)
        return match.group(1) if result.returncode==0 and match else ''
    except (OSError,subprocess.TimeoutExpired):
        return ''


class MrtrixCommandRunner(ExternalCommandRunner):
    def __init__(self,payload):
        self.contract=payload['execution']['contract']
        from ...method_registry import MethodDefinition
        definition=MethodDefinition.model_validate(payload).contract()
        self.runner_id,self.label=definition['runner_id'],definition['label']
        self.method=MethodOption(id=payload['id'],skill_id='',definition_id=payload['id'],definition_version=payload['version'],**definition)
        command=self.contract['command']
        if command not in COMMANDS:raise ValueError('Unreviewed MRtrix command')
        self.executable=Path(os.getenv('LOCALFLOW_MRTRIX_ROOT','/opt/software/mrtrix3-master'))/'bin'/command

    def runtime_status(self):
        status=super().runtime_status()
        if status['available']:
            version=version_probe(str(self.executable),self.executable.stat().st_mtime_ns)
            status.update(version=version,available=version==self.contract['version'],
                reason='已核对 MRtrix3 '+version if version==self.contract['version'] else '命令版本与审核定义不一致，需要重新核对')
        return status

    def values(self,context):
        declared={p.id:p for p in self.method.parameters}
        if set(context.step.selected_parameters)-set(declared):raise ValueError('包含未审核命令参数')
        values={p.id:context.step.selected_parameters.get(p.id,p.default) for p in declared.values()}
        for p in declared.values():
            if not active(p,values):continue
            value=values[p.id]
            if value is None:
                if required(p,values):raise ValueError('缺少必填参数：'+p.label)
                continue
            valid=(type(value) is bool if p.type=='boolean' else type(value) is int if p.type=='integer'
                else type(value) in {int,float} if p.type=='number' else isinstance(value,str))
            if not valid:raise ValueError('参数类型不正确：'+p.id)
            if p.options and value not in [o.value for o in p.options]:raise ValueError('非法选项：'+p.id)
            if p.type in {'integer','number'}:
                if not math.isfinite(value) or (p.minimum is not None and value<p.minimum) or (p.maximum is not None and value>p.maximum):raise ValueError('数值超出范围：'+p.id)
            if p.type=='string' and (len(value)>2000 or any(c in value for c in ('\x00','\n','\r'))):raise ValueError('参数文本无效：'+p.id)
            if conflicts(p,values):raise ValueError('互斥参数：'+p.id+' / '+', '.join(conflicts(p,values)))
        allocated=os.getenv('SLURM_CPUS_PER_TASK')
        if allocated and max(1,values.get('nthreads') or 1)>int(allocated):raise ValueError('线程数超过 Slurm 分配的 CPU 核数')
        if values.get('min_iters') is not None and values.get('max_iters') is not None and values['min_iters']>values['max_iters']:raise ValueError('最少迭代次数不能超过最多迭代次数')
        return values

    def build_argv(self,context,*,allow_deferred=False):
        values=self.values(context)
        inputs={s.id:s for s in self.method.input_slots}
        outputs={s.id:s for s in self.method.output_slots}
        if set(context.step.input_bindings)-set(inputs):raise ValueError('包含未审核的输入')
        for slot in inputs.values():
            needed=input_required(slot,values)
            if needed and slot.id not in context.step.input_bindings:raise ValueError('缺少输入：'+slot.label)
        def resolve(value):
            key=value['id']
            if value['source']=='input':
                if key not in context.step.input_bindings:return None
                if allow_deferred and context.input_is_deferred(key):return '<upstream:'+key+'>'
                path=self.required_binding(context,key)
                if not path.is_file() or not self._allowed_binding_input(context.step.input_bindings[key],path,context.output_directory.parent):raise ValueError('输入必须为授权范围内的文件：'+key)
                return str(path)
            if value['source']=='output':
                target=(context.output_directory/outputs[key].path_template).resolve()
                if not target.is_relative_to(context.output_directory.resolve()):raise ValueError('输出越界')
                return str(target)
            current=values.get(key)
            if current is None:return None
            kind=value.get('kind')
            if kind in {'ISEQ','FSEQ'}:
                pattern=r'-?\d+(,-?\d+)*' if kind=='ISEQ' else r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?(?:,[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)*'
                if not re.fullmatch(pattern,str(current)):raise ValueError('需要明确的逗号分隔数值列表：'+key)
                parts=str(current).split(',')
                if key.endswith(('_start','_mid','_end')) and len(parts)!=3:raise ValueError('坐标必须包含三个数值：'+key)
                if self.contract['command']=='mrdegibbs' and key=='axes' and (len(parts)!=2 or len(set(parts))!=2 or any(p not in {'0','1','2'} for p in parts)):raise ValueError('处理平面需要两个不同空间轴（0、1、2）')
                if self.contract['command']=='dwidenoise' and key=='extent' and (len(parts) not in {1,3} or any(int(p)<1 or int(p)%2==0 for p in parts)):raise ValueError('窗口需要一个或三个正奇数')
            return ('1' if current else '0') if kind=='BOOL' else str(current)
        argv=[str(self.executable)]
        for value in self.contract['positional']:
            text=resolve(value)
            if text is None:
                if value.get('optional'):continue
                raise ValueError('缺少位置输入：'+value['id'])
            argv.append(text)
        for option in self.contract['options']:
            if any(p.id=='gradient_source' for p in context.method.parameters) and option['flag'] in {'-grad','-fslgrad'}:
                continue
            if not matches(option.get('when',{}),values):continue
            texts=[resolve(v) for v in option['values']]
            if texts and any(t is None for t in texts):
                if any(t is not None for t in texts):raise ValueError('选项参数不完整：'+option['flag'])
                continue
            argv.extend([option['flag'],*texts])
        if any(p.id=='gradient_source' for p in context.method.parameters):
            from ..gradient_policy import gradient_arguments
            argv.extend(context.prepared_options['gradient_args'] if 'gradient_args' in context.prepared_options else gradient_arguments(context)[0])
        return argv

    def preflight(self,context):
        problems=self.common_preflight(context)
        try:self.build_argv(context,allow_deferred=context.is_dry_run)
        except (ValueError,RuntimeError,OSError) as exc:problems.append(str(exc))
        return problems

    def run(self,context):
        self.execute(context,self.build_argv(context),environment=MrtrixTckgenRunner._environment(),cwd=context.output_directory)
        values=self.values(context)
        artifacts=[]
        for output in context.method.output_slots:
            if not matches(output.generated_when,values):continue
            path=context.output_directory/output.path_template
            if not path.is_file() or path.stat().st_size==0:raise RuntimeError('命令未生成有效输出：'+output.id)
            artifacts.append(self.artifact(context,path,output.type,output.label,metadata={'output_slot_id':output.id,'command':self.contract['command']}))
        return RunnerResult(artifacts=artifacts,message=self.label+'完成')
