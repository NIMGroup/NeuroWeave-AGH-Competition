"""Canonical facts, checked file containers and immediate manual-plan delivery."""
# Ruff cannot infer pytest's implicit fixture imports and argument binding.
# ruff: noqa: F811
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.capability_registry import CapabilityRegistry
from app.goal_planner import GoalPlanner, RouteQuery
from app.pipeline_compiler import PipelineCompiler
from app.pipeline_models import ComposeRequest
from app.execution.recipe_compiler import RecipeCompiler, RecipeCompilationError
from app.workflow_models import InputBinding
from app.agent_artifacts import normalize_summary
from app.agent_team import AgentTeam, specialist_tools
from test_agent_team import TeamProvider, setup, goal
from test_data_operations import data as data
from test_goal_planner import query
from test_workflow_service import SKILLS_DIR


def test_port_aliases_and_input_order_share_one_route_cache():
    registry = CapabilityRegistry(SKILLS_DIR)
    first = query().model_dump()
    first['task_contract']['allowed_existing_inputs'] = []
    original = first['existing_inputs']
    first['existing_inputs'] = [dict(item, slot_id='bvals' if item['path'].endswith('.bval') else
        'bvecs' if item['path'].endswith('.bvec') else '') for item in original]
    canonical = RouteQuery.model_validate(first)
    first['existing_inputs'] = list(reversed(first['existing_inputs'])) + [
        dict(item, artifact_type='b_values' if item['path'].endswith('.bval') else 'b_vectors',
             slot_id='bval' if item['path'].endswith('.bval') else 'bvec')
        for item in original if item['artifact_type'] == 'gradient_table']
    equivalent = RouteQuery.model_validate(first)
    assert equivalent.model_dump() == canonical.model_dump()
    planner = GoalPlanner(registry)
    state, result = planner.search(canonical, {})
    repeat, reused = planner.search(equivalent, {}, state)
    assert result['status'] == 'matched' and reused['summary_reused']
    assert repeat['expansions'] == state['expansions']


def test_distinct_semantic_roles_are_preserved_and_conflicts_are_explicit():
    raw = query().model_dump()
    raw['existing_inputs'] = [{'artifact_type':'response_functions', 'path':'response.txt', 'slot_id':role}
        for role in ['wm_response','gm_response']]
    assert len(RouteQuery.model_validate(raw).existing_inputs) == 2
    raw['existing_inputs'] = [{'artifact_type':'b_values','path':'dwi.bval','features':{'count':n}}
        for n in [12,13]]
    with pytest.raises(ValueError, match='互相冲突'):
        RouteQuery.model_validate(raw)


def test_raw_dwi_without_mask_gets_an_explicit_compatible_conversion_route():
    registry = CapabilityRegistry(SKILLS_DIR)
    raw = query().model_dump()
    raw['task_contract']['allowed_existing_inputs'] = []
    raw['existing_inputs'] = [{'artifact_type':t,'path':p,'slot_id':s}
        for t,p,s in [('raw_dwi','dwi.nii.gz',''),('gradient_table','dwi.bval','bvals'),
                     ('gradient_table','dwi.bvec','bvecs')]]
    state, result = GoalPlanner(registry).search(RouteQuery.model_validate(raw), {})
    assert result['status'] == 'matched', result
    route = state['routes'][0]
    assert 'mrtrix.dwi-to-nifti' in [s['method_id'] for s in route['steps']]
    compiled = PipelineCompiler(registry).compile(ComposeRequest(name='raw',steps=route['steps']))
    assert compiled['steps']
    assert len({(b['path'],b['artifact_type']) for b in route['bindings']}) == 3


def test_incompatible_formats_are_rejected_in_planning_and_expanded_execution():
    registry = CapabilityRegistry(SKILLS_DIR)
    graph=[{'id':'denoise','method_id':'mrtrix.command.dwidenoise'},
           {'id':'mask','method_id':'reviewed.dipy-tool.mask',
            'inputs':{'dwi':{'step_id':'denoise','output_slot_id':'out'}}}]
    with pytest.raises(ValueError,match='格式不匹配'):
        PipelineCompiler(registry).compile(ComposeRequest(name='invalid',steps=graph))
    registry.definitions.load()
    units=[SimpleNamespace(step=SimpleNamespace(id='denoise',title='denoise',input_bindings={},selected_parameters={}),
                           method=registry.definitions.method('mrtrix.command.dwidenoise')),
           SimpleNamespace(step=SimpleNamespace(id='mask',title='mask',selected_parameters={},
               input_bindings={'dwi':InputBinding(slot_id='dwi',artifact_type='preprocessed_dwi',
                   source='upstream_output',upstream_step_id='denoise',output_slot_id='out')}),
                           method=registry.definitions.method('reviewed.dipy-tool.mask'))]
    with pytest.raises(RecipeCompilationError,match='格式不匹配'):
        RecipeCompiler._validate_upstream_inputs(units)


def test_workflow_tools_keep_targeted_candidates_but_not_broad_catalog_discovery():
    tools=[{'function':{'name':n}} for n in ['list_skill_methods','read_skill_method','read_method_routes','list_skills']]
    assert [t['function']['name'] for t in specialist_tools(tools,{'deliverable':'workflow'},'methods')] == [
        'list_skill_methods','read_skill_method','read_method_routes']
    assert specialist_tools(tools,{'deliverable':'answer'},'methods') == tools
    assert specialist_tools(tools,{'deliverable':'workflow','method_planning':{'state':{
        'summary':{'status':'unsupported'}}}},'methods') == tools


def test_fact_fields_are_self_describing_and_missing_fields_keep_saved_evidence(data):
    (data.output/'a.txt').write_text('value')
    owner,req=setup(data,TeamProvider())
    team=AgentTeam(owner,req)
    result=asyncio.run(team.query('data','inspect_output_path',{'path':'a.txt'}))
    header=team.facts.read(req,{'fact_id':result['fact_id']})
    assert header['available_fields']
    missing=team.facts.read(req,{'fact_id':result['fact_id'],'field':'imaginary'})
    assert missing['status']=='field_not_found' and missing['available_fields']==header['available_fields']
    assert team.facts.read(req,{'entity_id':result['entity_id']})['available_fields']


def test_saving_workflow_is_pending_and_skips_optional_compaction(data,monkeypatch):
    provider=TeamProvider(coordinator=[[goal(),('prepare_workflow',{'goal':'test','steps':[]})],
        [('finish_task',{'status':'awaiting_run','summary':'已准备','artifact_ids':['wf-test']})]])
    owner,req=setup(data,provider)
    owner.workflow_service.get=lambda identity: SimpleNamespace(revision=1,title='test',steps=[],
        input_selection='user',contract_errors=[],planning_diagnostics={},sample_scope=None)
    team=AgentTeam(owner,req)
    team.ledger.record(req,'prepare_workflow',{},
        {'id':'wf-test','kind':'workflow','status':'pending','revision':1,'missing_inputs':[]})
    assert req.assistant_task['prepared_this_turn']==['wf-test']
    # Exercise the same post-batch decision used for all proposal tools.
    team.compression.compact=AsyncMock()
    import app.agent_runtime as runtime
    import app.agent_plan_config as config
    # This unit isolates delivery/compaction with a minimal workflow stub;
    # real configuration attachment is covered by runtime policy tests.
    monkeypatch.setattr(config,'attach_workflow_configuration',lambda *_: {})
    real_execute=runtime.execute
    async def execute(owner,ledger,request,name,args,observations):
        if name=='prepare_workflow':
            return {'id':'wf-test','kind':'workflow','status':'pending','revision':1,'missing_inputs':[]}
        return await real_execute(owner,ledger,request,name,args,observations)
    monkeypatch.setattr(runtime,'execute',execute)
    req.assistant_task['prepared_this_turn']=[]
    result=asyncio.run(team.run())
    assert result.assistant_task['phase']=='awaiting_run'
    team.compression.compact.assert_not_awaited()


def test_escaped_paragraphs_are_rendered_without_decoding_code_and_paths():
    text=r'已准备。\n范围如下：\n- 样本一\n\n`C:\new\name`'+'\n```python\nprint("a\\nb")\n```'
    result=normalize_summary(text)
    assert '已准备。\n范围如下：\n- 样本一\n\n' in result
    assert r'`C:\new\name`' in result and 'print("a\\nb")' in result


def test_reviewed_reader_rejects_mif_before_invoking_python(tmp_path):
    from app.execution.runners.reviewed import ReviewedRunner
    from app.method_registry import MethodRegistry
    reader=object.__new__(ReviewedRunner)
    reader.spec=MethodRegistry(SKILLS_DIR.parent).reviewed_specs()['reviewed.dipy-tool.mask']
    paths={slot:tmp_path/name for slot,name in [('dwi','data.mif'),('bval','data.bval'),('bvec','data.bvec')]}
    for path in paths.values():
        path.touch()
    reader.binding_paths=lambda binding:[paths[binding]]
    reader._allowed_binding_input=Mock(return_value=True)
    context=SimpleNamespace(step=SimpleNamespace(input_bindings={key:key for key in paths}),output_directory=tmp_path/'output')
    with pytest.raises(ValueError,match='格式不兼容'):
        reader.checked_inputs(context)
    paths['dwi']=tmp_path/'data.nii.gz'
    paths['dwi'].touch()
    assert reader.checked_inputs(context)==paths


def test_workflow_batch_does_not_require_data_before_route_selection(data):
    from app.agent_facts import packed
    owner,req=setup(data,TeamProvider())
    team=AgentTeam(owner,req)
    team.ledger.update_goal(req,goal('workflow')[1])
    observed=[]
    async def invoke(call,tools,**kwargs):
        args=json.loads(call['function']['arguments'])
        observed.append(args)
        return {'agent':args['agent']},None
    team.call=invoke
    calls=[{'id':role,'function':{'name':'delegate_agent','arguments':packed({'agent':role,'question':role})}}
        for role in ['methods','data']]
    asyncio.run(team.call_batch(calls,[]))
    assert {a['agent'] for a in observed} == {'methods','data'}
    assert all(not a.get('depends_on') for a in observed)


def test_old_method_snapshots_require_replanning_before_any_execution():
    registry=CapabilityRegistry(SKILLS_DIR)
    method=RecipeCompiler._method_option(registry.method_for('method','reviewed.dipy-tool.mask'))
    method.definition_version='obsolete'
    step=SimpleNamespace(id='mask',title='mask',order=0,selected_method_id=method.id,method_options=[method])
    with pytest.raises(RecipeCompilationError,match='契约已更新'):
        RecipeCompiler(registry).compile(SimpleNamespace(steps=[step]))


def test_matched_route_can_be_reported_without_reading_every_contract(data):
    provider=TeamProvider(methods=[[('read_method_routes',{})],
        [('finish_agent',{'answer':'路线已核验','fact_ids':[],'unresolved':[],
            'selected_methods':['mrtrix.tckgen'], 'findings':[{'question':'选路','status':'confirmed',
                'conclusion':'登记图已核验','basis':'inference','fact_ids':[],'checks':[]}]})]])
    owner,req=setup(data,provider)
    team=AgentTeam(owner,req)
    team.ledger.update_goal(req,goal('workflow')[1])
    async def query(role,name,args):
        team.ledger.save(req,{**req.assistant_task,'method_planning':{'task_id':req.assistant_task['task_id'],
            'task_revision':req.assistant_task['task_revision'],
            'state':{'routes':[{'steps':[{'method_id':'mrtrix.tckgen'}]}], 'summary':{
                'status':'matched','routes':[{'route_id':'route:validated','method_ids':['mrtrix.tckgen']}]}}}})
        return {'status':'matched','routes':[{'route_id':'route:validated','method_ids':['mrtrix.tckgen']}]}
    team.query=query
    team.compression.compact=AsyncMock()
    result=asyncio.run(team.delegate({'agent':'methods','question':'选路'}))
    assert result['selected_methods']==['mrtrix.tckgen']
    assert len(provider.requests)==1 and result['planning_complete']
    assert result['route_ids']==['route:validated'] and result['unresolved']==[]
    team.compression.compact.assert_not_awaited()


def test_plan_delivery_summary_is_short_without_limiting_regular_answers():
    from app.agent_state import STATE_TOOLS
    from app.agent_team import coordinator_tools
    ordinary=next(t for t in coordinator_tools(STATE_TOOLS,{}) if t['function']['name']=='finish_task')
    prepared=next(t for t in coordinator_tools(STATE_TOOLS,{'prepared_this_turn':['plan']}) if t['function']['name']=='finish_task')
    assert ordinary['function']['parameters']['properties']['summary']['maxLength']==4000
    assert prepared['function']['parameters']['properties']['summary']['maxLength']==800


def test_incomplete_workflow_keeps_fact_queries_open_instead_of_forcing_finish(data):
    owner,req=setup(data,TeamProvider())
    team=AgentTeam(owner,req)
    ready={'id':'plan','kind':'workflow','status':'pending','revision':1,'missing_inputs':[],'inputs_ready':True}
    team.ledger.record(req,'prepare_workflow',{},ready)
    assert req.assistant_task['prepared_this_turn']==['plan']
    incomplete={**ready,'revision':2,'missing_inputs':[{'slot':'input'}],'inputs_ready':False}
    team.ledger.record(req,'prepare_workflow',{},incomplete)
    assert req.assistant_task['prepared_this_turn']==[] and 'plan' in req.assistant_task['artifacts']
    with pytest.raises(ValueError,match='必需输入'):
        team.ledger.finish(req,{'status':'awaiting_run','summary':'不应称为可运行','artifact_ids':['plan']})


def test_scientific_preflight_uses_gradient_roles_instead_of_parent_table_type(tmp_path):
    from app.execution.scientific_checks import scientific_checks
    from app.artifact_semantics import canonical_input
    paths={slot:tmp_path/name for slot,name in [('bvals','values.bval'),('bvecs','vectors.bvec'),('grad','scheme.txt')]}
    contents={'bvals':'0 1000','bvecs':'0 1\n0 0\n0 0','grad':'0 0 0 0\n1 0 0 1000'}
    bindings={}
    for slot,path in paths.items():
        path.write_text(contents[slot])
        bindings[slot]=InputBinding(slot_id=slot,artifact_type='gradient_table',source='existing_path',absolute_path=str(path))
    context=SimpleNamespace(step=SimpleNamespace(id='test',input_bindings=bindings),
        method=SimpleNamespace(parameters=[]))
    rows=scientific_checks(context)
    assert len(rows)==3 and all(r['status']=='passed' and r['details']['count']==2 for r in rows)
    assert canonical_input('gradient_table','values.txt','bvals')==('b_values','')
    with pytest.raises(ValueError,match='端口冲突'):
        canonical_input('gradient_table','values.bval','bvecs')
    paths['bvecs'].write_text('1 0\n0 1')
    assert any(r['status']=='error' and r['slot_id']=='bvecs' for r in scientific_checks(context))
