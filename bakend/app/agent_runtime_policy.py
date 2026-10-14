"""Runtime investigations follow declared execution needs, not generic task readiness."""
from .agent_delivery import matched_routes


CONFIG_TOOL = 'read_execution_configuration'
SCRIPT_TOOL = 'list_data_environments'
CONFIG_SCOPES = {'scientific_configuration', 'script_environment', 'environment_question'}


class RuntimeDependencyError(ValueError):
    error_code = 'runtime_dependency_not_ready'
    recoverable = False


def selected_methods(task):
    selection = task.get('method_selection', {})
    if selection.get('task_id') == task.get('task_id') and selection.get('method_ids'):
        return list(dict.fromkeys(selection['method_ids']))
    return list(dict.fromkeys(mid for route in matched_routes(task) for mid in route.get('method_ids', [])))


def has_script_need(task):
    return task.get('execution_kind') == 'python_script' or any(
        v.get('artifact_type') == 'script_plan' for v in task.get('artifacts', {}).values())


def default_scope(task):
    if task.get('phase') == 'verifying':
        return 'execution_result'
    return 'scientific_configuration' if task.get('deliverable') == 'workflow' else 'execution_result'


def investigation_scope(task, args):
    scope = args.get('runtime_scope')
    if scope is None:
        scope = default_scope(task)
    if scope not in CONFIG_SCOPES | {'execution_result'}:
        raise ValueError('未知环境调查范围')
    if scope == 'scientific_configuration' and not selected_methods(task):
        raise RuntimeDependencyError('先选择真实方法路线；配置由准备方案时自动检查，不需要先普查环境')
    if scope == 'script_environment' and not has_script_need(task):
        raise RuntimeDependencyError('尚无 Python 脚本需求；先记录 execution_kind=python_script，科学 Runner 不查询脚本沙箱')
    if scope == 'script_environment' and task.get('deliverable') == 'workflow':
        raise RuntimeDependencyError('科学流程使用 Runner 配置，不能用数据脚本沙箱评估科学环境')
    return scope


def runtime_tools(tools, task, scope):
    result = []
    for tool in tools:
        name = tool['function']['name']
        if name == CONFIG_TOOL and scope not in {'scientific_configuration', 'environment_question'}:
            continue
        if name == SCRIPT_TOOL and scope not in {'script_environment', 'environment_question'}:
            continue
        if name == CONFIG_TOOL and scope == 'scientific_configuration':
            tool['function']['parameters']['properties']['method_ids']['items']['enum'] = selected_methods(task)
        result.append(tool)
    return result


def validate_runtime_query(task, name, args, scope=None):
    if name == SCRIPT_TOOL:
        if scope == 'environment_question':
            return
        investigation_scope(task, {'runtime_scope': 'script_environment'})
    if name == CONFIG_TOOL and scope != 'environment_question':
        investigation_scope(task, {'runtime_scope': 'scientific_configuration'})
        ids = args.get('method_ids', [])
        if not isinstance(ids, list) or set(ids) - set(selected_methods(task)):
            raise RuntimeDependencyError('配置查询仅限当前已选方法；直接准备保存路线，不猜方法名称或扩展软件范围')


def infer_dependencies(task, assignments):
    """Only configuration needs a method dependency; execution feedback remains independent."""
    roles = {a.get('agent') for a in assignments}
    for args in assignments:
        if args.get('agent') != 'runtime':
            continue
        scope = args.get('runtime_scope') or default_scope(task)
        if scope == 'scientific_configuration' and not selected_methods(task) and 'methods' in roles:
            deps = args.get('depends_on', [])
            if isinstance(deps, list):
                args['depends_on'] = list(dict.fromkeys([*deps, 'methods']))
