"""Current-step consistency checks. Intent records do not authorize execution."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .data_selection import DataSelection, provenance_paths
from .model_validation import ModelValidationError


class StepDraft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_excerpt: str = Field(default='', max_length=1200, description='兼容字段；实际本轮原话由后端绑定。历史目标和本轮补充可以共同支持步骤。')
    purpose: str = Field(default='', max_length=1200)
    source_scope: Literal['dataset', 'output', 'unspecified'] = 'unspecified'
    mode: Literal['copy', 'inplace', 'analysis'] | None = Field(default=None, description='文件结果保存方式，与是否分析数据无关。copy=生成新文件或副本到输出目录；inplace=修改来源中的已有文件；analysis=只返回日志/答案，不生成任何文件。')
    actions: list[Literal['copy', 'rename', 'write_text', 'delete', 'mkdir', 'script', 'workflow']] = Field(default_factory=list, max_length=8)
    stage: Literal['new', 'revise_pending', 'modify_results', 'repeat'] = Field(default='new', description='new 新步骤；revise_pending 修订待运行方案；modify_results 处理已生成结果；repeat 明确重复旧动作。')
    base_operation_id: str | None = Field(default=None, description='仅 revise_pending/repeat 使用执行基准；new/modify_results 必须为 null。已完成产物来源仅放 selection.from_operation_id。')
    selection: DataSelection = Field(default_factory=DataSelection)


class AssistantStep(StepDraft):
    request_excerpt: str = Field(min_length=1, max_length=1200)
    purpose: str = Field(min_length=1, max_length=1200)

    @model_validator(mode='after')
    def effects(self):
        # A duplicated provenance reference never becomes an execution baseline.
        if self.stage == 'modify_results' and self.base_operation_id == self.selection.from_operation_id:
            self.base_operation_id = None
        if self.mode == 'analysis' and set(self.actions) - {'script', 'workflow'}:
            raise ModelValidationError(('step', 'mode'), 'analysis_effect', 'analysis 只返回日志或答案，不生成文件；生成新文件用 copy，修改已有文件用 inplace。保留用户要求的文件结果，不要删除写入动作来绕过校验。')
        if self.mode == 'copy' and set(self.actions) & {'rename', 'delete'}:
            raise ModelValidationError(('step', 'mode'), 'copy_effect', 'copy 不改名或删除来源文件，修改已有文件须用 inplace')
        return self


class StepMismatch(ValueError):
    def __init__(self, message):
        super().__init__(message)
        self.feedback = {'error': message, 'reason': 'step_mismatch', 'executed': False,
                         'instruction': '重新核对本轮原话、处理对象、范围和动作；旧批次只能在同一待运行阶段修订。不要通过覆盖确认消除方向错误。'}


def validate_step(step, message, facts):
    if step.request_excerpt not in message:
        raise ModelValidationError(('step', 'request_excerpt'), 'current_request_anchor', 'request_excerpt 必须逐字来自当前消息')
    if step.stage == 'revise_pending':
        base = facts.get(step.base_operation_id, {})
        if not step.base_operation_id or base.get('status') != 'pending' or not any(base.get(k) for k in ('recipe', 'transform', 'manifest_hash', 'script_plan_hash')):
            raise ModelValidationError(('step', 'stage'), 'completed_stage', '只能修订已核实的待运行方案；已完成任务应作为结果来源或明确重做')
        if base.get('source_scope') != step.source_scope or base.get('mode') != step.mode:
            raise ModelValidationError(('step', 'source_scope'), 'baseline_scope', '修订阶段不能改变旧方案的来源或模式')
    elif step.base_operation_id and step.stage != 'repeat':
        raise ModelValidationError(('step', 'base_operation_id'), 'provenance_not_baseline', '新阶段不继承旧执行方案；base_operation_id 设为 null，结果来源放在 selection.from_operation_id')
    if step.stage == 'modify_results' and step.source_scope != 'output':
        raise ModelValidationError(('step', 'source_scope'), 'result_source', '处理已生成结果时来源必须是当前输出目录')


def check_proposal(service, request, *, source_scope, mode, actions=None, selection=None, paths=None, base_id=None, directory=False):
    if request.assistant_task.get("protocol_version") == 2:
        # Actual roots, selection, manifest, version and grants are checked by
        # DataOperations. An assistant's previous strategy is not user authority.
        return
    raw = request.assistant_task.get('step')
    if not raw:
        return  # Legacy callers without a model task remain protected by normal scope/HTTP grants.
    try:
        step = AssistantStep.model_validate(raw)
        validate_step(step, request.message, request.assistant_task.get('operations', {}))
    except (ValidationError, ModelValidationError) as exc:
        mismatch = StepMismatch('准备操作前须补齐并核实本轮步骤：来源、范围、模式、动作和阶段')
        if isinstance(exc, ModelValidationError):
            mismatch.feedback['fields'] = exc.schema_errors
        else:
            mismatch.feedback['fields'] = [{'field': '.'.join(map(str, e['loc'])), 'message': e['msg']}
                                           for e in exc.errors(include_input=False, include_url=False)[:8]]
        raise mismatch from exc
    if step.source_scope == 'unspecified' or (step.mode is None and not directory) or not step.actions:
        raise StepMismatch('本轮步骤尚未确定来源、模式或动作；先核实必要事实再准备方案')
    if source_scope != step.source_scope or (not directory and mode != step.mode):
        mismatch = StepMismatch(f'方案来源/模式为 {source_scope}/{mode}，本轮步骤要求 {step.source_scope}/{step.mode}')
        if source_scope == step.source_scope and mode in {'analysis', 'copy'}:
            mismatch.feedback['next_step'] = '若这是完成原目标所需的分析或清单生成脚本，先调用 prepare_current_step：preparation=true，stage=new，base_operation_id=null，actions=[script]，保持来源和 selection；mode=analysis 只打印，mode=copy 生成报告。然后准备完整脚本卡片，不再询问已确认的用户选择。'
        raise mismatch
    if actions and not set(actions) <= set(step.actions):
        raise StepMismatch('方案动作不符合本轮步骤：' + ', '.join(sorted(set(actions) - set(step.actions))))
    if base_id:
        base = service.get(base_id, request.conversation_id)
        same_step = request.assistant_task.get('step_operation_id') == base_id
        if not same_step and (step.stage not in {'revise_pending', 'repeat'} or step.base_operation_id != base_id):
            raise StepMismatch('本轮没有要求修订或重复该旧方案；已完成批次仅作为来源记录')
        if (same_step or step.stage == 'revise_pending') and base['status'] != 'pending':
            raise StepMismatch('基准方案已经离开待运行状态，不能继续修订')
        if base['source_scope'] != source_scope or base['mode'] != mode:
            raise StepMismatch('旧方案与本轮来源或模式不一致')
    if selection is not None and selection != step.selection:
        raise StepMismatch('方案选择范围与本轮步骤不一致；请保留明确的子集和排除项')
    if paths is not None:
        scope = {'conversation_id': request.conversation_id, 'workspace_id': request.workspace_id,
                 'dataset_id': request.dataset_id, 'source_scope': source_scope, 'mode': mode}
        provenance = provenance_paths(service, scope, step.selection)
        if any(not step.selection.matches(p) or (provenance is not None and p not in provenance) for p in paths):
            raise StepMismatch('方案含本轮选择范围以外的文件')


def compatible_baseline(service, request, source_scope, mode, selection):
    if request.assistant_task.get("protocol_version") == 2:
        return None  # Revisions choose an explicit pending baseline.
    step = request.assistant_task.get('step', {})
    identity = request.assistant_task.get('step_operation_id') or (step.get('base_operation_id') if step else request.assistant_task.get('active_operation_id'))
    if step and step.get('stage') != 'revise_pending' and not request.assistant_task.get('step_operation_id'):
        return None
    if not identity:
        return None
    base = service.get(identity, request.conversation_id)
    if base['status'] != 'pending' or not base.get('recipe'):
        return None
    previous = DataSelection.model_validate(base['recipe'].get('selection') or {'path': base['recipe'].get('path', '.')})
    return identity if base['source_scope'] == source_scope and base['mode'] == mode and previous == selection else None


def result_problem(request, result):
    raw = request.assistant_task.get('step')
    if not raw or result.get('kind') != 'data':
        return None
    try:
        step = AssistantStep.model_validate(raw)
    except ValidationError:
        return '操作步骤仍需补齐并核实'
    if step.source_scope != 'unspecified' and result.get('source_scope') != step.source_scope:
        return '方案来源与当前步骤不一致'
    if step.mode and result.get('mode') != step.mode:
        return '方案模式与当前步骤不一致'
    selection = (result.get('transform') or result.get('recipe') or result).get('selection')
    if selection is not None and DataSelection.model_validate(selection) != step.selection:
        return '方案选择范围与当前步骤不一致'
    allowed = set(step.actions)
    if result.get('script_hash') and 'script' in allowed and result.get('script_result_kind') != 'changes':
        allowed.add('write_text')
    if result.get('phase') != 'script' and allowed and not set(result.get('action_counts', {})) <= allowed:
        return '方案包含当前步骤未要求的动作'
    return None
