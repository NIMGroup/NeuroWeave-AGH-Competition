"""Read-only catalog integrity audit; never executes software or contacts a model."""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'bakend'))
from app.method_registry import MethodRegistry
from app.method_validation import documentation_gaps


def audit(root=ROOT):
    store=MethodRegistry(root);store.load()
    errors=[];generic=[];conditions=0;types=Counter()
    specs=store.reviewed_specs()
    for mid,m in store.methods.items():
        errors.extend(f'{mid}: {gap}' for gap in documentation_gaps(m))
        for key in ('input','output'):
            ports=getattr(m,key+'_slots')
            if set(getattr(m,key+'_types'))!={p.type for p in ports}:
                errors.append(f'{mid}: stale {key}_types')
        for port in m.output_slots:
            types[port.type]+=1
            if port.generated_when:conditions+=1
            if not port.path_template:errors.append(f'{mid}:{port.id}: missing output path')
            if port.type in {'generated_file','generated_folder','imaging_input'}:
                generic.append({'method_id':mid,'output_id':port.id,'type':port.type,'description':port.description})
            if mid in specs and port.id!='results':
                path=port.path_template.removeprefix('results/')
                if specs[mid]['artifact_types'].get(path)!=port.type:
                    errors.append(f'{mid}:{port.id}: runtime output type drift')
    return {'methods':len(store.methods),'skills':len(store.skills),'pipelines':len(store.pipelines),
            'conditional_outputs':conditions,'output_types':dict(sorted(types.items())),
            'errors':errors,'generic_ports':generic,
            'limitations':'Catalog validation is not runtime execution or scientific validation. Generic containers and images remain generic unless a concrete semantic contract is established.'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args();report=audit()
    if args.output:args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({k:v for k,v in report.items() if k!='generic_ports'}|{'generic_ports':len(report['generic_ports'])},ensure_ascii=False))
    raise SystemExit(bool(report['errors']))
