"""Treat sandbox-produced changes as data, using the trusted batch validator."""
import json
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field

from .data_models import DataEdit, GeneratedDataPlan
from .data_selection import provenance_paths
from .script_contract import MAX_CHANGE_EDITS, MAX_CHANGE_BYTES


class ScriptChanges(BaseModel):
    model_config = ConfigDict(extra='forbid')
    edits: list[DataEdit] = Field(max_length=MAX_CHANGE_EDITS)


def read_script_changes(service, request, scope, artifacts):
    path = artifacts / 'changes.json'
    if not path.is_file():
        raise ValueError('脚本未输出 /output/changes.json；尚未生成可执行操作清单')
    service._artifact_path(path)
    if path.stat().st_size > MAX_CHANGE_BYTES:
        raise ValueError('脚本操作清单超过 32 MiB；未接受不完整清单')
    if any(p.is_file() and p != path for p in artifacts.rglob('*')):
        raise ValueError('changes 结果仅允许 changes.json；生成文件请使用 files 结果')
    payload = ScriptChanges.model_validate(json.loads(path.read_text(encoding='utf-8-sig')))
    allowed = provenance_paths(service, scope, request.selection)
    for edit in payload.edits:
        if edit.action not in request.change_actions:
            raise PermissionError('脚本操作清单包含未声明的动作')
        if not request.selection.matches(edit.path) or allowed is not None and edit.path not in allowed:
            raise PermissionError('脚本操作清单超出本轮选择范围')
        for relative in [edit.path, *([edit.destination] if edit.destination is not None else [])]:
            if '\\' in relative or ':' in relative or PurePosixPath(relative).is_absolute() or '..' in PurePosixPath(relative).parts:
                raise PermissionError('脚本操作清单须使用安全相对路径')
    if not payload.edits:
        return None
    return GeneratedDataPlan(**scope, title=request.title, edits=payload.edits, execution='preview')
