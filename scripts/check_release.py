#!/usr/bin/env python3
"""Check release structure and reviewed execution metadata without installed scientific tools."""
from pathlib import Path
import ast
import json
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "bakend"))
from app.capability_registry import CapabilityRegistry


def main():
    from app.method_registry import MethodRegistry
    from app.execution.runner_registry import RunnerRegistry
    store=MethodRegistry(root);store.load(force=True)
    from app.skill_loader import SkillLoader
    from app.skill_references import reference_path
    for skill in SkillLoader(root / 'skills').load_all():
        for reference in skill.references:
            reference_path(skill.path, reference)
    if set(store.methods) != RunnerRegistry().ids():
        raise RuntimeError('Atomic method catalog and Runner allowlist differ')
    # Native method.json is the authoritative reviewed contract. Bootstrap
    # generators predate curated semantics and must not erase those improvements.
    # Validate all registered execution token references, not generated prose.
    for method in store.methods.values():
        contract=method.execution.get('contract',{})
        slots={'input':{v.id for v in method.input_slots},'output':{v.id for v in method.output_slots},'parameter':{v.id for v in method.parameters}}
        def check_tokens(value):
            if isinstance(value,list):
                for child in value:check_tokens(child)
            elif isinstance(value,dict):
                source=value.get('source')
                if isinstance(source,str) and source in slots and value.get('id') not in slots[source]:raise RuntimeError('Unbound argv token: '+method.id+'/'+str(value))
                for key in slots:
                    if key in value and isinstance(value[key],str) and value[key] not in slots[key]:raise RuntimeError('Unbound argv token: '+method.id+'/'+str(value))
                for key,child in value.items():
                    if key not in {'patterns','omitted_options'}:check_tokens(child)
        check_tokens(contract)
    from catalog_translation import check as check_translations
    locale = check_translations(root)
    if locale['missing'] or locale['invalid_placeholders']:
        raise RuntimeError('Catalog translations are incomplete; run scripts/catalog_translation.py check')
    methods=list(store.methods.values())
    from app.method_validation import documentation_gaps
    for method in methods:
        if documentation_gaps(method):
            raise RuntimeError('Missing method documentation: '+method.id)
        if store.verification(method.id)['execution']['status'] == 'invalid':
            raise RuntimeError('Invalid execution evidence: '+method.id)
    referenced={ref['method_ref'] for skill in store.skills.values() for ref in skill['method_refs']}
    if set(store.methods) - referenced:
        raise RuntimeError('Methods missing from canonical Skill catalog')
    for p in store.pipelines.values():
        store.pipeline_recipe(p['id'])
    for path in (root / "scripts").glob("*.py"):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = json.loads((root / "frontend/package.json").read_text(encoding="utf-8"))
    lock = json.loads((root / "frontend/package-lock.json").read_text(encoding="utf-8"))["packages"][""]
    for key in ("dependencies", "devDependencies"):
        if package[key] != lock[key]:
            raise RuntimeError("Frontend dependency lock is stale")
    for filename in ("bakend/requirements.lock", "bakend/requirements-dev.lock"):
        for line in (root / filename).read_text(encoding="utf-8").splitlines():
            if line and not line.startswith(("#", "-r ")) and "==" not in line:
                raise RuntimeError("Unpinned backend dependency")
    print(f"Release structure valid: {len(methods)} atomic methods, {len(store.pipelines)} pipelines, {len(store.skills)} skills; dependency locks present")


if __name__ == "__main__":
    main()
