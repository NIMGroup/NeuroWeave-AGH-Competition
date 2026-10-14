"""Attach sourced configuration readiness to a real plan without another model round."""
from .agent_execution_config import configuration_result, workflow_configuration


def attach_workflow_configuration(owner, ledger, request, workflow):
    result = workflow_configuration(owner, workflow)
    fact_ids = []
    items = result.get('items', [])
    for offset in range(0, len(items), 20):
        chunk = items[offset:offset+20]
        args = {'method_ids': sorted(i['method_id'] for i in chunk)}
        cached = ledger.facts.lookup(owner, request, 'read_execution_configuration', args)
        if cached:
            fact_ids.append(cached['id'])
            continue
        raw = configuration_result(chunk)
        ref = ledger.record(request, 'read_execution_configuration', args, raw)
        row = ledger.facts.record(request, 'runtime', 'read_execution_configuration', args, raw,
                                 ref, raw['configuration_version'])
        if row:
            fact_ids.append(row['id'])
    result['fact_ids'] = fact_ids
    result['task_id'] = request.assistant_task.get('task_id')
    changed = workflow.planning_diagnostics.get('execution_configuration') != result
    workflow.planning_diagnostics['execution_configuration'] = result
    prefix = '运行前配置：'
    workflow.warnings = [w for w in workflow.warnings if not w.startswith(prefix)]
    workflow.warnings.extend(prefix + i['reason'] + ('（' + i['method_id'] + '）' if i.get('method_id') else '')
        for i in result.get('required_before_run', []))
    if changed:
        owner.service.store.save_workflow(workflow, event_type='workflow_configuration_checked')
    needs = [dict(n) for n in request.assistant_task.get('information_needs', [])]
    for need in needs:
        if need.get('owner') == 'runtime' and need.get('phase') == 'configuration':
            need.update(status='resolved' if result['status'] == 'configured' else 'open',
                conclusion='所选方案的软件路径已配置；实际执行仍需预检和手动运行。' if result['status'] == 'configured' else
                           '；'.join(i['reason'] for i in result.get('required_before_run', [])),
                evidence_refs=fact_ids[:8])
    task = {**request.assistant_task, 'execution_configuration': result, 'information_needs': needs}
    if task.get('last_error', {}).get('error_code') in {'runtime_dependency_not_ready', 'unknown_configuration_method'}:
        task.pop('last_error', None)
    ledger.save(request, task)
    return result
