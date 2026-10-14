"""Semantic read identities and the public task view; never run grants."""
import json
from pathlib import PurePosixPath
from .data_selection import DataSelection


READ_NAMES = {"inventory_data_files", "query_data_files", "inspect_data_metadata", "read_conversation_memory",
              "search_conversation_history", "list_data_environments"}
LIVE_NAMES = {"get_data_operation", "list_data_operations", "get_workflow_result",
              "list_data_operation_files", "inspect_data_coverage", "read_data_result"}


def is_read(name):
    return name in READ_NAMES | LIVE_NAMES or name.startswith(("list_", "read_", "inspect_", "search_", "get_"))


def query_signature(name, arguments):
    value = dict(arguments)
    if name == 'read_execution_configuration' and isinstance(value.get('method_ids'), list) and all(isinstance(i,str) for i in value['method_ids']):
        value['method_ids'] = sorted(set(value['method_ids']))
    # Only collapse documented defaults. Invalid or scope-changing parameters stay distinct.
    if name == "inventory_data_files":
        try:
            selection = DataSelection.model_validate(value.pop("selection", {}))
            if "path" in value:
                selection = DataSelection.model_validate({**selection.model_dump(), "path": value.pop("path")})
            value["selection"] = selection.model_dump()
        except ValueError:
            return name, json.dumps(arguments, sort_keys=True, ensure_ascii=False)
        for key, default in {"view": "files", "offset": 0, "group_offset": 0, "limit": 30}.items():
            value.setdefault(key, default)
        # A missing lease and the current lease refer to the same per-turn snapshot.
        value.pop("inventory_id", None)
    elif name.startswith(("list_", "read_", "inspect_", "search_")) and any(
            part in name for part in ("_output_", "_dataset_", "_workspace_")):
        path = value.get("path", ".")
        if isinstance(path, str) and not PurePosixPath(path.replace("\\", "/")).is_absolute() and ".." not in PurePosixPath(path.replace("\\", "/")).parts:
            value["path"] = PurePosixPath(path.replace("\\", "/")).as_posix()
        if name.startswith("list_"):
            for key, default in {"limit": 50, "view": "names", "sort": "name", "filter": "",
                                 "filter_mode": "text", "show_hidden": False}.items():
                value.setdefault(key, default)
        elif name.startswith("read_"):
            value.setdefault("offset", 0)
            value.setdefault("max_chars", 8000)
    return name, json.dumps(value, sort_keys=True, ensure_ascii=False)


def public_understanding(value):
    return {k: value[k] for k in ("task_id", "goal", "deliverable", "phase", "next_step",
            "scope_notes", "success_criteria", "strategy", "assumptions", "open_questions", "decisions",
            "user_requirements", "task_revision", "active_plan", "goal_source", "task_deleted", "plan_check", "scope", "updated_at",
            "user_completion", "user_closure") if k in value}


def task_closure(value):
    """Neutral closure, including the earlier completion-only records."""
    if value.get('user_closure'):
        return value['user_closure']
    if value.get('user_completion'):
        return {**value['user_completion'],'outcome':'completed'}
    return None


def closed_task_phase(value):
    return 'user_ended' if (task_closure(value) or {}).get('outcome') == 'ended' else 'user_confirmed'
