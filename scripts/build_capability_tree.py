"""Derive a functional capability graph from native Skill/Method/Pipeline refs.

The tree is a compact navigation/routing hint, not the executable registry. Adding
an atomic command changes its Skill references and therefore its tree membership;
there is no second hand-maintained NeuroClaw Skill taxonomy to drift out of date.
"""
from pathlib import Path
from collections import Counter
import argparse,json,sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'bakend'))
from app.capability_registry import CapabilityRegistry
from app.catalog_taxonomy import GROUPS,SUBGROUPS,catalog_entries,category_labels


def build_tree(skills=None):
    registry=CapabilityRegistry(ROOT/'skills')
    registry._load()
    entries=catalog_entries(registry.definitions)
    by_id={(entry['kind'],entry['id']):entry for entry in entries}
    nodes={}
    for cap in registry.capabilities():
        methods=registry.methods_for(cap)
        mids=sorted({m.definition_id for m in methods if m.entry_kind=='method'})
        pids=sorted({m.definition_id for m in methods if m.entry_kind=='pipeline'})
        sids=sorted({m.skill_id for m in methods if m.skill_id in registry.definitions.skills})
        rows=[by_id[('method',i)] for i in mids]+[by_id[('pipeline',i)] for i in pids]
        if not rows:rows=[by_id[('skill',i)] for i in sids]
        category=Counter(tuple(r['category_ids']) for r in rows).most_common(1)[0][0] if rows else ('data','guidance')
        precise={'dwi.preprocess':('preprocessing','correction'),'dwi.tensor_metrics':('diffusion','tensor'),
            'dwi.response':('diffusion','response'),'dwi.fod':('diffusion','response'),
            'dwi.tractography':('diffusion','tracking'),'dwi.five_tt':('segmentation','tissue'),
            'dwi.connectome':('diffusion','connectome'),'dwi.report':('quality','reports')}
        category=precise.get(cap,category)
        # Preserve registered capability IDs so scope exclusions and precise
        # native capabilities remain meaningful across saved conversations.
        skill=registry.definitions.skills.get(sids[0],{}) if len(sids)==1 else {}
        if skill.get('scope') == 'dataset':
            category = ('datasets', 'cohorts')
        if cap.startswith(('skill.','software.')) and skill:
            label=skill.get('name',skill['id']);description=skill.get('description','')
        else:
            label=methods[0].label if methods else cap
            description=methods[0].description if methods else ''
        nodes[cap]={'id':cap,'label':label,'description':description[:350],
            'keywords':list(dict.fromkeys([*sids,*skill.get('keywords',[])]))[:16],
            'skill_ids':sids,'method_ids':mids,'pipeline_ids':pids,'entity_kind':'capability',
            'execution_level':'structured' if mids or pids else 'planning','category_ids':list(category)}
    # Skills with guidance only still belong in the native catalog; aliases are
    # references, not duplicate capabilities or misleading executable entries.
    covered={s for node in nodes.values() for s in node['skill_ids']}
    for sid in sorted(set(registry.definitions.skills)-covered):
        entry=by_id['skill',sid]
        nodes['skill.'+sid]={'id':'skill.'+sid,'label':entry['label'],'description':entry['description'][:350],
            'skill_ids':[sid],'method_ids':entry['method_ids'],'pipeline_ids':entry['pipeline_ids'],
            'keywords':[sid], 'entity_kind':'capability','execution_level':'planning','category_ids':entry['category_ids']}
    branches=[]
    for group in GROUPS:
        children=[]
        for sub in SUBGROUPS:
            leaves=[node for node in nodes.values() if node['category_ids']==[group,sub]]
            if not leaves:continue
            children.append({'id':'function.'+group+'.'+sub,'label':category_labels([group,sub])[-1],
                'description':SUBGROUPS[sub][1],'execution_level':'category','entity_kind':'function',
                'children':sorted(leaves,key=lambda n:n['id'])})
        if children:
            branches.append({'id':'function.'+group,'label':GROUPS[group][0],'description':GROUPS[group][1],
                'execution_level':'category','entity_kind':'function','children':children})
    return {'schema_version':1,'architecture':'skill-method-pipeline','generated_from':'scripts/build_capability_tree.py',
        'root':{'id':'localflow-capabilities','label':'NeuroWeave 功能与执行资源',
            'description':'按处理目标导航。Skill 提供统筹指导，Method 是最小执行单位，Pipeline 保存可复用连接；树外的原生方法也参与全库检索。',
            'entity_kind':'function','execution_level':'category','children':branches}}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true');args=parser.parse_args()
    output=ROOT/'skills/capability.tree.json'
    text=json.dumps(build_tree(),ensure_ascii=False,indent=2)+'\n'
    if args.check:
        if output.read_text(encoding='utf-8')!=text:raise SystemExit('Native capability tree is stale')
    else:output.write_text(text,encoding='utf-8')
    print('Native capability tree checked' if args.check else 'Native capability tree generated')
