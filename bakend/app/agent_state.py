"""One durable goal, typed artifacts and sourced decisions. Never execution grants."""
from __future__ import annotations

import json
import time
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .assistant_tasks import AssistantTasks, compact_operation
from .agent_context import prepared_plan_state
from .data_auth import principal
from .agent_facts import SharedFacts
from .agent_identity import task_closure, closed_task_phase
from .agent_delivery import latest_jobs
from .task_contract import TaskContract, input_declaration_signature
from .goal_grounding import intent_schema


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quote: str = Field(min_length=1, max_length=1200)
    reference: str = Field(description="当前会话用户消息的 message:<序号>；current 表示本轮原话。")


class InformationNeed(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80)
    question: str = Field(min_length=1, max_length=400, description='完成目标仍需核实的具体事实，不添加用户未要求的任务。')
    required: bool = True
    phase: Literal['planning', 'preflight', 'configuration', 'execution'] = Field(default='planning',
        description='输入存在性、覆盖和内容兼容性属于preflight，不阻止结构规划；软件路径属于configuration，不作为方法选路前提。')
    owner: Literal['data', 'methods', 'runtime', 'coordinator'] = 'coordinator'
    status: Literal['open', 'resolved'] = 'open'
    conclusion: str = Field(default='', max_length=800, description='依据证据得到的结论，或仍影响方案的具体缺口；不只保存问题和引用。')
    evidence_refs: list[str] = Field(default_factory=list, max_length=8, description='引用当前范围真实 fact:<ID>、evidence:<ID> 或 message:<序号>。后端解析引用，不凭推测关闭缺口。')


class DeclaredInput(Decision):
    artifact_type: str = Field(min_length=1, max_length=100)
    slot_id: str = Field(default="", max_length=100, description="同类型不同角色分别声明，例如wm_response/gm_response/csf_response。")
    path: str = Field(default="", max_length=4096, description="仅用户原话包含的相对路径；未给文件名留空，dry-run按契约定位。")
    per_sample: bool = True
    available: bool = Field(default=True, description="用户纠正为不存在时设false，取代旧声明。")
    features: dict = Field(default_factory=dict, description="用户明确声明的输入属性，未核验；不能从文件名推断。")


class GoalUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal: str = Field(min_length=1, max_length=1200)
    relation: Literal["continue", "new"] = Field(default="continue",
        description='补充或修改使用continue；用户已结束或确认旧目标（user_closure/user_completion）后提出新目标才new。初次建立目标可new。')
    deliverable: Literal["answer", "file_changes", "files", "report", "workflow"] = "answer"
    execution_kind: Literal['unspecified', 'structured_files', 'python_script', 'scientific_workflow'] = 'unspecified'
    decisions: list[Decision] = Field(default_factory=list, max_length=20)
    assumptions: list[str] = Field(default_factory=list, max_length=8)
    open_questions: list[str] = Field(default_factory=list, max_length=8)
    scope_notes: str = Field(default='', max_length=1200, description='理解的输入与处理范围；未知范围说明缺口，不猜测为全部。')
    success_criteria: list[str] = Field(default_factory=list, max_length=8, description='怎样证明满足用户目标；这是模型理解，不能添加用户未要求的任务。')
    strategy: str = Field(default='', max_length=1200, description='当前方法与下一步，可随证据修改，不改变目标或权限。')
    task_contract: TaskContract | None = Field(default=None,
        description='科学流程的自然语言目标、必需中间阶段与输入范围。无需填写内部产物类型；方法agent选择真实输出后由后端确定。')
    information_needs: list[InformationNeed] | None = Field(default=None, max_length=12)
    declared_inputs: list[DeclaredInput] = Field(default_factory=list, max_length=40,
        description="用户明确说已有的输入；逐字quote引用消息。规划直接复用，存在性及内容兼容性留给dry-run；不是读取或执行授权。")


class FinishTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["answered", "needs_input", "awaiting_inputs", "awaiting_run", "completed", "blocked"]
    summary: str = Field(min_length=1, max_length=4000)
    artifact_ids: list[str] = Field(default_factory=list, max_length=16)
    resolved_need_ids: list[str] = Field(default_factory=list, max_length=12, description='本轮已依据关联证据解决的缺口 ID，可在交付时一并关闭，避免额外往返；没有真实引用不能关闭。')


def tool(name, description, model):
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": model.model_json_schema()}}


STATE_TOOLS = [
    tool("update_task_state", "保存或纠正持续目标与预期产物；decisions 必须引用用户原话。方法可随证据改变，不锁定工具、动作或 copy/inplace。操作需求先记录目标；解释追问不要覆盖未完成目标。", GoalUpdate),
    tool("finish_task", "结束本轮：回答、关键澄清、待点击运行、核验完成或具体阻碍。prepared_plan_state=needs_agent_review 时，核对当前版本与用户目标一致后用 awaiting_run 引用方案 ID，后端完成待运行核验；此标记不要求重复扫描或运行脚本。completed 必须引用已完成且符合目标类型的后端产物，报告不能证明文件已修改。", FinishTask),
]
intent_schema(STATE_TOOLS[0]['function']['parameters'])


def artifact(result):
    kind, status = result.get("kind"), result.get("status")
    if kind == "data":
        if result.get("phase") == "script" and not result.get("manifest_ready", True):
            typed = "script_plan"
        elif status == "analyzed":
            typed = "report"
        elif status == "succeeded":
            typed = "files" if result.get("script_result_kind") == "files" else "file_changes"
        else:
            typed = "file_manifest"
        value = compact_operation(result)
        value.update(manifest_ready=result.get("manifest_ready", True), preview_expired=result.get("preview_expired", False))
        if result.get('verification_report'):
            value['verification_report']=result['verification_report']
        version = result.get("script_plan_hash") if typed == "script_plan" else result.get("manifest_hash")
    elif kind == "workflow":
        typed, version = "workflow_plan", result.get("revision")
        value = {k: result[k] for k in ("id", "kind", "status", "revision", "title", "missing_inputs", "warnings", "inputs_ready", "input_validation", "input_selection", "draft_ready", "preflight_summary", "input_bindings", "sample_scope", "execution_configuration") if k in result}
    elif kind == "workflow_run":
        typed, version = "workflow_result", result.get("state_revision")
        value = {k: result[k] for k in ("id", "kind", "status", "workflow_id", "workflow_revision", "state_revision", "error", "artifacts") if k in result}
    else:
        typed, version = "file_changes" if status == "succeeded" else "file_plan", result.get("expires_at")
        value = {k: result[k] for k in ("id", "kind", "status", "action", "source", "target", "error", "expires_at") if k in result}
    plan_state = prepared_plan_state(result)
    return {**({"prepared_plan_state": plan_state} if plan_state else {}), **value, "artifact_type": typed, "version": version, "observed_at": time.time(),
            "current_state_verified": True}


class AgentTasks(AssistantTasks):
    def __init__(self, store):
        super().__init__(store)
        from .managed_tasks import ensure_schema
        ensure_schema(store)
        self.facts = SharedFacts(self.memory)

    def save(self, request, value):
        from .data_auth import check_conversation
        check_conversation(self.store, request.conversation_id)
        expected = request.assistant_task
        value = {**value,'scope':self.scope(request),'updated_at':time.time()}
        with self.store.transaction() as db:
            row = db.execute('SELECT payload FROM assistant_tasks WHERE conversation_id=? AND actor=?',
                (request.conversation_id,principal.get().name)).fetchone()
            current = json.loads(row[0]) if row else {}
            if (current.get('task_id') == expected.get('task_id') and task_closure(current)
                    and task_closure(current) != task_closure(expected)):
                raise ValueError('目标已由用户归档，旧请求不能覆盖归档状态')
            if (expected.get('task_id') and current.get('task_id') == expected['task_id']
                    and current.get('scope') != self.scope(request)):
                raise ValueError('任务目录范围已变化，旧请求不能覆盖当前任务')
            if (expected.get('task_id') and current.get('scope') == self.scope(request) and
                    (current.get('task_id') != expected['task_id'] or
                     current.get('task_revision',1) != expected.get('task_revision',1))):
                raise ValueError('任务目标版本已变化，旧请求结果不能覆盖当前任务；请按最新目标更新方案')
            from .managed_tasks import sync_state
            sync_state(db,value,request.conversation_id,principal.get().name)
            db.execute('INSERT OR REPLACE INTO assistant_tasks VALUES(?,?,?)',
                (request.conversation_id,principal.get().name,json.dumps(value,ensure_ascii=False)))
        request.assistant_task = value
        return value

    def begin(self, request):
        old = self.load(request)
        request.assistant_task = old
        if old.get("protocol_version") != 2:
            # Old model-generated steps are context, never new constraints.
            old = {"legacy_context": {k: old[k] for k in ("goal", "constraints", "operations") if k in old}}
        value = {**old, "protocol_version": 2, "task_id": old.get("task_id") or "task_" + uuid4().hex,
                 "task_revision":old.get('task_revision',1),
                 "goal": old.get("goal") or request.message, "deliverable": old.get("deliverable", "answer"),
                 "current_request": request.message, "source_sequence": request._user_sequence,
                 "previous_phase": old.get("phase"),
                 "phase": "verifying" if request._execution_feedback else "preparing",
                 "actor": principal.get().name, "last_request": request.model_dump(mode="json")}
        value["prepared_this_turn"] = []
        if task_closure(old):
            value['phase'] = closed_task_phase(old)
        value.pop('task_deleted',None)
        if not value.get('active_plan') and value.get('awaiting_results'):
            identity = value['awaiting_results'][0]
            prior = value.get('artifacts',{}).get(identity,{})
            value['active_plan'] = {'id':identity,'kind':prior.get('kind'),
                'revision':prior.get('revision'),'task_revision':value.get('task_revision',1)}
        for retired in ('working_memory', 'query_checkpoint', 'legacy_context'):
            value.pop(retired, None)
        self.save(request, value)
        if not request._execution_feedback:
            self.memory.capture(request)
        else:
            request._conversation_memory = self.memory.load(request)
        return value

    def update_goal(self, request, arguments):
        from .task_management import TaskActionError, assert_task_open, requires_goal_confirmation
        update = GoalUpdate.model_validate(arguments)
        if request.assistant_task.get('goal_source') == 'user' and request.task_action == 'update_plan':
            for field in ('goal','scope_notes','success_criteria'):
                setattr(update,field,request.assistant_task.get(field,[] if field == 'success_criteria' else ''))
        if update.task_contract and (update.deliverable == 'workflow' or request.assistant_task.get('deliverable') == 'workflow'):
            update.task_contract.plan_scope = 'goal_from_inputs'
        decisions, warnings = [], []
        for item in update.decisions:
            reference = f"message:{request._user_sequence}" if item.reference == "current" else item.reference
            if not reference.startswith("message:") or not reference[8:].isdigit():
                warnings.append({'field': 'decisions', 'reference': reference,
                                 'reason': '用户决定需要准确的消息引用；该条未作为用户要求保存'})
                continue
            with self.store.connection() as db:
                row = db.execute("SELECT role,content FROM conversation_messages WHERE conversation_id=? AND sequence=?",
                                 (request.conversation_id, int(reference[8:]))).fetchone()
            if not row or row["role"] != "user" or item.quote not in row["content"]:
                warnings.append({'field': 'decisions', 'reference': reference,
                                 'reason': '决定必须逐字引用用户原话；该条未作为用户要求保存，可将推测放入 assumptions'})
                continue
            decisions.append({"quote": item.quote, "reference": reference})
        old = request.assistant_task
        if update.relation == "new":
            if requires_goal_confirmation(self.store,old):
                raise TaskActionError('task_completion_confirmation_required', 'user',
                    '当前目标尚未由用户归档；请在任务卡点击“确认完成并结束此目标”或“结束目标并归档”，再开启新目标。补充或修改仍更新同一张卡。')
            if old.get('active_plan') and not any(d['reference'] == f'message:{request._user_sequence}' for d in decisions):
                raise ValueError('当前会话正在维护一个任务；新建任务需引用用户本轮明确的新任务要求。解释或补充不能丢弃原任务。')
            old = {k: old[k] for k in ("protocol_version", "architecture", "phase", "scope", "actor", "last_request", "current_request", "source_sequence") if k in old}
            old["task_id"] = "task_" + uuid4().hex
        else:
            assert_task_open(old)
        if update.information_needs is not None:
            if len({n.id for n in update.information_needs}) != len(update.information_needs):
                raise ValueError('信息缺口 ID 必须唯一')
            for need in update.information_needs:
                valid = []
                for ref in need.evidence_refs:
                    try:
                        valid.append(self.facts.resolve_reference(request, ref))
                    except (ValueError, KeyError) as exc:
                        warnings.append({'field': 'information_needs', 'id': need.id,
                                         'reference': ref, 'reason': str(exc)})
                need.evidence_refs = list(dict.fromkeys(valid))
                if need.status == 'resolved' and (not valid or not need.conclusion.strip()):
                    need.status = 'open'
                    warnings.append({'field': 'information_needs', 'id': need.id,
                                     'reason': '该缺口保持 open：关闭需真实引用与明确结论；不阻碍保存其他有效目标字段'})
        prior = old.get("decisions", []) if update.relation == "continue" else []
        # A follow-up metadata update must not reset the original deliverable,
        # criteria or assumptions to schema defaults omitted by the model.
        previous_inputs = (input_declaration_signature(self.facts.declarations(request))
            if update.declared_inputs and (update.deliverable == 'workflow' or old.get('deliverable') == 'workflow') else None)
        for item in update.declared_inputs:
            reference = f"message:{request._user_sequence}" if item.reference == "current" else item.reference
            try:
                self.facts.declare(request, {**item.model_dump(), "reference": reference})
            except ValueError as exc:
                warnings.append({"field": "declared_inputs", "reason": str(exc)})
        payload = update.model_dump(exclude={"relation", "decisions", "declared_inputs"}, exclude_unset=update.relation == 'continue')
        if update.task_contract is None:
            payload.pop('task_contract', None)
        workflow_goal = update.deliverable == 'workflow' or old.get('deliverable') == 'workflow'
        if workflow_goal:
            contract = dict(old.get('task_contract', {}))
            if update.task_contract is not None:
                contract.update(update.task_contract.model_dump(exclude_unset=True))
            semantic_change = update.goal != old.get('goal') or (
                update.task_contract is not None and 'required_stages' in update.task_contract.model_fields_set
                and contract.get('required_stages',[]) != old.get('task_contract',{}).get('required_stages',[]))
            explicit_legacy = update.task_contract is not None and 'goal_artifact_types' in update.task_contract.model_fields_set
            if semantic_change and not explicit_legacy:
                # Reuse catalog/data facts, but reselect outputs for changed intent.
                for field in ('goal_artifact_types','goal_output_refs','required_output_refs',
                              'required_output_types','required_method_ids'):
                    contract.pop(field,None)
            contract['goal'] = update.goal[:800]
            contract['plan_scope'] = 'goal_from_inputs'
            payload['task_contract'] = TaskContract.model_validate(contract).model_dump()
        changed = any(k in payload and payload[k] != old.get(k) for k in ('goal','scope_notes','success_criteria','task_contract'))
        if previous_inputs is not None:
            changed |= previous_inputs != input_declaration_signature(self.facts.declarations(request))
        payload['goal_review_sequence'] = request._user_sequence
        if changed:
            payload['task_revision'] = old.get('task_revision',1) + 1
        if update.relation == 'new':
            payload['task_revision'] = 1
        if changed and not workflow_goal and not update.task_contract and old.get('task_contract') and update.relation == 'continue':
            payload['task_contract'] = old['task_contract']
        if update.deliverable != 'answer' or old.get('deliverable') not in (None,'answer'):
            original = old.get('user_requirements') or request.message
            quotes = [] if request.task_action == 'update_plan' else [d['quote'] for d in decisions if d['quote'] not in original]
            payload['user_requirements'] = '\n'.join(dict.fromkeys([original,*quotes]))[:8000]
        if update.information_needs is None:
            payload.pop('information_needs', None)
        else:
            payload['information_needs'] = [need.model_dump() for need in update.information_needs]
        return self.save(request, {**old, **payload,
                                  'state_warnings': warnings,
                                  "decisions": [*prior, *[d for d in decisions if d not in prior]][-40:]})

    def record(self, request, name, arguments, result, *, preview_budget=6000):
        if "error" in result:
            value = {**request.assistant_task, "last_error": {"tool": name, **result}}
            if name.startswith('propose_') or name in {'prepare_workflow', 'run_data_python'}:
                value['preparation_error'] = {'tool': name, **result}
            self.save(request, value)
            return None
        reference = self.memory.remember_evidence(request, name, arguments, result)
        value = dict(request.assistant_task)
        value.pop("last_error", None)
        if result.get('kind') in {'data', 'workflow'} and name.startswith(('propose_', 'prepare_', 'run_')):
            value.pop('preparation_error', None)
        previous = value.get("artifacts", {}).get(result.get("id"), {})
        # Paged manifests share a task ID but have a collection kind (changes,
        # unmatched, ...). They are evidence, never a replacement task header.
        partial = previous.get("kind") in {"data", "workflow", "workflow_run"} and result.get("kind") != previous["kind"]
        if not partial and result.get("id") and (result.get("kind") in {"data", "workflow", "workflow_run"} or "status" in result):
            facts = dict(value.get("artifacts", {}))
            facts[result["id"]] = {**artifact(result), "observed_turn_id": request.client_turn_id}
            value["artifacts"] = dict(list(facts.items())[-32:])
            if name in {'prepare_workflow','run_data_python'} or name.startswith('propose_'):
                value['active_plan'] = {'id':result['id'],'kind':result.get('kind'),
                    'revision':result.get('revision',result.get('script_plan_hash',result.get('manifest_hash'))),
                    'task_revision':value.get('task_revision',1)}
            scope = result.get('sample_scope')
            incomplete_workflow = result.get('kind') == 'workflow' and (result.get('missing_inputs') or
                result.get('inputs_ready') is False or scope and not scope.get('eligible_sample_ids'))
            if incomplete_workflow and not (result.get('input_selection') == 'user' and result.get('draft_ready')):
                value['prepared_this_turn'] = [i for i in value.get('prepared_this_turn', []) if i != result['id']]
            elif name.startswith(("propose_", "create_", "copy_", "move_", "rename_", "delete_")) or name in {"run_data_python", "prepare_workflow"}:
                value["prepared_this_turn"] = list(dict.fromkeys([*value.get("prepared_this_turn", []), result["id"]]))
        self.save(request, value)
        return reference

    def context(self, request):
        value = request.assistant_task
        context = {k: value[k] for k in ("task_id", "goal", "deliverable", "decisions", "assumptions", "open_questions",
                   "phase", "last_error", "legacy_context", "source_sequence", "scope_notes", "success_criteria", "strategy",
                   "information_needs", "query_progress", "preparation_error", "state_warnings", "turn_outcome", "execution_kind", "execution_configuration") if k in value}
        context.update({k:value[k] for k in ('task_revision','user_requirements','active_plan','task_contract','goal_source','user_completion','user_closure') if k in value})
        context['method_selection'] = value.get('method_selection', {})
        if context.get('execution_configuration'):
            from .agent_execution_config import configuration_view
            context['execution_configuration'] = configuration_view(context['execution_configuration'])
        planning = value.get('method_planning',{})
        context['method_routes'] = (planning.get('state',{}).get('summary',{}) if planning.get('task_revision',1) == value.get('task_revision',1)
            else {'status':'goal_changed','next_step':'按当前完整目标更新路线；原始方法事实继续复用'})
        context['candidate_review'] = value.get('candidate_review', {})
        current_jobs = latest_jobs(value)
        from .agent_delivery import configuration_supersedes
        context['agent_jobs'] = [{k: job[k] for k in ('id', 'agent', 'question', 'purpose', 'status', 'report', 'runtime_scope') if k in job}
                                 for job in current_jobs.values() if not configuration_supersedes(value, job)]
        context['earlier_agent_job_count'] = len(value.get('agent_jobs', {})) - len(current_jobs)
        for job in context['agent_jobs']:
            if 'report' in job:
                report = job['report']
                job['report'] = {k: report[k] for k in ('agent', 'status', 'assignment_complete', 'unresolved',
                    'fact_ids', 'report_warnings', 'selected_methods', 'selection_reason', 'planning_complete', 'route_ids', 'next_step',
                    'action_owner', 'next_action', 'error_code') if k in report}
                job['report']['findings'] = [{k: f[k] for k in ('requirement_id', 'status', 'conclusion',
                    'fact_ids', 'basis', 'observation_verified') if k in f} for f in report.get('findings', [])]
        context['understanding_notice'] = '目标、范围、完成条件、策略和信息缺口结论是模型理解；引用存在不证明解释正确。以功能agent的observation_verified及checks区分观测和推断，未知不反复重查相同证据。用户原话及已引用决定优先；历史状态需核验。'
        context["artifacts"] = {identity: fact for identity, fact in value.get("artifacts", {}).items()}
        # Keep full scopes in the artifact/store, but do not repeat thousands of
        # sample IDs and duplicate missing-input reasons in every model request.
        for identity,fact in list(context['artifacts'].items()):
            scope=fact.get('sample_scope')
            if scope:
                context['artifacts'][identity]={**fact,'sample_scope':{
                    **scope,'eligible_count':len(scope['eligible_sample_ids']),
                    'skipped_count':len(scope['skipped_samples']),
                    'eligible_sample_ids':scope['eligible_sample_ids'][:8],
                    'skipped_samples':scope['skipped_samples'][:8],
                    'detail_available':True,
                    'sample_only':len(scope['eligible_sample_ids'])>8 or len(scope['skipped_samples'])>8}}
        context["artifact_count"] = len(value.get("artifacts", {}))
        context["evidence"] = request._conversation_memory.get("evidence", [])[-8:]
        return json.dumps(context, ensure_ascii=False)

    def associate_need(self, request, need_id, reference):
        """Remember which evidence was already returned, without asserting it proves the answer."""
        value = dict(request.assistant_task)
        needs = [dict(n) for n in value.get('information_needs', [])]
        for need in needs:
            if need['id'] == need_id:
                need['evidence_refs'] = list(dict.fromkeys([*need.get('evidence_refs', []), reference]))[-8:]
        self.save(request, {**value, 'information_needs':needs})

    def finish(self, request, arguments):
        finish = FinishTask.model_validate(arguments)
        if task_closure(request.assistant_task) and finish.status != 'answered':
            from .task_management import assert_task_open
            assert_task_open(request.assistant_task)
        facts = request.assistant_task.get("artifacts", {})
        selected = [facts[identity] for identity in finish.artifact_ids if identity in facts]
        user_drafts = [v for v in selected if v.get('artifact_type') == 'workflow_plan'
                       and v.get('input_selection') == 'user' and v.get('draft_ready')]
        active = request.assistant_task.get('active_plan',{})
        if finish.status in {'awaiting_inputs','awaiting_run'} and active:
            if active['id'] not in finish.artifact_ids or active.get('task_revision',1) != request.assistant_task.get('task_revision',1):
                raise ValueError('当前目标尚无对应的新版本方案，不能交付旧草案；更新当前方案后再交付')
            if len([v for v in selected if v.get('artifact_type')=='workflow_plan']) > 1:
                raise ValueError('请把当前任务的步骤组织到同一个流程并建立真实连接，不能将独立草案描述为已连接')
        if finish.status == 'awaiting_run' and any(v.get('missing_inputs') for v in user_drafts):
            finish.status = 'awaiting_inputs'
        if finish.status == 'awaiting_inputs':
            if len(user_drafts) != len(selected) or not selected:
                raise ValueError('待选择输入需要引用已保存且结构有效的用户输入流程草案')
        if any(v.get('artifact_type')=='workflow_plan' and (v.get('missing_inputs') or v.get('inputs_ready') is False) for v in selected) and finish.status=='awaiting_run':
            raise ValueError('流程仍有必需输入未绑定；使用已查询路径补齐input_bindings，不能把需要用户手工配置的草稿称为待运行方案')
        if finish.status=='awaiting_run' and any(v.get('sample_scope') and not v['sample_scope']['eligible_sample_ids'] for v in selected):
            raise ValueError('没有共同满足必需输入的样本，不能称为待运行方案')
        if len(selected) != len(finish.artifact_ids):
            raise ValueError("产物引用不属于当前任务")
        if finish.status == "completed":
            if active and active.get('task_revision',1) != request.assistant_task.get('task_revision',1):
                raise ValueError('目标已修改，旧版本运行结果不能证明新目标完成；保留运行记录并更新方案')
            expected = {"file_changes": {"file_changes"}, "files": {"files", "file_changes"},
                        "report": {"report", "files"}, "workflow": {"workflow_result"}, "answer": set()}
            allowed = expected[request.assistant_task.get("deliverable", "answer")]
            if not selected or not all(v.get("current_state_verified") for v in selected) or not any(
                    v["artifact_type"] in allowed and v["status"] in {"succeeded", "analyzed"} for v in selected):
                raise ValueError("缺少符合原目标且已核验的完成产物；分析报告、脚本或流程草稿不代表修改或计算完成")
        if finish.status == "awaiting_run":
            if not selected or not all(v["status"] == "pending" and
                    (v.get("can_confirm") or v.get("can_run_script") or v["artifact_type"] in {"workflow_plan", "file_plan"})
                    and not v.get("preview_expired") for v in selected):
                raise ValueError("尚无完整可审阅的待运行方案；依据具体错误继续修订或补充必要信息")
        # An actual validated draft controls readiness. Auxiliary memory IDs
        # cannot prevent delivery; unsupported claims remain unresolved.
        needs = self.resolve_needs(request, finish.resolved_need_ids, strict=False)
        phase = {"answered": "answered", "needs_input": "needs_input", "awaiting_inputs":"awaiting_inputs", "awaiting_run": "awaiting_run",
                 "completed": "completed", "blocked": "blocked"}[finish.status]
        waiting = request.assistant_task.get("awaiting_results", []) if phase == "answered" else []
        if phase == "answered" and waiting:
            phase = request.assistant_task.get("previous_phase") or "awaiting_run"
        elif phase == 'answered' and active:
            phase = request.assistant_task.get('previous_phase') or 'answered'
        if task_closure(request.assistant_task):
            phase = closed_task_phase(request.assistant_task)
        from .agent_artifacts import stage_summary
        summary, hint = stage_summary(finish.summary, selected if phase in {'awaiting_inputs','awaiting_run'} else [])
        self.save(request, {**request.assistant_task, "phase": phase, "next_step": hint or summary,
                           'information_needs':needs,
                           "awaiting_results": finish.artifact_ids if phase in {'awaiting_inputs','awaiting_run'} and finish.status != "answered" else waiting,
                           "result_summary": summary})
        return summary

    def resolve_needs(self, request, identities, *, strict=True):
        needs = [dict(n) for n in request.assistant_task.get('information_needs', [])]
        resolved, rejected = [], []
        for identity in identities:
            need = next((n for n in needs if n['id'] == identity), None)
            try:
                if not need or not need.get('evidence_refs') or not need.get('conclusion', '').strip():
                    raise ValueError('关闭缺口需要当前目标中的真实关联证据和明确结论')
                for ref in need['evidence_refs']:
                    self.facts.resolve_reference(request, ref)
            except (ValueError,KeyError,PermissionError) as exc:
                if strict:
                    raise
                rejected.append({'id':identity,'reason':str(exc)[:300]})
                continue
            need['status'] = 'resolved'
            resolved.append(identity)
        if identities:
            self.save(request, {**request.assistant_task,'information_needs':needs,
                'need_resolution':{'resolved_ids':resolved,'unresolved':rejected}})
        return needs
