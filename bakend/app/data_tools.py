"""Selected-output writes; explicit overwrite/delete/dataset grants stay HTTP-only."""
from .data_models import DataPlanRequest, OrganizeRequest, PythonRequest, TransformRequest
from .assistant_tasks import compact_operation
from .assistant_contract import check_proposal, compatible_baseline, StepMismatch
from .data_selection import DataSelection


DATA_PROMPT = """
依据当前 step 的来源、范围和动作选择数据工具；延续目标不代表重复旧动作。
仅同一待运行阶段可增量修订规则，完成批次用作来源记录，不自动继承为执行模板。
名称整理用规则或名称变换；文本修改用结构化清单；复杂统计或内容计算可准备隔离脚本。
规则含义须用实际样例和必要元数据核实，不根据扩展名推断科学语义；大批处理使用后端完整扫描。
冲突或覆盖不足时先检查方向，再修参数或换工具；不能靠扩大覆盖权限掩盖错误。
脚本环境先查询可用库，不安装依赖。脚本输入只读，运行及生成文件保存各须用户点击运行。
现有规则表达不了目标时改用脚本。若先生成映射/统计报告，用 prepare_current_step(preparation=true) 准备同来源同范围的新 copy/analysis 脚本，然后 run_data_python；生成完整清单或报告，不能只打印样例。用户选择已明确时直接准备卡片，不再索取方法切换的泛化确认。
目标是文件改动且算法需脚本时，优先准备 result_kind=changes 的 copy/inplace 脚本，change_actions 保持当前步骤动作；完整 edits 写到 /output/changes.json。运行脚本只计算清单，后端把结果接到同一任务卡的操作预览；用户再次点击运行后才真正改文件。保留原步骤的改名/复制/文本/删除动作，不把目标降为仅打印映射的 analysis。
"""


def _tool(name, description, model):
    definition = model.model_json_schema()
    for field in ("conversation_id", "dataset_id", "workspace_id", "background"):
        definition["properties"].pop(field, None)
    definition["required"] = [v for v in definition.get("required", []) if v != "conversation_id"]
    return {"type": "function", "function": {"name": name, "description": description, "parameters": definition}}


from .agent_metadata import METADATA_TOOL, inspect_metadata
from .agent_file_queries import FILE_QUERY_TOOL, query_files
from .data_sequences import SEQUENCE_TOOL, inspect_sequences

DATA_TOOLS = [
    FILE_QUERY_TOOL,
    METADATA_TOOL,
    SEQUENCE_TOOL,
    {"type": "function", "function": {"name": "list_data_environments", "description": "查询预装 Python 库、可用环境和固定资源配额；只读。",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
    _tool("propose_data_changes", "准备批量文件/文本改动预览；必须由用户点击运行，任何参数都不会自动执行。", DataPlanRequest),
    _tool("propose_data_organization", "完整扫描并按序列 glob 规则批量改名/复制；同步关联文件，预览冲突。", OrganizeRequest),
    _tool('propose_filename_transform', '对完整选择范围逐文件变换名称：按顺序替换文本、去前缀或去后缀；默认保留扩展名和父目录。可按已完成批次的 preserved/changed 类别选择输出文件。仅生成待运行清单，不重新复制旧数据集；不自动更新文件内容中的路径引用。', TransformRequest),
    _tool("run_data_python", "准备隔离 Python 脚本：来源挂载 /input 只读，结果写 /output。文件操作可用 result_kind=changes 输出完整 changes.json，后端转换为待运行操作清单；files 收集生成文件，analysis 仅返回日志。脚本与最终文件修改各须用户点击运行。", PythonRequest),
    {"type": "function", "function": {"name": "list_data_operations", "description": "查询当前会话的数据任务及授权后执行结果。",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "get_data_operation", "description": "查询数据任务真实状态、冲突和进度。",
        "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "inspect_data_coverage", "description": "完整冻结快照的未覆盖类型、文件名分组、规则与覆盖变化；分页样例不能代替该统计。",
        "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "list_data_operation_files", "description": "查询已有批次的操作或未匹配文件清单、数量与类型；只读分页，不准备新操作。",
        "parameters": {"type": "object", "properties": {"id": {"type": "string"},
            "kind": {"type": "string", "enum": ["unmatched", "changes", "preserved"]},
            "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 200}},
            "required": ["id"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "inventory_data_files", "description": "批量任务优先查询：后端遍历选择范围，默认跳过符号链接并报告 warnings、skipped_symlinks；数量不包含链接目标。view=summary 返回完整数量、名称分组和少量样例；足够后直接准备规则/名称变换/脚本，不逐项枚举。view=files 才读取分页明细；预览不是执行清单，批量方案会再次完整扫描。",
        "parameters": {"type": "object", "properties": {"source_scope": {"type": "string", "enum": ["dataset", "output"]},
             "path": {"type": "string"}, 'selection': DataSelection.model_json_schema(),
             'view': {'type':'string','enum':['summary','files'],'default':'files'},
             'follow_links': {'type':'boolean','default':False,'description':'仅本次只读查询可设 true：跟随当前授权范围内的链接，排除循环和重复目标。默认跳过并报告；不扩大修改方案范围。'},
             'inventory_id': {'type':'string','description':'本轮同范围清单 ID，可继续分页；不能作为执行授权或跨会话复用。'},
             'group_offset': {'type':'integer','minimum':0,'description':'summary 中名称分组的起点，使用 coverage.next_group_offset。'},
             "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}},
         "additionalProperties": False}}},
]
PROPOSAL_TOOLS = {"propose_data_changes", "propose_data_organization", "propose_filename_transform", "run_data_python"}


def execute_data_tool(service, name, arguments, request):
    if name in PROPOSAL_TOOLS and request.interaction_mode != "task":
        raise PermissionError("仅问答模式不能准备文件改动，请使用助手模式")
    if name in PROPOSAL_TOOLS and request.response_policy and request.response_policy.action in {"answer", "explore", "clarify", "inspect"}:
        raise PermissionError("本轮只允许回答和查询")
    if any(k in arguments for k in ("conversation_id", "dataset_id", "workspace_id", "root_path")):
        raise PermissionError("操作范围由当前会话确定")
    scope = {"conversation_id": request.conversation_id, "dataset_id": request.dataset_id, "workspace_id": request.workspace_id}
    if name == 'query_data_files':
        scope.update(source_scope=arguments.get('source_scope'), mode='inplace')
        return query_files(service, request, scope, arguments)
    if name == 'inspect_data_metadata':
        scope['source_scope'] = arguments.get('source_scope')
        scope['mode'] = 'inplace'
        return inspect_metadata(service, request, scope, arguments)
    if name == 'inspect_data_sequences':
        scope.update(source_scope=arguments.get('source_scope'), mode='inplace')
        return inspect_sequences(service, request, scope, arguments)
    if name == "propose_data_changes":
        plan = DataPlanRequest(**scope, **arguments)
        check_proposal(service, request, source_scope=plan.source_scope, mode=plan.mode,
                       actions=[e.action for e in plan.edits], paths=[e.path for e in plan.edits])
        return service.propose(plan)
    if name == 'propose_filename_transform':
        from .data_transform import transform
        arguments = dict(arguments)
        arguments.setdefault('selection', request.assistant_task.get('step', {}).get('selection', {}))
        plan = TransformRequest(**scope, **arguments)
        check_proposal(service, request, source_scope=plan.source_scope, mode=plan.mode,
                       actions=['rename' if plan.mode == 'inplace' else 'copy'], selection=plan.selection)
        return transform(service, plan)
    if name == "propose_data_organization":
        arguments = dict(arguments)
        if request.assistant_task.get('protocol_version') != 2 and arguments.get('replace_rules') and request.assistant_task.get('goal') and not request.assistant_task.get('allow_recipe_replacement'):
            raise ValueError('用户要求保留既定规则；请用 base_operation_id 增量修正，不要整组替换规则')
        if request.assistant_task.get('coverage') == 'all':
            arguments['require_complete'] = True
        arguments.setdefault('selection', request.assistant_task.get('step', {}).get('selection', {}))
        plan = OrganizeRequest(**scope, **arguments)
        selection = plan.selection.model_copy(update={'path': plan.path}) if plan.path is not None else plan.selection
        # Only same-step drafts and explicitly selected pending revisions inherit recipes.
        if not plan.base_operation_id and not plan.replace_rules:
            baseline = compatible_baseline(service, request, plan.source_scope, plan.mode, selection)
            if baseline:
                plan.base_operation_id = baseline
        check_proposal(service, request, source_scope=plan.source_scope, mode=plan.mode,
                       actions=['copy' if plan.mode == 'copy' else 'rename'], selection=selection, base_id=plan.base_operation_id)
        return service.organize(plan)
    if name == "run_data_python":
        if 'background' in arguments:
            raise ValueError('脚本工具统一后台执行')
        arguments = dict(arguments)
        arguments.setdefault('selection', request.assistant_task.get('step', {}).get('selection', {}))
        plan = PythonRequest(**scope, **arguments, background=True)
        check_proposal(service, request, source_scope=plan.source_scope, mode=plan.mode, selection=plan.selection,
                       actions=plan.change_actions if plan.result_kind == 'changes' else None)
        actions = request.assistant_task.get('step', {}).get('actions', [])
        if plan.result_kind == 'files' and actions and plan.mode != 'analysis' and not set(actions) & {'write_text', 'script'}:
            raise StepMismatch('脚本仅能分析或生成文件版本；改名、删除等清单请使用结构化文件工具')
        return service.python(plan)
    if name == 'list_data_environments':
        if arguments:
            raise ValueError('不允许额外参数')
        return service.environments()
    if name == "get_data_operation":
        if set(arguments) != {"id"}:
            raise ValueError("需要准确任务 ID")
        return service.get(arguments["id"], request.conversation_id)
    if name == "list_data_operations":
        if arguments:
            raise ValueError("不允许额外参数")
        return {"items": [compact_operation(t) for t in service.list(request.conversation_id)]}
    if name == 'inspect_data_coverage':
        if set(arguments) != {'id'}:
            raise ValueError('需要准确任务 ID')
        return service.coverage(arguments['id'], request.conversation_id)
    if name == "list_data_operation_files":
        if "id" not in arguments or set(arguments) - {"id", "kind", "offset", "limit"}:
            raise ValueError("需要准确任务 ID 和有效分页参数")
        return service.operation_files(arguments["id"], request.conversation_id,
                                       arguments.get("kind", "unmatched"), arguments.get("offset", 0), arguments.get("limit", 50))
    if name == "inventory_data_files":
        if set(arguments) - {"source_scope", "path", "selection", "offset", "limit", "view", "inventory_id", "group_offset", "follow_links"}:
            raise ValueError("不允许额外参数")
        source_scope = arguments.get("source_scope", request.assistant_task.get('step', {}).get('source_scope', 'dataset'))
        if source_scope not in {"dataset", "output"}:
            raise ValueError("无效数据范围")
        scope.update(source_scope=source_scope, mode="inplace")
        from .assistant_inventory import inventory_query
        return inventory_query(service, request, scope, arguments)
    raise ValueError("未知数据工具")
