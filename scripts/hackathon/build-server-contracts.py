"""Generate the reviewed AGH tool schemas from existing business contracts."""
import json
from copy import deepcopy
from pathlib import Path

from app.agh_server import SavedRouteDraft
from app.run_models import RunExecutionOptions
from app.workspace_tools import TOOLS, WORKSPACE_PROMPT, WRITE_ACTIONS
from app.data_tools import DATA_TOOLS, PROPOSAL_TOOLS
from app.skill_catalog import SKILL_TOOLS
from app.skill_loader import SkillLoader
from app.goal_planner import ROUTE_TOOL
from app.agent_state import STATE_TOOLS, FinishTask

ROOT = Path(__file__).resolve().parents[2]
mutations = set(WRITE_ACTIONS) | PROPOSAL_TOOLS
tools = []
goal_schema = deepcopy(STATE_TOOLS[0]['function']['parameters'])
goal_schema['properties']['declared_inputs']['description'] = '逐字引用用户明确说已有或缺失的输入；已有 available=true，明确没有 available=false。未声明的输入保持未知，留给卡片选择。'
goal_schema['$defs']['DeclaredInput']['properties']['artifact_type']['description'] = '使用已读取的 Method 输入槽 type/artifact_type，不能把 WM/GM/CSF 等角色名当成类型；这些角色放入 slot_id。'
for entry in [*TOOLS, *DATA_TOOLS, *SKILL_TOOLS]:
    definition = entry['function']
    name = definition['name']
    tools.append({'name': 'nw_' + name, 'business_name': name,
        'description': definition['description'],
        'parameters': definition['parameters'], 'mutation': name in mutations})

(ROOT/'integrations/agh/server-contracts.json').write_text(json.dumps({
    'tools': tools, 'workflow': SavedRouteDraft.model_json_schema(), 'execution': RunExecutionOptions.model_json_schema(),
    'route': ROUTE_TOOL['function']['parameters'], 'goal': goal_schema,
    'finish': FinishTask.model_json_schema()}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(f'Generated {len(tools)} business tool contracts and workflow schema')
skills = [{'name': skill.name, 'description': skill.description[:1000],
           'body': skill.prompt_excerpt(max_chars=8000)} for skill in SkillLoader(ROOT/'skills').load_all()]
(ROOT/'integrations/agh/domain-skills.json').write_text(json.dumps(skills, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(f'Generated {len(skills)} professional Skill contributions')
