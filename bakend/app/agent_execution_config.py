"""Read resolved runner configuration without invoking software or probing Python packages."""
from pathlib import PurePath

from .fact_memory import digest

from pydantic import BaseModel, ConfigDict, Field


class ExecutionConfigurationQuery(BaseModel):
    model_config = ConfigDict(extra='forbid')
    method_ids: list[str] = Field(min_length=1, max_length=20,
        description='只查询方案所需的真实方法 ID；不普查所有软件或 Python 环境。')


EXECUTION_CONFIG_TOOL = {'type': 'function', 'function': {
    'name': 'read_execution_configuration',
    'description': '只读检查必要方法的 Runner 和软件路径是否配置。不执行命令、不核查版本/依赖/GPU；Python 环境仅在实际需要时另查。配置不是运行成功的证明。',
    'parameters': ExecutionConfigurationQuery.model_json_schema()}}


class ConfigurationIdentityError(ValueError):
    """A catalog identity error is repaired from a route, never by synonym retries."""

    error_code = 'unknown_configuration_method'
    recoverable = False


def method_configuration(identity, method, runners):
    runner = runners.get(method.runner_id) if method.runner_id else None
    raw_path = str(getattr(runner, 'executable', '') or '')
    path_required = runner is not None and hasattr(runner, 'executable')
    configured = bool(raw_path.strip() and raw_path not in {'.', '/'})
    spec = getattr(runner, 'spec', {}) or {}
    required_paths = list(spec.get('executables', []))
    is_python = PurePath(raw_path.replace('\\', '/')).name.lower().startswith('python')
    if getattr(method, 'execution_recipe', None) is not None:
        status, reason = 'unknown', '复合方法需按准备好的方案展开实际执行单元'
    elif method.execution_mode != 'runner':
        status, reason = 'missing_configuration', '方法尚未接入执行器'
    elif runner is None:
        status, reason = 'missing_configuration', 'Runner 尚未登记'
    elif path_required and not configured:
        status, reason = 'missing_configuration', '必要软件路径未配置'
    elif any(not str(p).strip() or str(p) in {'.', '/'} for p in required_paths):
        status, reason = 'missing_configuration', '附属软件路径未配置'
    else:
        status, reason = 'configured', ''
    return {'method_id': identity, 'runner_id': method.runner_id,
        'execution_mode': method.execution_mode, 'runner_registered': runner is not None,
        'executable_path': raw_path, 'path_required': path_required, 'path_configured': configured,
        'configuration_source': 'resolved_runner_configuration',
        'required_software_paths': required_paths, 'required_python_modules': list(spec.get('modules', [])),
        'python_environment_needed': is_python, 'python_environment_scope': 'scientific_runner' if is_python else None,
        'execution_scope': 'scientific_runner', 'status': status, 'reason': reason, 'check': 'configuration_only'}


def configuration_result(items):
    pending = [i for i in items if i['status'] != 'configured']
    status = 'missing_configuration' if any(i['status'] == 'missing_configuration' for i in pending) else 'unknown' if pending else 'configured'
    return {'items': items, 'total_items': len(items), 'scope_complete': True,
        'execution_scope': 'scientific_runner', 'check': 'configuration_only',
        'configuration_version': digest(items), 'status': status,
        'required_before_run': [{'method_id': i['method_id'], 'reason': i['reason']} for i in pending],
        'execution_verified': False,
        'notice': '仅检查已选方法的配置；路径已填写不证明文件存在或计算成功。科学 Python 属于 Runner，数据脚本沙箱的库和资源配额不适用于它。实际运行仍需用户点击运行及原有预检。'}


def configuration_view(result):
    """A compact readiness view; exact configurations remain in sourced facts."""
    value = {k: v for k, v in result.items() if k != 'items'}
    items = result.get('items', [])
    value['items'] = [{k: i[k] for k in ('method_id', 'runner_id', 'status', 'reason') if k in i} for i in items[:6]]
    value['items_complete'] = result.get('items_complete', True) and len(items) <= 6 and result.get('total_items', len(items)) <= len(items)
    return value


def read_execution_configuration(owner, arguments):
    query = ExecutionConfigurationQuery.model_validate(arguments)
    catalog = owner.skill_catalog
    if catalog is None:
        raise ValueError('科学方法目录尚未配置')
    items = []
    for identity in sorted(set(query.method_ids)):
        method = catalog.registry.method_for('method', identity)
        if method is None:
            raise ConfigurationIdentityError('未登记的方法 ID：' + identity + '；使用已保存路线中的真实 ID，禁止猜名称重试')
        items.append(method_configuration(identity, method, catalog.runners))
    return configuration_result(items)


def configuration_stamp(owner, name, arguments):
    """Hash resolved configuration only; live results and runtime probes are excluded."""
    if name == 'read_execution_configuration':
        try:
            return read_execution_configuration(owner, arguments)['configuration_version']
        except ConfigurationIdentityError:
            return None  # The actual query emits one deterministic identity error.
    if name == 'list_data_environments' and owner.data_operations:
        return digest(owner.data_operations.environments())
    return None


def workflow_configuration(owner, workflow):
    """Expand executable recipes, including their internal runner methods."""
    from .execution.recipe_compiler import RecipeCompiler, RecipeCompilationError
    catalog = getattr(owner, 'skill_catalog', None)
    if catalog is None:
        return {'status': 'unknown', 'execution_scope': 'scientific_runner', 'check': 'configuration_only',
                'execution_verified': False, 'required_before_run': [{'reason': '方法配置目录不可读取'}]}
    try:
        units = RecipeCompiler(catalog.registry).compile(workflow)
    except RecipeCompilationError as exc:
        return {'status': 'unknown', 'execution_scope': 'scientific_runner', 'check': 'configuration_only',
                'execution_verified': False, 'required_before_run': [{'reason': str(exc)}]}
    if not units:
        return {'status': 'unknown', 'execution_scope': 'scientific_runner', 'check': 'configuration_only',
                'execution_verified': False, 'required_before_run': [{'reason': '方案尚无实际执行单元'}]}
    methods = {unit.method.id: unit.method for unit in units}
    return configuration_result([method_configuration(mid, method, catalog.runners)
        for mid, method in sorted(methods.items())])
