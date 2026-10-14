"""Produce a public, method-level readiness inventory without patient data or secrets."""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import re
import sys
import subprocess

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'bakend'))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--runtime-env',type=Path)
    parser.add_argument('--slurm-node', help='Probe the configured shared runtime on one named Slurm node')
    args=parser.parse_args()
    if args.runtime_env:
        from dotenv import dotenv_values
        from app.execution.slurm_worker import RUNTIME_ENV_ALLOWLIST
        values=dotenv_values(args.runtime_env)
        for key in RUNTIME_ENV_ALLOWLIST:
            if values.get(key) is not None:os.environ[key]=values[key]
    if args.slurm_node:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',args.slurm_node):
            parser.error('Invalid Slurm node name')
        from app.execution.slurm_worker import RUNTIME_ENV_ALLOWLIST
        env={key:os.environ[key] for key in RUNTIME_ENV_ALLOWLIST if key in os.environ}
        env.update(PATH='/usr/local/bin:/usr/bin:/bin',LANG='C.UTF-8')
        python=os.getenv('LOCALFLOW_SLURM_PYTHON','/opt/neuroweave/envs/scientific-py311/bin/python')
        result=subprocess.run(['srun','--nodes=1','--ntasks=1','--nodelist='+args.slurm_node,
            '--partition=all','--cpus-per-task=2','--mem=4G','--time=00:10:00','--job-name=nw-runtime-audit',
            python,'-B',str(Path(__file__).resolve()),'--output',str(args.output.resolve())],env=env)
        raise SystemExit(result.returncode)
    from app.capability_registry import CapabilityRegistry
    from app.execution.runner_registry import RunnerRegistry
    registry=CapabilityRegistry(ROOT/'skills');runners=RunnerRegistry()
    runtime={entry['runner_id']:entry for entry in runners.public_catalog()}
    records=[]
    for capability in registry.capabilities():
        for method in registry.selectable_methods_for(capability):
            members=registry.recipe_methods(method.execution_recipe) if method.execution_recipe else [method]
            states=[runtime.get(member.runner_id,{'available':False,'reason':'执行器未注册'}) for member in members]
            state=runtime.get(method.runner_id)
            ready=all(s.get('available') for s in states)
            status='planning' if method.execution_mode!='runner' else 'ready' if ready else 'missing_runtime'
            reason=(method.execution_resolution.get('reason') or '尚未核对到具体执行入口') if status=='planning' else '；'.join(dict.fromkeys(s['reason'] for s in states if status=='ready' or not s.get('available')))
            records.append(dict(capability=capability,skill=method.skill_id,method_id=method.method_id,label=method.label,runner_id=method.runner_id,status=status,reason=reason,
                                execution_resolution=method.execution_resolution,
                                recipe_runners=[member.runner_id for member in members] if method.execution_recipe else [],
                                implementation_source=state.get('implementation_source') if state else None))
    skills=[]
    registry.definitions.load()
    for identity in sorted(registry.definitions.skills):
        own=[m for m in records if m['skill']==identity]
        skills.append(dict(skill=identity,**dict(Counter(m['status'] for m in own))))
    summary=dict(skills=len(skills),skills_with_ready_methods=sum(s.get('ready',0)>0 for s in skills),
                 registered_runners=len(runtime),ready_runners=sum(bool(s.get('available')) for s in runtime.values()),
                 selectable_methods=len(records),**dict(Counter(m['status'] for m in records)))
    result=dict(generated_at=datetime.now(timezone.utc).isoformat(),node=os.getenv('SLURMD_NODENAME',__import__('socket').gethostname()),
                summary=summary,registry=registry.stats(),skills=skills,
                missing_runners=[dict(runner_id=s['runner_id'],reason=s['reason']) for s in runtime.values() if not s.get('available')],methods=records)
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False));print(json.dumps(result['missing_runners'],ensure_ascii=False))


if __name__=='__main__':main()
