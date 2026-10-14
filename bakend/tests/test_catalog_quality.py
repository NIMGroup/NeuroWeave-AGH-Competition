"""Catalog boundaries: no duplicate choices, invalid contracts or inflated evidence."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.method_registry import MethodRegistry
from app.method_validation import contract_errors, documentation_gaps, execution_fingerprint, verification_status
from app.skill_definitions import native_skills
from app.skill_loader import SkillLoader
from app.skill_catalog import SKILL_PROMPT, SkillCatalog
from app.capability_registry import CapabilityRegistry

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope='module')
def store():
    registry = MethodRegistry(ROOT)
    registry.load()
    return registry


def test_canonical_catalog_keeps_every_method_but_no_alias_entries(store):
    loader = SkillLoader(ROOT / 'skills')
    assert {s.name for s in loader.load_all()} == set(store.skills)
    assert {r['method_ref'] for s in store.skills.values() for r in s['method_refs']} == set(store.methods)
    assert len(store.skills) == 105
    assert loader.find('software-fsl') is None
    assert all(
        'alias_of' not in json.loads(path.read_text(encoding='utf-8'))
        for path in (ROOT/'skills').glob('*/skill.json')
    )
    registry = CapabilityRegistry(ROOT/'skills')
    catalog = SkillCatalog(loader, registry, SimpleNamespace(get=lambda _: None))
    with pytest.raises(KeyError):
        catalog.list_methods('software-fsl')
    assert catalog.list_methods('fsl-tool')['total'] > 0
    for capability in registry.capabilities():
        choices = registry.selectable_methods_for(capability)
        keys = [(m.entry_kind, m.definition_id or m.method_id) for m in choices]
        assert len(keys) == len(set(keys)), capability


@pytest.mark.parametrize('raw', [
    {'a': {'alias_of': 'b'}, 'b': {'alias_of': 'a'}},
    {'a': {'alias_of': 'missing'}},
    {'a': {'alias_of': 'b', 'method_refs': [{}]}, 'b': {}},
])
def test_retired_alias_definitions_are_rejected(raw):
    with pytest.raises(ValueError):
        native_skills(raw)


def test_every_contract_has_valid_parameters_and_port_documentation(store):
    for method in store.methods.values():
        assert not contract_errors(method), method.id
        assert not documentation_gaps(method), method.id


@pytest.mark.parametrize('mutation, expected', [
    (lambda m: setattr(m.parameters[0], 'default', object()), 'default'),
    (lambda m: setattr(m.parameters[0], 'visible_when', {'invented': [True]}), 'unknown condition'),
    (lambda m: setattr(m.parameters[0], 'conflicts_with', ['invented']), 'conflict'),
    (lambda m: setattr(m.output_slots[0], 'generated_when', {'nthreads': [True]}), 'condition values'),
    (lambda m: setattr(m.input_slots[0], 'required_when_any', [{'nthreads': ['1']}]), 'condition values'),
])
def test_invalid_contracts_fail_before_execution(store, mutation, expected):
    method = store.method('mrtrix.command.mrconvert')
    mutation(method)
    assert any(expected in message for message in contract_errors(method))


def test_evidence_requires_matching_code_and_checked_report(store, tmp_path):
    method = store.method('mrtrix.command.mrconvert')
    assert verification_status(method, tmp_path)['execution']['status'] == 'not_recorded'
    directory = tmp_path/'deploy/method-verification'
    report = directory/'reports/smoke.json'
    report.parent.mkdir(parents=True)
    report.write_text('{"passed":true}')
    evidence = {'method_id': method.id, 'method_version': method.version,
        'execution_fingerprint': execution_fingerprint(method, tmp_path),
        'report_path': report.relative_to(tmp_path).as_posix(), 'report_sha256': hashlib.sha256(report.read_bytes()).hexdigest(),
        'test_type': 'synthetic', 'tested_at': '2026-09-23T00:00:00Z', 'environment': 'test fixture', 'result': 'passed'}
    record = directory/(method.id+'.json')
    record.write_text(json.dumps(evidence))
    assert verification_status(method, tmp_path)['execution']['status'] == 'passed'
    report.write_text('{}')
    assert verification_status(method, tmp_path)['execution']['status'] == 'invalid'
    method.description += ' revised'
    assert verification_status(method, tmp_path)['execution']['status'] == 'stale'


def test_skill_guidance_is_bounded_and_reuses_existing_inputs():
    skill = SkillLoader(ROOT/'skills').find('dwi-skill')
    excerpt = skill.prompt_excerpt()
    assert len(excerpt) <= 1805
    assert '5TT' in excerpt and '复用' in excerpt
    assert 'list_skill_methods' in SKILL_PROMPT
    assert 'list_skill_methods' not in excerpt
    assert '仅复用用户允许的结果' in SKILL_PROMPT
    assert '已有 5TT 时直接复用' in excerpt
    assert len(excerpt) < len(skill.instructions)


def test_native_definition_rejects_misspelled_conditions(store):
    from app.method_registry import MethodDefinition
    value = store.method('mrtrix.command.mrconvert').model_dump(mode='json')
    value['input_slots'][0]['require_when'] = {'nthreads': [1]}
    with pytest.raises(ValueError, match='Unknown native contract fields'):
        MethodDefinition.model_validate(value)
