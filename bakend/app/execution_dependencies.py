"""Conservative per-adapter source closure for reproducible execution evidence.

Registry factories enumerate unrelated adapters and are intentionally not traversed.
Unknown adapter resolution falls back to the full execution tree, never empty trust.
"""
import ast
from pathlib import Path
from functools import lru_cache

@lru_cache(maxsize=2048)
def _syntax(path,mtime,size):
    return ast.parse(Path(path).read_text(encoding='utf-8-sig'))

def syntax(path):
    stat=path.stat();return _syntax(str(path),stat.st_mtime_ns,stat.st_size)

def execution_sources(method,root):
    app=root/'bakend/app';execution=app/'execution'
    kind=method.execution.get('kind');seeds=set()
    if kind=='reviewed':
        seeds.add(execution/'runners/reviewed.py')
        spec=method.execution.get('adapter',method.execution)
        for item in spec.get('implementation_files',[]):seeds.add(root/item['path'])
        if spec.get('script'):seeds.add(root/spec['script'])
    elif kind=='command':
        contract=method.execution.get('contract',{})
        manifest=root/'bakend/resources/scientific-command-allowlist.json'
        import json
        scientific=method.id in json.loads(manifest.read_text(encoding='utf-8')) if manifest.exists() else False
        seeds.add(execution/'runners'/('scientific_commands.py' if scientific else 'mrtrix_commands.py'))
        if scientific:seeds.add(manifest)
    else:
        for file in (execution/'runners').glob('*.py'):
            tree=syntax(file)
            if any(isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='runner_id' for t in n.targets)
                   and isinstance(n.value,ast.Constant) and n.value.value==method.runner_id for n in ast.walk(tree)):
                seeds.add(file)
    if not seeds:seeds.update(execution.rglob('*.py'))
    seeds.update(execution/name for name in ('base.py','external_command.py','recipe_compiler.py','run_service.py','sample_execution.py','sample_plan.py','slurm_backend.py','slurm_worker.py','retry.py'))
    seeds.update(app/name for name in ('artifact_semantics.py','parameter_rules.py','workflow_models.py','method_validation.py','execution_dependencies.py'))
    visited=set();pending=list(seeds)
    while pending:
        path=pending.pop().resolve()
        if path in visited or not path.is_relative_to(root.resolve()) or not path.is_file():continue
        visited.add(path)
        if path.suffix!='.py':continue
        tree=syntax(path)
        for node in ast.walk(tree):
            candidates=[]
            if isinstance(node,ast.ImportFrom) and node.level:
                base=path.parent
                for _ in range(node.level-1):base=base.parent
                module=base.joinpath(*(node.module or '').split('.'))
                candidates=[module.with_suffix('.py'),module/'__init__.py']
            if isinstance(node,ast.Constant) and isinstance(node.value,str) and node.value.endswith('_worker.py') and '/' not in node.value:
                candidates.append(execution/node.value)
            for target in candidates:
                # Factories import every runner; they do not implement the selected command.
                if target.name not in {'runner_registry.py','method_registry.py','capability_registry.py','pipeline_library.py','local_store.py'}:pending.append(target)
    return sorted(visited)
