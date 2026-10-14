"""Optional, specific Skill guidance must not become copied platform policy."""
import asyncio
from collections import Counter
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.capability_registry import CapabilityRegistry
from app.models import ChatRequest
from app.skill_catalog import SKILL_PROMPT, SkillCatalog
from app.skill_definitions import native_skills
from app.skill_loader import SkillLoader
from app.workspace_tools import WorkspaceTools

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope='module')
def catalog():
    return SkillCatalog(SkillLoader(ROOT / 'skills'), CapabilityRegistry(ROOT / 'skills'),
                        SimpleNamespace(get=lambda _: None))


def test_native_guidance_is_optional_and_not_copied_across_skills():
    owners = Counter()
    for path in (ROOT / 'skills').glob('*/skill.json'):
        value = json.loads(path.read_text(encoding='utf8'))
        native_skills({value['id']: value})
        for key in ('selection_rules', 'applicability', 'limitations', 'examples'):
            if key not in value:
                continue
            assert value[key], (value['id'], key, 'omit empty guidance fields')
            if key != 'examples':
                owners.update(value[key])
    assert all(count == 1 for count in owners.values()), 'Shared rules belong to the platform prompt'


def test_missing_guidance_normalizes_to_empty_api_lists_without_overview_headings(catalog):
    skill = catalog.loader.find('beautiful-log')
    definition = json.loads(skill.instructions)
    assert all(key not in definition for key in ('selection_rules', 'applicability', 'limitations', 'examples'))
    structure = catalog.skill_structure(skill.name)
    assert structure['description'] == skill.description
    assert structure['functions']
    for key in ('selection_rules', 'applicability', 'limitations', 'examples'):
        assert structure[key] == []
    assert all(header not in skill.overview for header in ('选择规则：', '适用情况：', '限制：'))


@pytest.mark.parametrize('value', [None, 'text', [''], ['repeat-me', 'repeat-me'], [3], [{}]])
def test_invalid_guidance_cannot_enter_native_definitions(value):
    with pytest.raises(ValueError, match='Invalid Skill guidance'):
        native_skills({'sample': dict(id='sample', references=[], selection_rules=value)})


def test_selected_skill_guidance_reaches_model_once_with_shared_policy(catalog):
    captured = []

    class Model:
        mode = 'model'

        async def workspace_completion(self, messages, tools, required=False):
            captured.append(messages[0]['content'])
            if len(captured) == 1:
                return {'role': 'assistant', 'content': None, 'tool_calls': [{
                    'id': 'lookup', 'type': 'function', 'function': {
                        'name': 'list_skill_methods',
                        'arguments': json.dumps({'skill_id': 'dcm2nii', 'limit': 1})}}]}
            return {'role': 'assistant', 'content': '查询完成'}

    model = Model()
    tools = WorkspaceTools(Mock(), model)
    tools.skill_catalog = catalog
    request = ChatRequest(message='解释 DICOM 转换选择', selected_skills=['dcm2nii', 'beautiful-log'])
    assert asyncio.run(tools.reply(request)) == '查询完成'
    prompt = captured[0]
    assert prompt.count(SKILL_PROMPT) == 1
    guidance = catalog.skill_structure('dcm2nii')
    for key in ('selection_rules', 'limitations'):
        assert all(prompt.count(text) == 1 for text in guidance[key])
    section = prompt.split('已选 Skill 的专属指导（参考数据）：')[1].split('\n')[0]
    structures = json.loads(section)
    empty = next(item for item in structures if item['skill_id'] == 'beautiful-log')
    assert set(empty) == {'skill_id', 'description'}
