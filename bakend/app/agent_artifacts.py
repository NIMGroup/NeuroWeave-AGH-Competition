"""Current, scoped artifact snapshots and deterministic action guidance."""
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from .agent_context import tool_projection
from .agent_state import artifact
from .operation_context import operation_thread


ARTIFACT_READ_TOOL = {'type': 'function', 'function': {
    'name': 'read_task_artifact',
    'description': '读取当前任务已登记产物的最新状态、脚本、清单或日志。结果反馈已注入当前产物；仅缺具体字段时定向读取，不检索历史来判断当前阶段。只读，不运行。',
    'parameters': {'type': 'object', 'properties': {
        'id': {'type': 'string'}, 'field': {'type': 'string', 'enum': ['state', 'script', 'manifest', 'log'], 'default': 'state'},
        'offset': {'type': 'integer', 'minimum': 0, 'default': 0},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 12000, 'default': 6000}},
        'required': ['id'], 'additionalProperties': False}}}


def guidance(result):
    status = result.get('status')
    if status in {'failed', 'interrupted', 'cancelled', 'rolled_back'}:
        return {'stage': status, 'action': 'inspect_result', 'message': '本次执行未完成原目标；查看实际错误或取消结果，修订后的操作仍需手动运行。'}
    if status in {'succeeded', 'analyzed'}:
        return {'stage': status, 'action': 'verify_result', 'message': '执行已结束，核对实际产物是否满足原需求；不要重新执行已完成步骤。'}
    if status != 'pending':
        return {'stage': status or 'unknown', 'action': 'wait', 'message': '根据当前后台进度等待或停止，不重复启动任务。'}
    if result.get('kind') == 'workflow':
        if result.get('missing_inputs'):
            if result.get('input_selection') == 'user' and result.get('draft_ready'):
                return {'stage':'awaiting_inputs','action':'select_inputs',
                        'message':'流程草案已生成，尚未执行。请在任务卡选择输入或采用批量规则，再审阅参数并点击 dry-run。'}
            return {'stage': 'missing_inputs', 'action': 'complete_inputs', 'message': '流程尚缺必需输入，补齐并核验后才能手动运行。'}
        config = result.get('execution_configuration', {})
        if config.get('status') in {'missing_configuration', 'unknown'}:
            reasons = '；'.join(dict.fromkeys(v['reason'] for v in config.get('required_before_run', []) if v.get('reason')))
            return {'stage': 'configuration_required', 'action': 'configure_execution',
                    'message': '流程方案已生成，尚未执行；运行前需补齐配置：' + (reasons or '执行配置未知') + '。配置后预检并手动运行。'}
        return {'stage': 'workflow_ready', 'action': 'run_workflow', 'message': '流程已准备，尚未执行；核对后点击运行。'}
    if result.get('kind') != 'data':
        return {'stage': 'operation_ready', 'action': 'run_operation', 'message': '操作已准备，尚未执行；核对后点击运行，覆盖和删除需确认。'}
    script = result.get('phase') == 'script' and not result.get('manifest_ready', True)
    return {'stage': 'script_preview' if script else 'file_application',
            'action': 'run_script' if script else 'apply_manifest',
            'message': ('脚本已准备，尚未执行。点击运行生成预览或分析结果；产生文件清单后需再次手动运行才能应用。' if script else
                        '文件预览已生成，尚未应用。检查当前清单后点击运行应用改动；覆盖和删除仍需确认。')}


class ArtifactRead(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(min_length=1, max_length=160)
    field: Literal['state', 'script', 'manifest', 'log'] = 'state'
    offset: int = Field(default=0, ge=0, strict=True)
    limit: int = Field(default=6000, ge=1, le=12000, strict=True)


async def read_artifact(owner, ledger, request, args):
    args = ArtifactRead.model_validate(args).model_dump()
    identity = args['id']
    known = request.assistant_task.get('artifacts', {}).get(identity)
    if not known:
        raise PermissionError('产物不属于当前任务；使用已登记的实际 ID')
    kind = known.get('kind')
    if kind == 'data':
        result = await operation_thread(owner.data_operations.get, identity, request.conversation_id)
    elif kind == 'workflow':
        workflow = owner.workflow_service.get(identity)
        from .agent_plan_config import attach_workflow_configuration
        from .agent_execution_config import configuration_view
        config = await operation_thread(attach_workflow_configuration, owner, ledger, request, workflow)
        result = {'id': identity, 'kind': kind, 'status': 'pending', 'revision': workflow.revision,
                  'input_selection':workflow.input_selection,'draft_ready':not workflow.contract_errors,
                  'title': workflow.title, 'steps': [v.model_dump(mode='json') for v in workflow.steps],
                  'missing_inputs': [v.missing_required_inputs for v in workflow.steps if v.missing_required_inputs],
                  'execution_configuration': configuration_view(config)}
        report=workflow.dry_run_report
        result['input_validation']='passed_dry_run' if report and report.valid else 'failed_dry_run' if report else 'pending_dry_run'
        if report:
            result['preflight_summary']={'valid':report.valid,'checked_at':report.checked_at,
                'quality_summary':report.quality_summary,
                'errors':[row for row in report.quality_checks if row.get('status')=='error'][:12],
                'gradient_sources':sorted({row.get('details',{}).get('source','') for row in report.quality_checks
                                          if row.get('code')=='gradient_source_resolved'}),
                'cached_content_checks':len(workflow.input_check_cache)}
    else:
        from .agent_runtime import execute
        result = await execute(owner, ledger, request, 'get_workflow_result', {'workflow_id': known['workflow_id']}, []) if kind == 'workflow_run' else await operation_thread(owner.service.operation, identity)
    ledger.record(request, 'current_artifact', {}, result)
    field, offset, limit = args.get('field', 'state'), args.get('offset', 0), args.get('limit', 6000)
    if field == 'state':
        return {**tool_projection(result), **artifact(result), 'stage_guidance': guidance(result),
                'available_fields': ['state', 'script', 'manifest', 'log'] if kind=='data' else ['state']}
    if kind != 'data':
        raise ValueError('该产物不支持脚本、文件清单或脚本日志字段')
    if field == 'manifest':
        rows = await operation_thread(owner.data_operations.repository.items, identity, offset, min(limit, 30))
        items = [{k: row.get(k) for k in ('action', 'path', 'destination', 'size')} for row in rows]
        return {'id': identity, 'version': artifact(result)['version'], 'items': items,
                'offset': offset, 'total': result['file_count'], 'next_offset': offset+len(items) if offset+len(items)<result['file_count'] else None}
    if field == 'log':
        from .agent_runtime import read_result
        return await operation_thread(read_result, owner, request, {'id': identity, 'offset': offset, 'max_chars': limit})
    text = result.get('script', '')
    return {'id': identity, 'version': artifact(result)['version'], 'script_hash': result.get('script_hash'),
            'content': text[offset:offset+limit], 'total_chars': len(text), 'offset': offset,
            'next_offset': offset+limit if offset+limit<len(text) else None,
            'stage_guidance': guidance(result)}


async def inject_feedback(team):
    """Re-read the authoritative service at processing time, never trust event snapshots."""
    feedback = team.request._execution_feedback
    if not feedback:
        return None
    identity = feedback.get('source_id') or feedback.get('id')
    if identity not in team.request.assistant_task.get('artifacts', {}):
        raise PermissionError('结果反馈不属于当前任务')
    known = team.request.assistant_task['artifacts'][identity]
    if known.get('kind') == 'workflow':
        from .agent_runtime import execute
        result = await execute(team.owner, team.ledger, team.request, 'get_workflow_result', {'workflow_id':identity}, [])
        team.ledger.record(team.request, 'current_artifact', {}, result)
        if result.get('kind') == 'workflow_run':
            identity=result['id']
    snapshot = await read_artifact(team.owner, team.ledger, team.request, {'id': identity})
    if snapshot.get('kind') == 'data':
        task = await operation_thread(team.owner.data_operations.repository.load, identity, 'preview')
        script = task.get('script', '')
        snapshot['script_source'] = {'content':script[:12000], 'total_chars':len(script), 'next_offset':12000 if len(script)>12000 else None}
        snapshot['declared_validation'] = {k:task.get('_python_request',{}).get(k) for k in ('test_cases','manifest_invariants')}
    current = team.request.assistant_task['artifacts'][identity]
    if current.get('status') == 'pending' and not (snapshot.get('kind') == 'workflow' and snapshot.get('missing_inputs')):
        team.feedback_review_ids = [identity]
    team.observations.append({'tool': 'current_artifact', 'arguments': {'id': identity}, 'result': snapshot})
    snapshot['feedback_notice'] = '这是处理开始时从实际服务读取的当前版本；旧脚本审批不覆盖新清单。直接核验此版本；缺具体字段用 read_task_artifact。历史用于用户原话，不用于发现当前结果。'
    team.event('current_artifact', 'coordinator', 'succeeded', artifact_id=identity,
               version=current.get('version'), stage=snapshot['stage_guidance']['stage'])
    return snapshot


def normalize_summary(summary):
    """Repair escaped prose paragraphs without interpreting code or file paths."""
    import re
    pieces = re.split(r'(```[\s\S]*?```|`[^`\n]*`)', summary)
    for i in range(0, len(pieces), 2):
        text = pieces[i].replace('\\n\\n', '\n\n')
        text = re.sub(r'([。！？；：])\\n', r'\1\n', text)
        text = re.sub(r'\\n(?=【|#{1,6} |[-*] |\d+[.)] )', '\n', text)
        pieces[i] = text
    return ''.join(pieces)


def stage_summary(summary, items):
    """The backend owns the next action; model prose supplies findings only."""
    summary = normalize_summary(summary)
    if not items:
        return summary, ''
    hints = list(dict.fromkeys(guidance(v)['message'] for v in items))
    # Keep explanations, replace explicit procedural sections from older models.
    import re
    text = re.sub(r'(?:【下一步】|下一步[：:])[^\n]*(?:\n(?!\n|【)[^\n]*)*', '', summary).strip()
    hint = '\n'.join(hints)
    return hint + ('\n\n'+text if text else ''), hint
