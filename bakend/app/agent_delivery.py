"""Current route readiness and gaps, independent of historical investigation prose."""


def matched_routes(task):
    from .agent_identity import task_closure
    if task_closure(task):
        return []
    saved = task.get('method_planning', {})
    summary = saved.get('state', {}).get('summary', {})
    current = saved.get('task_revision',1) == task.get('task_revision',1)
    return summary.get('routes', []) if current and saved.get('task_id') == task.get('task_id') and summary.get('status') == 'matched' else []


def latest_jobs(task):
    latest = {}
    ordered = sorted(enumerate(task.get('agent_jobs', {}).values()),
        key=lambda pair: (pair[1].get('updated_at', 0), pair[0]))
    for _, job in ordered:
        if job.get('status') != 'cancelled':
            latest[job['agent']] = job
    return latest


def route_report(task, rows, job):
    """Report the graph result, without certifying unasked/all-sample properties.

    Details remain in the graph; prepare_workflow validates real sample coverage.
    A broader assignment's extra questions are retained as unchecked, but they
    cannot conceal an available graph or force another specialist LLM round.
    """
    routes = matched_routes(task)
    refs = sorted({f for route in routes for f in route.get('fact_ids', [])})
    refs += [row['id'] for row in rows if row['tool'] == 'read_method_routes' and row['id'] not in refs]
    methods = list(dict.fromkeys(mid for route in routes for mid in route.get('method_ids', [])))
    return {'agent': 'methods', 'status': 'answered', 'assignment_complete': False,
        'planning_complete': True, 'assessment_kind': 'validated_method_graph', 'facts_are_authoritative': True,
        'answer': '已找到并保存真实方法路线；主控交付流程草案，外部输入由用户在任务卡选择，内容与覆盖留给dry-run。',
        'fact_ids': refs, 'route_ids': [r['route_id'] for r in routes], 'selected_methods': methods,
        'selection_reason': '登记依赖图及输入端口已核验', 'unresolved': [], 'report_warnings': [],
        'findings': [], 'unchecked_requirements': job.get('requirement_items', []),
        'next_step': 'prepare_workflow(goal,route_id)；图和默认参数已保存，不需要再次搜索或复制契约。'}


def current_gaps(task):
    """Only the latest assignment per responsibility is a current checkpoint.

    A matched graph supersedes discovery/representative-input questions. Their
    historical reports remain stored; preparation is the authoritative joint
    coverage check, and preparation errors take priority over old questions.
    """
    if task.get('preparation_error'):
        error = task['preparation_error']
        return [str(error.get('error', error) if isinstance(error, dict) else error)[:600]]
    latest = latest_jobs(task)
    ready = bool(matched_routes(task))
    config = task.get('execution_configuration', {})
    current_config = config.get('task_id') == task.get('task_id') and bool(config.get('status'))
    gaps = []
    for role, job in latest.items():
        if ready and role in {'data', 'methods'}:
            continue
        if role == 'runtime' and configuration_supersedes(task, job):
            continue  # Current plan configuration supersedes speculative environment investigation.
        gaps.extend(job.get('report', {}).get('unresolved', []))
    if current_config:
        gaps.extend('运行前配置：' + v['reason'] for v in config.get('required_before_run', []) if v.get('reason'))
    return list(dict.fromkeys(gaps))[:6]


def configuration_supersedes(task, job):
    config = task.get('execution_configuration', {})
    return config.get('task_id') == task.get('task_id') and bool(config.get('status')) and (
        job.get('runtime_scope') == 'scientific_configuration' or
        (job.get('agent') == 'runtime' and job.get('runtime_scope') is None and
         job.get('purpose') == 'initial' and task.get('deliverable') == 'workflow'))
