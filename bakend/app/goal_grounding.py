"""Ground human intent in selected live catalog ports; no files or model calls."""
from .task_contract import OutputReference, StageOutputReference, TaskContract
from .task_management import TaskActionError


def output_port(registry, reference):
    reference = OutputReference.model_validate(reference)
    method = registry.method_for('method', reference.method_id)
    if method is None:
        raise TaskActionError('goal_method_unknown', 'methods',
            '目标引用不是已登记的独立方法：'+reference.method_id+
            '；从候选 output_refs 选择方法与输出端口。组合流程使用 read_skill_pipeline，不把它的ID作为方法ID。')
    port = next((p for p in method.output_slots if p.id == reference.output_slot_id), None)
    if port is None:
        raise TaskActionError('goal_output_unknown', 'methods',
            reference.method_id+' 没有输出端口 '+reference.output_slot_id+
            '；可用端口：'+', '.join(p.id for p in method.output_slots)+'。只修正此引用，不重复调查数据。')
    return port


def ground_contract(registry, contract, targets=(), stage_outputs=()):
    """Semantic goal/scope stay canonical; concrete type keys come from ports."""
    result = TaskContract.model_validate(contract).model_copy(deep=True)
    if targets:
        pairs = list(dict.fromkeys(
            (r.method_id, r.output_slot_id) for r in map(OutputReference.model_validate, targets)))
        result.goal_output_refs = [OutputReference(method_id=m, output_slot_id=p) for m,p in pairs]
    if result.goal_output_refs:
        result.goal_artifact_types = list(dict.fromkeys(output_port(registry,r).type for r in result.goal_output_refs))
    vocabulary = {p.id for p in registry.artifact_types()}
    if not result.goal_artifact_types or set(result.goal_artifact_types)-vocabulary:
        raise TaskActionError('goal_output_selection_required', 'methods',
            '任务目标已保存，尚需选择真实输出；用 list_skill_methods(q=目标描述, limit=4) 查询少量候选，'
            '将候选 output_refs 传给 read_method_routes.targets。无需查询类型总表或文件结构。')
    if stage_outputs:
        previous_producers = {r.method_id for r in result.required_output_refs}
        stages = [StageOutputReference.model_validate(r) for r in stage_outputs]
        if sorted(r.stage_index for r in stages) != list(range(len(result.required_stages))):
            raise TaskActionError('required_stage_selection_incomplete', 'methods',
                'stage_outputs 必须逐项对应 required_stages，每个阶段选择一个真实输出引用；只补齐缺失阶段。')
        result.required_output_refs = [OutputReference(method_id=r.method_id,output_slot_id=r.output_slot_id)
                                      for r in sorted(stages,key=lambda r:r.stage_index)]
        result.required_method_ids = [m for m in result.required_method_ids if m not in previous_producers]
    if result.required_stages and len(result.required_output_refs) != len(result.required_stages):
        raise TaskActionError('required_stage_selection_incomplete', 'methods',
            '用户要求的中间阶段尚未全部选择真实输出；按 required_stages 序号补充 stage_outputs，保留最终目标。')
    if result.required_output_refs:
        result.required_output_types = list(dict.fromkeys(output_port(registry,r).type for r in result.required_output_refs))
        result.required_method_ids = list(dict.fromkeys([*result.required_method_ids,
            *(r.method_id for r in result.required_output_refs)]))
    return result


def save_grounding(ledger, request, contract):
    # Grounding enriches the current goal; it is not a new user task/revision.
    if request.assistant_task.get('task_contract') != contract.model_dump():
        ledger.save(request,{**request.assistant_task,'task_contract':contract.model_dump()})


def intent_schema(schema):
    """Legacy API fields remain readable but are not model-authored intent."""
    definitions = schema.get('$defs', {})
    contract = definitions.get('TaskContract', {})
    for field in ('goal_artifact_types','required_output_types','required_method_ids',
                  'goal_output_refs','required_output_refs','retrieval_queries'):
        contract.get('properties',{}).pop(field,None)
    return schema
