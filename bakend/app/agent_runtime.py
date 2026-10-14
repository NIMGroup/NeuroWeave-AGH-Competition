"""Deterministic plan tools, artifact checks and the coordinator entry point."""
from __future__ import annotations

import json
import copy
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .agent_state import GoalUpdate, STATE_TOOLS
from .assistant_memory import MEMORY_TOOLS
from .data_tools import DATA_TOOLS, PROPOSAL_TOOLS
from .models import ChatResponse
from .operation_context import OperationCancelled, operation_thread
from .skill_catalog import SKILL_TOOLS
from .task_contract import TaskContract, OutputReference, StageOutputReference, declared_input_scope
from .goal_grounding import ground_contract, save_grounding, intent_schema
from .method_planning import MethodPlan
from .goal_planner import ROUTE_TOOL, read_routes, saved_route
from .pipeline_models import ComposeStep
from .workflows.assistant_bindings import AssistantInput
from .agent_identity import public_understanding


class WorkflowDraftArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal: str = Field(min_length=1, max_length=4000)
    route_id: str = Field(default="", description="read_method_routes返回的真实路线ID；后端恢复完整步骤和输入，避免模型重新抄写图。")
    task_contract: TaskContract | None = Field(default=None, description="使用route_id时省略；直接提交steps时保留自然语言目标、中间阶段和用户输入范围，targets选择真实输出。")
    targets: list[OutputReference] = Field(default_factory=list, max_length=12,
        description='直接提交steps时选择真实最终输出端口，后端推导目标类型；已有路线省略。')
    stage_outputs: list[StageOutputReference] = Field(default_factory=list, max_length=12)
    capability_ids: list[str] = Field(default_factory=list, max_length=20, description='可选的真实能力ID。方法和Pipeline ID不是能力ID；已有明确步骤时省略。')
    method_ids: list[str] = Field(default_factory=list, max_length=20, description='可选的真实方法ID作为检索提示。')
    steps: list[ComposeStep] = Field(default_factory=list, max_length=20, description='已核实方法时直接提交完整步骤、参数和输入输出连接；后端校验，不再调用第二个模型重新规划。')
    input_bindings: list[AssistantInput] = Field(default_factory=list, max_length=80, description='可选的输入路径建议；不会自动绑定，用户在任务卡点击采用后才生效。路径未知可省略，不为此分派调查。')
    resolved_need_ids: list[str] = Field(default_factory=list, max_length=12, description='有真实证据且已解决的信息项，可与准备方案一起关闭。')


PLANNING_TOOL = {"type": "function", "function": {"name": "prepare_workflow",
    "description": "调用实际科学流程规划器，准备已登记 Method/Runner 的可审阅流程，不执行。规划失败作为工具反馈返回，可在本循环纠正。goal 保留用户已有输入和排除条件。",
    "parameters": WorkflowDraftArguments.model_json_schema()}}
intent_schema(PLANNING_TOOL['function']['parameters'])
ARTIFACT_TOOL = {"type": "function", "function": {"name": "list_workflow_artifact_types",
    "description": "只读查询实际科学产物类型 ID，供方法查询和 prepare_workflow 使用；未列出的类型不能编造。",
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}}
RESULT_TOOL = {"type": "function", "function": {"name": "read_data_result",
    "description": "只读分页读取已运行脚本日志或生成的文本产物。日志可能截断；引用完整后端产物，不把样例或报告当文件改动。",
    "parameters": {"type": "object", "properties": {"id": {"type": "string"}, "index": {"type": "integer", "minimum": 0},
        "offset": {"type": "integer", "minimum": 0}, "max_chars": {"type": "integer", "minimum": 1, "maximum": 12000}},
        "required": ["id"], "additionalProperties": False}}}
RUN_TOOL = {"type": "function", "function": {"name": "get_workflow_result",
    "description": "只读查询当前任务流程的真实运行状态、错误及产物。",
    "parameters": {"type": "object", "properties": {"workflow_id": {"type": "string"}},
                   "required": ["workflow_id"], "additionalProperties": False}}}


def available_tools(owner, request):
    from .agent_artifacts import ARTIFACT_READ_TOOL
    from .agent_execution_config import EXECUTION_CONFIG_TOOL
    from .workspace_tools import DATASET_TOOLS, OUTPUT_TOOLS, WRITE_ACTIONS
    task_mode = request.interaction_mode == "task"
    output = OUTPUT_TOOLS if request.workspace_id else []
    if not task_mode:
        output = [t for t in output if t["function"]["name"] not in WRITE_ACTIONS]
    data = DATA_TOOLS + [RESULT_TOOL] if owner.data_operations else []
    if not task_mode or not (request.dataset_id or request.workspace_id):
        data = [t for t in data if t["function"]["name"] not in PROPOSAL_TOOLS]
    if not (request.dataset_id or request.workspace_id):
        data = [t for t in data if t["function"]["name"] != "inventory_data_files"]
    result = (STATE_TOOLS + [ARTIFACT_READ_TOOL] + (MEMORY_TOOLS if request.conversation_id else []) + data
            + (DATASET_TOOLS if request.dataset_id and owner.dataset_files else []) + output
            + (SKILL_TOOLS + [EXECUTION_CONFIG_TOOL] if owner.skill_catalog else [])
            + ([PLANNING_TOOL, RUN_TOOL, ARTIFACT_TOOL, ROUTE_TOOL] if task_mode and owner.workflow_service else []))
    result = copy.deepcopy(result)
    for definition in result:
        if definition["function"]["name"] in PROPOSAL_TOOLS | {"prepare_workflow"}:
            task_schema = GoalUpdate.model_json_schema()
            task_schema['properties'] = {k:v for k,v in task_schema['properties'].items()
                if k in {'goal','relation','deliverable','scope_notes','success_criteria','strategy','execution_kind'}}
            task_schema.pop('$defs', None)
            definition["function"]["parameters"]["properties"]["task"] = {
                **task_schema,
                "description": "可选：同时记录目标、范围、完成条件和方法；不授予运行权限。已记录目标可省略。"}
    def lean(value):
        if isinstance(value,dict):
            return {k:lean(v) for k,v in value.items() if k!='title'}
        if isinstance(value,list):
            return [lean(v) for v in value]
        return value
    return lean(result)


def read_result(owner, request, args):
    if set(args) - {"id", "index", "offset", "max_chars"}:
        raise ValueError("无效结果读取参数")
    service = owner.data_operations
    result = service.get(args["id"], request.conversation_id)
    offset, limit = args.get("offset", 0), args.get("max_chars", 8000)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 12000:
        raise ValueError("无效分页参数")
    if "index" in args:
        index = args["index"]
        if type(index) is not int or not 0 <= index < result["file_count"]:
            raise ValueError("产物不存在")
        item = service.repository.items(args["id"], index, 1)[0]
        if item.get("content") is not None:
            content = item["content"]
            return {"id": args["id"], "artifact_type": "text_version", "content": content[offset:offset+limit],
                    "has_more": len(content) > offset+limit, "next_offset": offset+limit if len(content) > offset+limit else None}
        if not item.get("artifact"):
            raise ValueError("该清单条目不是脚本生成的文件")
        path = Path(item["artifact"])
    else:
        path = service.directory / args["id"] / "execution.log"
    service._artifact_path(path)
    from . import workspace_io
    import os
    with os.fdopen(workspace_io.open_read(path), "rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        stream.seek(offset)
        import codecs
        data = stream.read(max(4, limit))
        decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
        content = decoder.decode(data, final=offset+len(data) >= size)
        next_offset = offset+len(data)-len(decoder.getstate()[0])
    return {"id": args["id"], "artifact_type": "script_log" if "index" not in args else "generated_file",
            "content": content, "byte_offset": offset, "total_bytes": size, "has_more": next_offset < size,
            "next_offset": next_offset if next_offset < size else None,
            "capture_limited": result.get("execution_log_capture_limited", False),
            "note": "内容仅为当前页；完整输出需分页读取，截断日志不能证明全量清单完整。"}


async def execute(owner, ledger, request, name, args, observations):
    if name == 'read_method_routes':
        return await operation_thread(read_routes, owner, ledger, request, args)
    if name == 'read_task_artifact':
        from .agent_artifacts import read_artifact
        return await read_artifact(owner, ledger, request, args)
    if name == 'read_execution_configuration':
        from .agent_execution_config import read_execution_configuration
        return read_execution_configuration(owner, args)
    if name in PROPOSAL_TOOLS | {"prepare_workflow"}:
        args = dict(args)
        task = args.pop("task", None)
        if task is not None and not (name == 'prepare_workflow' and args.get('route_id')
                and request.assistant_task.get('method_planning')):
            # A saved route already belongs to the canonical task. Repeated
            # display metadata cannot invalidate it inside the submission call.
            # Real goal edits go through update_task_state before selecting a route.
            ledger.update_goal(request, task)
        from .task_management import assert_task_open
        assert_task_open(request.assistant_task)
        if request.assistant_task.get("deliverable") == "answer":
            raise ValueError("目标产物尚未明确：在本工具 task 参数中记录 goal、deliverable、scope_notes、success_criteria，或调用 update_task_state；分析方法不是最终目标。")
    if name == "update_task_state":
        ledger.update_goal(request, args)
        return {"task_id": request.assistant_task["task_id"], "goal": request.assistant_task["goal"],
                "deliverable": request.assistant_task["deliverable"], "saved": True,
                "state_warnings": request.assistant_task.get("state_warnings", [])}
    if name == "prepare_workflow":
        if (request.assistant_task.get('active_plan') and not request._execution_feedback
                and request.assistant_task.get('goal_review_sequence') != request._user_sequence):
            raise ValueError('先用update_task_state核对本轮补充后的完整任务目标；保留最终产物及必需中间阶段，再更新当前方案')
        canonical = request.assistant_task.get('task_contract')
        parameters = WorkflowDraftArguments.model_validate(
            {**args,'task_contract':canonical} if canonical else args)
        contract = parameters.task_contract
        if canonical:
            current_contract = TaskContract.model_validate(canonical)
            contract = current_contract
        if parameters.route_id:
            if parameters.steps or parameters.input_bindings:
                raise ValueError("使用保存的路线时不同时覆盖步骤或绑定；需要修改时按新条件更新路线")
            route = await operation_thread(saved_route, owner, ledger, request, parameters.route_id, contract)
            contract = TaskContract.model_validate(route['task_contract'])
            parameters.steps = [ComposeStep.model_validate(s) for s in route['steps']]
            parameters.input_bindings = [AssistantInput.model_validate({
                'method_id': next(s['method_id'] for s in route['steps'] if s['id']==v['step_id']),
                'declaration_fact_id': v['fact_id'] if v.get('user_declared') else '',
                **{k:v[k] for k in ('step_id','slot_id','path','per_sample')}}) for v in route['bindings']]
        if contract is None:
            contract = TaskContract(goal=request.assistant_task.get('goal',parameters.goal)[:800],
                                    plan_scope='goal_from_inputs')
        if parameters.route_id and (parameters.targets or parameters.stage_outputs):
            raise ValueError('使用路线ID时不重新选择输出；修改目标后让methods更新同一方案路线。')
        if not parameters.route_id:
            contract = ground_contract(owner.workflow_service.registry, contract,
                                       parameters.targets, parameters.stage_outputs)
            save_grounding(ledger,request,contract)
        catalog = {a.id for a in owner.workflow_service.registry.artifact_types()}
        if not parameters.route_id:
            contract = declared_input_scope(contract, ledger.facts.declarations(request), catalog)
        used = set(contract.goal_artifact_types) | {a.artifact_type for a in [*contract.allowed_existing_inputs, *contract.forbidden_existing_inputs]}
        if not contract.goal_artifact_types or used - catalog:
            raise ValueError("需要真实已登记的目标产物类型；先查询 list_workflow_artifact_types 或已登记方法的输入输出契约")
        capabilities = list(parameters.capability_ids)
        registry = owner.workflow_service.registry
        for identity in parameters.method_ids:
            method=registry.method_for('method',identity)
            if method is None:
                raise ValueError('method_ids 中没有已登记方法：'+identity)
            from .method_planning import MethodContractIndex
            index=MethodContractIndex(registry)
            index.refresh()
            cap=index.capability(identity)
            if cap not in capabilities:
                capabilities.append(cap)
        invalid=[cap for cap in capabilities if not registry.methods_for(cap)]
        if invalid:
            # Identity errors have a deterministic repair; never guess a different method.
            known=[]
            for identity in invalid:
                if registry.method_for('method',identity) is not None:
                    known.append(identity)
            if len(known)!=len(invalid):
                raise ValueError('未知能力ID：'+','.join(invalid)+'；方法ID使用method_ids或steps，Pipeline不是能力ID')
            capabilities=[cap for cap in capabilities if cap not in invalid]
            if not parameters.method_ids:
                parameters.method_ids=known
        if not contract.goal:
            contract.goal = parameters.goal[:800]
        if not parameters.steps:
            raise ValueError("先使用read_method_routes按目标和数据事实生成路线，再传route_id；或提交已核实的完整步骤。不会启动额外模型目录搜索。")
        request_for_plan = request.model_copy(update={"message": parameters.goal, "response_policy": None, "task_contract": contract})
        request_for_plan._registered_plan = MethodPlan(status='matched',steps=parameters.steps) if parameters.steps else None
        request_for_plan._input_selection = 'user'
        request_for_plan._plan_parameter_overrides = (request.assistant_task.get('method_planning',{}).get('query',{}).get('parameters',{})
            if parameters.route_id else {s.method_id:s.parameters for s in parameters.steps})
        request_for_plan._workflow_input_bindings = parameters.input_bindings
        request_for_plan.workspace_context += "\n当前目标和证据（参考数据）：\n" + ledger.context(request)
        request_for_plan.workspace_context += "\n共享事实：\n" + json.dumps(ledger.facts.context(request), ensure_ascii=False)
        if parameters.method_ids:
            contract.retrieval_queries=list(dict.fromkeys([*parameters.method_ids,*contract.retrieval_queries]))[:8]
        routing = {"recommend_workflow": True, "capability_ids": capabilities,
                   "decision_source": "unified_agent", "task_intent": {"task_contract": contract.model_dump()}}
        if request._active_plan_snapshot and request.assistant_task.get('active_plan',{}).get('id') == request._active_plan_snapshot.workflow_id:
            workflow, content = await owner.workflow_service.draft(request_for_plan, routing, existing=request._active_plan_snapshot)
            request._active_plan_snapshot = workflow.model_copy(deep=True)
        else:
            workflow, content = await owner.workflow_service.draft(request_for_plan, routing)
        selected = list(dict.fromkeys(step.selected_method_id for step in workflow.steps if step.selected_method_id))
        if selected:
            ledger.save(request, {**request.assistant_task, 'method_selection': {
                'method_ids': selected, 'task_id': request.assistant_task['task_id'],
                'reason': '主控已准备的流程使用这些真实方法；补查限定在已选方法，替换需说明不兼容原因。'}})
        from .agent_plan_config import attach_workflow_configuration
        from .agent_execution_config import configuration_view
        configuration = await operation_thread(attach_workflow_configuration, owner, ledger, request, workflow)
        if parameters.resolved_need_ids:
            ledger.resolve_needs(request, parameters.resolved_need_ids, strict=False)
        request._prepared_workflow = workflow
        ledger.save(request,{**request.assistant_task,'plan_check':{
            'status':'validated','task_revision':request.assistant_task.get('task_revision',1),
            'workflow_id':workflow.workflow_id,'workflow_revision':workflow.revision,
            'final_outputs':contract.goal_artifact_types,'required_outputs':contract.required_output_types,
            'methods':[s.selected_method_id for s in workflow.steps],'input_validation':'pending_dry_run'}})
        request._workflow_content = content
        missing = [{"step": step.title, "slots": step.missing_required_inputs} for step in workflow.steps if step.missing_required_inputs]
        return {"id": workflow.workflow_id, "kind": "workflow", "status": "pending", "revision": workflow.revision,
                "title": workflow.title, "missing_inputs": missing, "warnings": workflow.warnings,
                "steps": [{"id": v.id, "title": v.title, "method_id": v.selected_method_id} for v in workflow.steps],
                "input_bindings":workflow.planning_diagnostics.get('input_bindings',[]),
                "need_resolution":request.assistant_task.get('need_resolution',{}),
                "sample_scope":workflow.sample_scope.model_dump() if workflow.sample_scope else None,
                "inputs_ready":not missing and (workflow.sample_scope is None or bool(workflow.sample_scope.eligible_sample_ids)),
                "input_validation":"pending_dry_run",
                "input_selection":"user", "draft_ready":not workflow.contract_errors,
                "execution_configuration": configuration_view(configuration),
                "executed": False, "next_step": content}
    if name == "list_workflow_artifact_types":
        if args:
            raise ValueError("不允许额外参数")
        items = [{"id": a.id, "label": a.label, "description": a.description[:500]}
                 for a in owner.workflow_service.registry.artifact_types()]
        return {"items": items, "total_items": len(items), "has_more": False, "scope_complete": True}
    if name == "get_workflow_result":
        identity = args.get("workflow_id")
        known = identity in request.assistant_task.get("artifacts", {}) or identity == request.current_workflow_context.get("workflow_id")
        if set(args) != {"workflow_id"} or not known:
            raise ValueError("流程引用不属于当前任务")
        runs = owner.service.store.list_runs(workflow_id=identity)
        if not runs:
            return {"workflow_id": identity, "status": "not_run", "executed": False}
        run = runs[0]
        return {"id": run.run_id, "kind": "workflow_run", "workflow_id": identity, "status": run.status,
                'workflow_revision':run.workflow_revision,
                "state_revision": run.state_revision, "error": run.error_message,
                "artifacts": [v.model_dump() for v in run.artifacts]}
    if name == "read_data_result":
        return await operation_thread(read_result, owner, request, args)
    if name == 'read_conversation_memory' and args.get('reference', '').startswith('fact:'):
        args = {**args, 'reference': ledger.facts.resolve_reference(request, args['reference'])}
    return await operation_thread(owner.execute, name, args, request)


async def refresh_artifacts(owner, ledger, request, identities):
    from .agent_identity import task_closure
    if task_closure(request.assistant_task):
        # Read-only discussion of the archived goal does not reconfigure it.
        return
    for identity in identities:
        fact = request.assistant_task.get("artifacts", {}).get(identity)
        if not fact:
            raise ValueError("未知任务产物")
        kind = fact.get("kind")
        if kind == "data":
            result = await operation_thread(owner.data_operations.get, identity, request.conversation_id)
            from .agent_state import artifact
            if fact.get("version") != artifact(result).get("version"):
                ledger.record(request, "verify_artifact", {}, result)
                raise ValueError("方案版本已变化；用 read_task_artifact(id="+identity+") 读取当前产物再核验，旧判断不能批准新版本")
        elif kind == "workflow":
            workflow = owner.workflow_service.get(identity)
            canonical = request.assistant_task.get('task_contract')
            if canonical:
                from .method_planning import validate_plan, workflow_plan
                validate_plan(workflow_plan(workflow),
                    declared_input_scope(TaskContract.model_validate(canonical), ledger.facts.declarations(request),
                        {a.id for a in owner.workflow_service.registry.artifact_types()}),
                    owner.workflow_service.registry)
            result = {"id": identity, "kind": "workflow", "status": "pending", "revision": workflow.revision,
                      'input_selection':workflow.input_selection,'draft_ready':not workflow.contract_errors,
                      "title": workflow.title, "missing_inputs": [v.missing_required_inputs for v in workflow.steps if v.missing_required_inputs]}
            from .agent_plan_config import attach_workflow_configuration
            from .agent_execution_config import configuration_view
            result['execution_configuration'] = configuration_view(await operation_thread(attach_workflow_configuration, owner, ledger, request, workflow))
            if fact.get('version') != workflow.revision:
                ledger.record(request, 'verify_artifact', {}, result)
                raise ValueError('流程版本已变化，先读取当前步骤和输入后重新核验')
            if request._prepared_workflow and request._prepared_workflow.workflow_id == identity:
                request._prepared_workflow = workflow
        elif kind == "workflow_run":
            result = await execute(owner, ledger, request, "get_workflow_result", {"workflow_id": fact["workflow_id"]}, [])
            if result.get('workflow_revision') is not None:
                current_workflow = owner.workflow_service.get(fact['workflow_id'])
                if result['workflow_revision'] != current_workflow.revision:
                    raise ValueError('运行结果属于旧流程版本，不能证明当前方案完成；保留旧结果，当前版本仍需手动运行')
        else:
            result = await operation_thread(owner.service.operation, identity)
            if result.get("conversation_id") != request.conversation_id:
                raise PermissionError("文件方案不属于当前会话")
        ledger.record(request, "verify_artifact", {}, result)


def review_ready(owner, ledger, request, identity):
    """The main agent reviews its effects; backend binds that decision to a hash."""
    from .assistant_proposal_review import REVIEW_BLOCK, update_review
    result = owner.data_operations.get(identity, request.conversation_id)
    if result.get('phase') == 'script':
        from .script_contract import validate_script
        validate_script(result['script'], result.get('script_result_kind', 'files'))
        import ast
        source = ast.parse(result['script'])
        functions = {n.name for n in source.body if isinstance(n, ast.FunctionDef) and
                     any(isinstance(v,ast.Return) and v.value is not None for v in ast.walk(n))}
        raw = owner.data_operations.repository.load(identity, 'preview').get('_python_request', {})
        cases = raw.get('test_cases', [])
        if functions and len(cases) < 2:
            raise ValueError('自定义变换函数尚缺普通和边界验证案例；在 run_data_python.test_cases 声明函数、参数及准确期望，准备修订版本。案例仅在用户点击运行后校验。')
    if result.get("quality", {}).get("proposal_review", {}).get("superseded_by"):
        raise ValueError("此版本已被新方案替代，请核对当前版本")
    problems = [v for v in result.get("quality", {}).get("blocking_issues", []) if v != REVIEW_BLOCK]
    if result.get("conflicts") or problems or result.get("preview_expired"):
        raise ValueError("方案仍有真实冲突、质量阻碍或已过期，先修订")
    fact = request.assistant_task["artifacts"][identity]
    current = result.get("script_plan_hash") if fact["artifact_type"] == "script_plan" else result.get("manifest_hash")
    if current != fact["version"]:
        raise ValueError("待运行方案版本已变化")
    reviewed = update_review(owner.data_operations, identity, request.conversation_id,
        {"decision": "ready", "issues": [], "source": "unified_agent", "reviewed_turn_id": request.client_turn_id}, current)
    ledger.record(request, "verify_artifact", {}, reviewed)
    owner.service.event(request.client_turn_id, {"tool": "review_plan", "status": "succeeded", "data_operation": reviewed})


def retire_old_plans(owner, ledger, request, selected, replaced):
    from .assistant_proposal_review import update_review
    replacement = [identity for identity in selected if request.assistant_task["artifacts"][identity].get("kind") == "data"]
    if len(replacement) != 1:
        return
    for identity in replaced - set(selected):
        fact = request.assistant_task.get("artifacts", {}).get(identity, {})
        if fact.get("kind") != "data":
            continue
        current = owner.data_operations.get(identity, request.conversation_id)
        if current["status"] != "pending":
            continue
        version = current.get("script_plan_hash") if fact.get("artifact_type") == "script_plan" else current.get("manifest_hash")
        if version != fact.get("version"):
            continue
        retired = update_review(owner.data_operations, identity, request.conversation_id,
            {"decision": "revise", "issues": ["同一目标的新方案已替代旧待运行方案"],
             "source": "unified_agent", "superseded_by": replacement[0]}, version)
        ledger.record(request, "verify_artifact", {}, retired)
        owner.service.event(request.client_turn_id, {"tool": "review_plan", "status": "succeeded", "data_operation": retired})


def response(owner, request, content):
    workflow = request._prepared_workflow
    return ChatResponse(content=content, type="workflow" if workflow else "done",
        provider_used=getattr(owner.chat, "provider_name", "model"), model_used=getattr(owner.chat, "model", "assistant") or "assistant",
        mode="model", selected_skills=request.selected_skills, interaction_mode=request.interaction_mode,
        workflow=workflow, workflow_recommended=bool(workflow), workflow_decision_source="unified_agent",
        clarification_required=request.assistant_task.get("phase") == "needs_input",
        assistant_task=public_understanding(request.assistant_task))


async def finish_prepared(owner, ledger, request, budget, observations, reason):
    """Deliver validated current proposals even if the model misses its finish call.

    This never reviews an unreviewed script or grants execution permission.
    Readiness comes from actual backend state, not the model's final prose.
    """
    from .workspace_tools import WRITE_ACTIONS
    proposed = {item['result'].get('id') for item in observations
                if item.get('tool') in PROPOSAL_TOOLS | set(WRITE_ACTIONS) | {'prepare_workflow', 'current_artifact'}
                and item['result'].get('status') == 'pending'}

    def ready():
        workflow = request._prepared_workflow
        return [value['id'] for value in request.assistant_task.get('artifacts', {}).values()
                if value.get('id') in proposed and value.get('current_state_verified')
                and value.get('status') == 'pending' and not value.get('preview_expired')
                and (value.get('can_confirm') or value.get('can_run_script')
                     or value.get('artifact_type') == 'file_plan'
                     or (value.get('artifact_type') == 'workflow_plan' and workflow
                         and value['id'] == workflow.workflow_id and (not value.get('missing_inputs')
                             or value.get('input_selection') == 'user' and value.get('draft_ready'))))]

    identities = ready()
    if not identities:
        return None
    try:
        await refresh_artifacts(owner, ledger, request, identities)
        identities = ready()
        if not identities:
            ledger.record(request, 'verify_artifact', {}, {'error': '当前方案状态已变化，尚未通过待运行核验', 'executed': False})
            return None
        summary = '方案已准备，尚未执行。请检查下方任务卡并点击运行。'
        ledger.finish(request, {'status': 'awaiting_run', 'summary': summary, 'artifact_ids': identities})
    except OperationCancelled:
        raise
    except (ValueError, TypeError, KeyError, OSError, RuntimeError) as exc:
        ledger.record(request, 'verify_artifact', {}, {'error': str(exc), 'executed': False})
        return None
    ledger.save(request, {**request.assistant_task, 'query_progress': budget.feedback(),
                         'prepared_plan_finalization': {'reason': reason, 'artifact_ids': identities}})
    ledger.memory.remember_reply(request, summary)
    return response(owner, request, summary)


async def run(owner, request):
    # Scope changes must not silently create a second unfinished goal.
    from .agent_state import AgentTasks
    ledger = AgentTasks(owner.service.store)
    from .data_auth import check_conversation
    check_conversation(owner.service.store,request.conversation_id)
    with owner.service.store.connection() as db:
        from .data_auth import principal
        row = db.execute('SELECT payload FROM assistant_tasks WHERE conversation_id=? AND actor=?',
            (request.conversation_id,principal.get().name)).fetchone()
    old = json.loads(row[0]) if row else {}
    from .task_management import requires_goal_confirmation
    if old.get('scope') != ledger.scope(request) and requires_goal_confirmation(owner.service.store,old):
        request.assistant_task = old
        return response(owner,request,'当前会话的目标尚未归档。请先在任务卡确认完成或主动结束目标，再使用新的数据集或输出目录开启下一个目标。')
    from .agent_team import run_team
    return await run_team(owner, request)
