"""Allowlisted native scientific commands with typed argv and managed outputs.

The manifest is shipped code, never supplied by a browser/model. Inputs are bound
paths; scalar parameters cannot become paths or shell fragments. Repeated input
arguments use bounded, separately authorized ports rather than wildcard strings.
"""
from __future__ import annotations
import json,os,re
from pathlib import Path

from ...method_registry import MethodRegistry, MethodDefinition
from ...parameter_rules import input_required, matches
from ...workflow_models import MethodOption
from ..base import RunnerResult
from .mrtrix_commands import MrtrixCommandRunner

ROOT=Path(__file__).resolve().parents[4]


def scientific_runners():
    manifest=json.loads((ROOT/'bakend/resources/scientific-command-allowlist.json').read_text(encoding='utf-8'))
    registry=MethodRegistry(ROOT)
    return [ScientificCommandRunner(registry.method(identity).model_dump(mode='json'), entry)
            for identity,entry in manifest.items()]


class ScientificCommandRunner(MrtrixCommandRunner):
    def __init__(self,payload,entry):
        definition=MethodDefinition.model_validate(payload)
        self.contract=payload['execution']['contract']
        self.entry=entry
        if self.contract['command']!=entry['command'] or self.contract['software']!=entry['software']:
            raise ValueError('Scientific command identity does not match code allowlist')
        self.runner_id,self.label=definition.runner_id,definition.label
        self.method=MethodOption(id=definition.id,skill_id='',definition_id=definition.id,
            definition_version=definition.version,**definition.contract())
        self.executable=Path(os.getenv(entry['root_env'],entry['root']))/'bin'/entry['command']

    def environment(self):
        root=self.executable.parent.parent
        environment={'PATH':str(self.executable.parent)+os.pathsep+os.environ.get('PATH',''),
                     'FSLOUTPUTTYPE':'NIFTI_GZ','OMP_NUM_THREADS':os.getenv('SLURM_CPUS_PER_TASK','1'),
                     'ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS':os.getenv('SLURM_CPUS_PER_TASK','1')}
        if self.entry['software']=='fsl':environment['FSLDIR']=str(root)
        if self.entry['software']=='freesurfer':
            environment.update(FREESURFER_HOME=str(root),SUBJECTS_DIR=str(root/'subjects'))
            environment['PATH']=str(root/'bin')+os.pathsep+str(root/'mni/bin')+os.pathsep+environment['PATH']
            if (root/'license.txt').is_file():environment['FS_LICENSE']=str(root/'license.txt')
        return environment

    def runtime_status(self):
        from ..external_command import ExternalCommandRunner
        status=ExternalCommandRunner.runtime_status(self)
        status.update(software=self.entry['software'],reviewed_version=self.entry['version'],
                      version_checked=False)
        if self.entry['software']=='mrtrix3' and status['available']:
            from .mrtrix_commands import version_probe
            version=version_probe(str(self.executable),self.executable.stat().st_mtime_ns)
            status.update(available=version==self.entry['version'],version=version,version_checked=True)
        # Availability is deliberately separate from recorded synthetic execution.
        return status

    def build_argv(self,context,*,allow_deferred=False):
        values=self.values(context)
        slots={s.id:s for s in self.method.input_slots}
        outputs={s.id:s for s in self.method.output_slots}
        if set(context.step.input_bindings)-slots.keys():raise ValueError('Unregistered input port')
        for slot in slots.values():
            if input_required(slot,values) and slot.id not in context.step.input_bindings:
                raise ValueError('Missing required input: '+slot.id)
        for key,pattern in self.contract.get('patterns',{}).items():
            if values.get(key) is not None and not re.fullmatch(pattern,str(values[key])):
                raise ValueError('Invalid structured value: '+key)
        for group in self.contract.get('complete_groups',[]):
            if any(values.get(k) is not None for k in group) and not all(values.get(k) is not None for k in group):
                raise ValueError('Set all related values: '+', '.join(group))
        def resolve(token):
            if isinstance(token,str):return [token]
            if 'when' in token and not matches(token['when'],values):return []
            if 'tokens' in token:return [v for child in token['tokens'] for v in resolve(child)]
            if 'input' in token:
                key=token['input'];slot=slots[key]
                if key not in context.step.input_bindings:
                    return [token['missing']] if 'missing' in token else []
                if allow_deferred and context.input_is_deferred(key):return ['<upstream:'+key+'>']
                if slot.cardinality=='many':
                    paths=self.binding_paths(context.step.input_bindings[key])
                    if not 1<=len(paths)<=64:raise ValueError('List input requires 1–64 files: '+key)
                    for item in paths:
                        if not item.is_file() or not self._allowed_binding_input(context.step.input_bindings[key],item.resolve(),context.output_directory.parent):raise ValueError('Unauthorized list input: '+key)
                    return [str(p.resolve()) for p in paths]
                path=self.required_binding(context,key)
                if (not path.is_dir() if slot.binding=='folder' else not path.is_file()) or not self._allowed_binding_input(context.step.input_bindings[key],path,context.output_directory.parent):
                    raise ValueError('Input must be an authorized '+slot.binding+': '+key)
                if slot.binding=='folder':
                    # Fixel directories are flat. Reject nested/symlink escapes
                    # before a native executable can follow them.
                    for n,child in enumerate(path.iterdir()):
                        if n>=10000 or child.is_dir() or not self._allowed_binding_input(context.step.input_bindings[key],child.resolve(),context.output_directory.parent):
                            raise ValueError('Unsupported or unauthorized directory entry: '+key)
                return [str(path)]
            if 'output' in token:
                output=outputs[token['output']]
                path=(context.output_directory/output.path_template).resolve()
                if not path.is_relative_to(context.output_directory.resolve()):raise ValueError('Output leaves run directory')
                if token.get('suffix'):
                    path=path/token['suffix']
                    if not path.resolve().is_relative_to(context.output_directory.resolve()):raise ValueError('Output prefix leaves run directory')
                if token.get('relative_to_output'):
                    parent=context.output_directory/outputs[token['relative_to_output']].path_template
                    return [path.relative_to(parent.resolve()).as_posix()]
                return [str(path)]
            key=token['parameter'];value=values.get(key)
            if value is None:return []
            # Arbitrary text is never accepted. Strings must be enumerated or
            # have a registered numerical/structured grammar.
            spec=next(p for p in self.method.parameters if p.id==key)
            if spec.type=='string' and not spec.options and key not in self.contract.get('patterns',{}):
                raise ValueError('Unreviewed text parameter: '+key)
            text=('1' if value else '0') if type(value) is bool else str(value)
            return [token.get('prefix','')+text+token.get('suffix','')]
        def group(group):
            if not matches(group.get('when',{}),values):return []
            tokens=group['tokens']
            resolved=[resolve(token) for token in tokens]
            if any(not part for token,part in zip(tokens,resolved) if isinstance(token,dict) and not token.get('optional') and ('parameter' in token or 'input' in token)):
                # An optional flag is omitted unless all its arguments exist.
                if group.get('optional') and not any(part for token,part in zip(tokens,resolved) if isinstance(token,dict) and ('parameter' in token or 'input' in token)):return []
                raise ValueError('Incomplete command arguments')
            # FSL's OptionParser requires --long=value, including paths with
            # spaces. Keep each pair in one argv element; never shell-quote it.
            result=[]; position=0
            while position<len(tokens):
                token=tokens[position]
                if (self.entry['software']=='fsl' and isinstance(token,str) and token.startswith('--')
                        and position+1<len(tokens) and isinstance(tokens[position+1],dict)
                        and len(resolved[position+1])==1):
                    result.append(token+'='+resolved[position+1][0]);position+=2
                else:
                    result.extend(resolved[position]);position+=1
            return result
        return [str(self.executable), *[token for g in self.contract['argv'] for token in group(g)]]

    def run(self,context):
        values=self.values(context)
        outputs=[s for s in context.method.output_slots if matches(s.generated_when,values)]
        for output in outputs:
            path=context.output_directory/output.path_template
            if path.exists():raise ValueError('Output already exists: '+output.id)
            if output.format=='directory' and any(isinstance(token,dict) and token.get('output')==output.id and token.get('suffix') for group in self.contract['argv'] for token in group['tokens']):path.mkdir(parents=True)
            elif not any(parent.format=='directory' and path.is_relative_to(context.output_directory/parent.path_template)
                         for parent in outputs):
                path.parent.mkdir(parents=True,exist_ok=True)
        log=context.output_directory/'command.log'
        self.execute(context,self.build_argv(context),environment=self.environment(),cwd=context.output_directory,log_path=log)
        artifacts=[]
        for output in outputs:
            path=context.output_directory/output.path_template
            valid=path.is_dir() and any(p.is_file() for p in path.iterdir()) if output.format=='directory' else path.is_file() and path.stat().st_size>0
            if not valid:raise RuntimeError('Command did not create output: '+output.id)
            artifacts.append((self.directory_artifact if output.format=="directory" else self.artifact)(context,path,output.type,output.label,metadata={'output_slot_id':output.id,'command':self.entry['command']}))
        return RunnerResult(artifacts=artifacts,message=self.label+' completed')
