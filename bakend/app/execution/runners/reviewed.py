"""Execute the packaged, reviewed method catalog through bounded argv contracts."""
from __future__ import annotations

import json
import hashlib
import math
import os
import subprocess
import re
from functools import lru_cache
from pathlib import Path

from ..base import RunnerResult
from ...parameter_rules import active
from ..external_command import ExternalCommandRunner
from ...method_registry import MethodRegistry

ROOT = Path(__file__).resolve().parents[4]


@lru_cache(maxsize=1)
def specifications() -> dict[str, dict]:
    retired = set(json.loads((ROOT/'bakend/resources/withdrawn_execution.json').read_text(encoding='utf-8'))['runner_ids'])
    result = {}
    for item in MethodRegistry(ROOT).reviewed_specs().values():
        if item['runner_id'] in retired:
            raise ValueError('已撤回的接口不能重新注册：'+item['runner_id'])
        if item.get('implementation_source', {}).get('kind') not in {'existing_project_script','library_api','software_command'}:
            raise ValueError('方法缺少现有实现来源：'+item['runner_id'])
        result[item['runner_id']] = item
    return result


@lru_cache(maxsize=128)
def probe(python: str, modules: tuple[str, ...], apis: tuple[tuple[str, str], ...] = ()) -> tuple[bool, str]:
    if not Path(python).is_file() or not os.access(python, os.X_OK):
        return False, 'Python 入口不存在：' + python
    code = '''import importlib, importlib.util, json
missing = []
for module in MODULES:
    try:
        if importlib.util.find_spec(module) is None: missing.append(module)
    except (ImportError, AttributeError, ValueError): missing.append(module)
for module, symbol in APIS:
    try:
        value = importlib.import_module(module)
        for name in symbol.split('.'): value = getattr(value, name)
        if not callable(value): missing.append(module+'.'+symbol)
    except Exception: missing.append(module+'.'+symbol)
print(json.dumps(missing))
'''.replace('MODULES', repr(list(modules))).replace('APIS', repr(list(apis)))
    try:
        result = subprocess.run([python, '-B', '-s', '-c', code], capture_output=True, text=True, timeout=20)
        missing = json.loads(result.stdout) if result.returncode == 0 else ['依赖探测失败']
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False, '无法探测 Python 依赖'
    return (False, '缺少依赖或现有 API：' + ', '.join(missing)) if missing else (True, '现有实现入口与依赖可用；运行前仍检查输入')


class ReviewedRunner(ExternalCommandRunner):
    def __init__(self, spec: dict):
        self.spec = spec
        self.runner_id, self.label = spec['runner_id'], spec['label']
        profile = spec.get('profile', 'scientific')
        default = '/opt/neuroweave/envs/' + ('torch-cu128-py311' if profile == 'torch' else 'scientific-py311') + '/bin/python'
        self.executable = Path(os.getenv('LOCALFLOW_TORCH_PYTHON' if profile == 'torch' else 'LOCALFLOW_SKILL_PYTHON', default))
        self.script = ROOT / spec['script']

    def runtime_status(self):
        available, reason = probe(str(self.executable), tuple(self.spec.get('modules', [])), tuple(tuple(api) for api in self.spec.get('required_apis', [])))
        if not self.spec.get('implementation_files'):
            available, reason = False, '缺少实现源码校验记录'
        for item in self.spec.get('implementation_files', []):
            path = (ROOT/item['path']).resolve()
            if not path.is_relative_to(ROOT) or not path.is_file():
                available, reason = False, '缺少记录的实现源码：'+item['path']
                continue
            actual = hashlib.sha256(path.read_text(encoding='utf-8-sig').encode('utf-8')).hexdigest()
            if actual != item['sha256']:
                available, reason = False, '实现源码已变化，需重新核对来源：'+item['path']
        if not self.script.is_file():
            available, reason = False, '缺少已审核实现：' + str(self.script)
        for path in self.spec.get('required_files', []):
            if not (ROOT / path).is_file():
                available, reason = False, '缺少模型源码：' + path
        for path in self.spec.get('executables', []):
            if not os.access(path, os.X_OK):
                available, reason = False, '缺少软件入口：' + path
        return {'available': available, 'reason': reason, 'executable': str(self.executable),
                'script': str(self.script), 'environment_profile': self.spec.get('profile', 'scientific'),
                'required_modules': self.spec.get('modules', []),
                'implementation_source': self.spec.get('implementation_source', {})}

    def parameters(self, context):
        declared = {p['id']: p for p in self.spec.get('parameters', [])}
        unknown = set(context.step.selected_parameters) - set(declared) - {'operation'}
        if unknown:
            raise ValueError('未审核参数：' + ', '.join(sorted(unknown)))
        result = {}
        schemas = {p.id:p for p in getattr(getattr(context, 'method', None), 'parameters', [])}
        for name, definition in declared.items():
            if name in schemas and not active(schemas[name], context.step.selected_parameters):
                continue
            value = context.step.selected_parameters.get(name, definition.get('default'))
            if value is None or value == '':
                if definition.get('required', True):
                    raise ValueError('缺少参数：' + name)
                continue
            kind = definition['type']
            valid = (isinstance(value, bool) if kind == 'boolean' else
                     isinstance(value, int) and not isinstance(value, bool) if kind == 'integer' else
                     isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value) if kind == 'number' else
                     isinstance(value, str) and len(value) <= 2000 and '\x00' not in value and '\n' not in value)
            if not valid:
                raise ValueError('参数类型或数值无效：' + name)
            choices = [option['value'] for option in definition.get('options', [])]
            if choices and value not in choices:
                raise ValueError('参数不在审核选项中：' + name)
            for boundary, comparison in [('minimum', lambda a,b:a<b), ('maximum', lambda a,b:a>b)]:
                if definition.get(boundary) is not None and comparison(value, definition[boundary]):
                    raise ValueError('参数超出范围：' + name)
            result[name] = value
        return result

    def checked_inputs(self, context, *, allow_deferred=False):
        result = {}
        deferred = set()
        declared = {item['id']: item for item in self.spec['inputs']}
        if set(context.step.input_bindings) - set(declared):
            raise ValueError('存在未审核的输入绑定')
        for name, definition in declared.items():
            binding = context.step.input_bindings.get(name)
            if allow_deferred and context.input_is_deferred(name):
                deferred.add(name)
                continue
            paths = self.binding_paths(binding) if binding else []
            if not paths and not definition.get('required', True):
                continue
            if len(paths) != 1:
                raise ValueError('输入必须绑定一个文件或目录：' + name)
            path = paths[0].resolve(strict=True)
            if not self._allowed_binding_input(binding, path, context.output_directory.parent):
                raise ValueError('输入超出授权数据根：' + name)
            if definition.get('binding') == 'folder':
                if not path.is_dir():
                    raise ValueError('输入需要目录：' + name)
                # Do not allow recursively followed links to cross the input boundary.
                for child in path.rglob('*'):
                    if child.is_symlink() and not child.resolve().is_relative_to(path):
                        raise ValueError('目录含指向外部的链接：' + str(child))
            else:
                if not path.is_file():
                    raise ValueError('输入需要文件：' + name)
                from ...artifact_semantics import accepts_format
                if not accepts_format(path, definition.get('formats', [])):
                    raise ValueError('输入文件格式不兼容：' + name + '；需要 ' + ', '.join(definition['formats']))
            if definition.get('path_list'):
                for value in path.read_text(encoding='utf-8').splitlines():
                    if not value.strip():
                        continue
                    nested = Path(value.strip())
                    if not nested.is_absolute():
                        nested = path.parent / nested
                    nested = nested.resolve(strict=True)
                    if not nested.is_file() or not self._allowed_input(nested, context.output_directory.parent):
                        raise ValueError('影像清单含未授权路径：' + value)
            result[name] = path
        if self.spec.get('require_any_input', True) and not result and not deferred:
            raise ValueError('至少绑定一项分析输入')
        return result

    def preflight(self, context):
        problems = self.common_preflight(context)
        try:
            self.parameters(context)
            self.checked_inputs(context, allow_deferred=context.is_dry_run)
        except (ValueError, OSError) as exc:
            problems.append(str(exc))
        return problems

    def run(self, context):
        problems = self.preflight(context)
        if problems:
            raise RuntimeError('；'.join(problems))
        parameters, inputs = self.parameters(context), self.checked_inputs(context)
        output = context.output_directory / 'results'
        output.mkdir(parents=True, exist_ok=True)
        argv = [str(self.executable), '-B', '-s', str(self.script), *self.spec.get('fixed_args', [])]
        for definition in self.spec['inputs']:
            name = definition['id']
            if name not in inputs:
                continue
            path = inputs[name]
            if definition.get('path_list'):
                normalized = output.parent / (name + '-resolved.txt')
                resolved = [(Path(line.strip()) if Path(line.strip()).is_absolute() else path.parent/line.strip()).resolve(strict=True)
                            for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
                normalized.write_text('\n'.join(str(p) for p in resolved) + '\n', encoding='utf-8')
                if not resolved:
                    raise ValueError('输入路径清单为空：'+name)
                if definition.get('many_args'):
                    argv.extend([definition['flag'], *map(str,resolved)])
                    continue
                path = normalized
            argv.extend([definition['flag'], str(path)])
        for definition in self.spec.get('parameters', []):
            name = definition['id']
            if name not in parameters:
                continue
            value = parameters[name]
            flag = definition.get('flag', '--' + name.replace('_', '-'))
            if definition['type'] == 'boolean':
                if value == definition.get('flag_when', True):
                    argv.append(flag)
            elif definition.get('split'):
                argv.extend([flag, *str(value).split(',')])
            else:
                argv.extend([flag, str(value)])
        for flag, name in self.spec.get('outputs', [['--output-dir', '.']]):
            argv.extend([flag, str(output/name)])
        log = context.output_directory/'execution.log'
        env = {'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1', 'MPLBACKEND':'Agg',
               'PYTHONPATH':str(ROOT), 'MPLCONFIGDIR':str(context.output_directory/'.mpl'),
               'OMP_NUM_THREADS':os.getenv('SLURM_CPUS_PER_TASK', '1'),
               'OPENBLAS_NUM_THREADS':os.getenv('SLURM_CPUS_PER_TASK', '1')}
        self.execute(context, argv, environment=env, cwd=context.output_directory, log_path=log)
        paths = sorted(p for p in output.rglob('*') if p.is_file())
        if any(p.is_symlink() or not p.resolve().is_relative_to(output.resolve()) for p in output.rglob('*')):
            raise RuntimeError('结果中存在未授权链接')
        required = self.spec.get('expected', [])
        if not paths or any(not list(output.glob(pattern)) for pattern in required):
            raise RuntimeError('执行未生成约定的分析产物')
        if any(not (output/name).is_file() or not (output/name).stat().st_size for name in required if not any(c in name for c in '*?[')):
            raise RuntimeError('约定的分析产物为空或不是文件')
        self.execute(context,[str(self.executable),'-B','-s',str(ROOT/'bakend/app/execution/validate_products.py'),str(output)],
                     environment=env,cwd=context.output_directory,log_path=context.output_directory/'validation.log')
        # Type collected files from the run's immutable Method snapshot, not
        # from a possibly newer live adapter catalog.
        output_types = {s.path_template.removeprefix('results/'): s.type
                        for s in context.method.output_slots if s.path_template.startswith('results/')}
        artifacts = [self.artifact(context, p, output_types.get(p.relative_to(output).as_posix(),'generated_file'), p.name,
            metadata={'output_slot_id':'file_'+re.sub('[^a-zA-Z0-9_]+','_',p.relative_to(output).as_posix())} if p.relative_to(output).as_posix() in required else None)
                     for p in paths if p.stat().st_size]
        if not artifacts:
            raise RuntimeError('分析产物为空')
        artifacts.append(self.directory_artifact(context,output,'generated_folder','分析结果目录',metadata={'output_slot_id':'results'}))
        artifacts.append(self.artifact(context, log, 'execution_log', '执行日志', 'text/plain'))
        context.log('分析完成，已验证并登记输出', 100.0, 'info')
        return RunnerResult(artifacts=artifacts, message=self.label + '完成。')
