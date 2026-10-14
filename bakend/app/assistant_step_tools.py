"""Compile a complete step only when preparing an operation, after exploration."""
from .assistant_contract import AssistantStep, StepMismatch, validate_step
from .data_selection import DataSelection


definition = AssistantStep.model_json_schema()
definition['properties'].pop('request_excerpt')
definition['properties']['preparation'] = {'type': 'boolean', 'default': False, 'description': '原目标的前置分析或映射/报告生成步骤，允许在相同来源和选择范围内准备 copy/analysis 脚本；不改原文件，不继承旧执行基准。'}
definition['required'] = [k for k in definition.get('required', []) if k != 'request_excerpt']
PREPARE_STEP_TOOL = {'type': 'function', 'function': {
    'name': 'prepare_current_step',
    'description': '查询或澄清后补齐操作步骤，再调用数据提议工具。只完善本轮草稿，不修改文件、不启动任务，不扩大已明确的范围或动作。历史目标和本轮补充共同确定步骤，本轮原话由后端绑定。',
    'parameters': definition}}


def prepare_step(ledger, request, arguments):
    if request.interaction_mode != 'task' or not request.response_policy or request.response_policy.action != 'operate':
        raise PermissionError('本轮只允许回答或查询，不能准备操作步骤')
    if 'request_excerpt' in arguments:
        raise ValueError('用户消息依据由后端绑定')
    arguments = dict(arguments)
    preparation = arguments.pop('preparation', False)
    if not isinstance(preparation, bool):
        raise ValueError('preparation 必须为布尔值')
    step = AssistantStep.model_validate({**arguments, 'request_excerpt': request.message[:1200]})
    validate_step(step, request.message, request.assistant_task.get('operations', {}))
    if step.source_scope == 'unspecified' or not step.actions or step.mode is None:
        raise StepMismatch('准备操作前须明确来源、模式和动作')
    old = request.assistant_task.get('step', {})
    if request.assistant_task.get('step_operation_id'):
        raise StepMismatch('已有操作方案，不能替换其步骤；应核实并修订待运行方案')
    if preparation:
        if step.mode not in {'analysis', 'copy'} or set(step.actions) != {'script'} or step.stage != 'new' or step.base_operation_id:
            raise StepMismatch('前置步骤只能准备不改来源文件的分析或报告脚本，stage=new，base_operation_id=null')
        if old.get('source_scope') not in {'dataset', 'output'} or old['source_scope'] != step.source_scope:
            raise StepMismatch('前置脚本须保持已核实的处理来源')
        if DataSelection.model_validate(old.get('selection', {})) != step.selection:
            raise StepMismatch('前置脚本须保持当前选择范围和排除项')
        target = request.assistant_task.get('target_step') or old
        ledger.save(request, {**request.assistant_task, 'target_step': target, 'step_role': 'preparation',
                             'step': step.model_dump(), 'active_operation_id': None})
        return {'step': step.model_dump(), 'executed': False, 'ready_for_proposal': True,
                'instruction': '现在准备实现本步骤完整结果的脚本卡片；不要只打印少量样例。原目标尚待后续完成，脚本及保存输出都须手动运行。'}
    for key in ('source_scope', 'mode'):
        if old.get(key) not in (None, 'unspecified') and old[key] != getattr(step, key):
            raise StepMismatch('不能通过补齐步骤改变本轮已明确的' + key)
    if old.get('actions') and not set(step.actions) <= set(old['actions']):
        raise StepMismatch('不能通过补齐步骤扩大本轮动作')
    if old.get('selection') and old['selection'] != DataSelection().model_dump() and old['selection'] != step.selection.model_dump():
        raise StepMismatch('不能通过补齐步骤改变本轮已明确的文件范围')
    if old.get('stage', 'new') != step.stage or old.get('base_operation_id') != step.base_operation_id:
        raise StepMismatch('不能通过补齐步骤继承或重复旧操作')
    ledger.save(request, {**request.assistant_task, 'step': step.model_dump()})
    return {'step': step.model_dump(), 'executed': False, 'ready_for_proposal': True}
