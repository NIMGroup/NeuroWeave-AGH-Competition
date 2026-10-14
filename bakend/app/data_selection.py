"""Bounded file selections and completed-operation provenance, never grants."""
import fnmatch
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .model_validation import ModelValidationError


class DataSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    path: str = Field(default='.', min_length=1, max_length=4096,
        description='当前 source_scope 所选根目录下的安全相对路径；根目录用点号 .，不要填写输出/数据集的绝对路径，不允许空串、冒号或 .. 路径段。')
    include: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(default_factory=list, max_length=100)
    exclude: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(default_factory=list, max_length=100)
    from_operation_id: str | None = None
    category: Literal['all', 'preserved', 'changed'] = 'all'

    @field_validator('path')
    @classmethod
    def relative_path(cls, value):
        value = value.replace('\\', '/')
        path = PurePosixPath(value)
        if path.is_absolute() or '..' in path.parts or ':' in value or not value:
            raise ModelValidationError(('path',), 'unsafe_relative_path',
                '选择范围必须是安全相对路径；当前所选根目录用 .，子目录用相对路径。不得使用绝对路径、空串、冒号或 .. 路径段；不要通过切换来源或扩大选择绕过校验。')
        return path.as_posix()

    @model_validator(mode='after')
    def provenance(self):
        if self.category != 'all' and not self.from_operation_id:
            raise ValueError('按旧批次类别选择需要 from_operation_id')
        return self

    def matches(self, relative):
        path = PurePosixPath(relative)
        if self.path != '.' and not (path == PurePosixPath(self.path) or PurePosixPath(self.path) in path.parents):
            return False
        names = [relative, path.name]
        ancestors = [relative, *[p.as_posix() for p in path.parents if p.as_posix() != '.']]
        return (not self.include or any(fnmatch.fnmatchcase(v, p) for p in self.include for v in names)) and not any(
            fnmatch.fnmatchcase(v, p) for p in self.exclude for v in ancestors + [path.name])


def provenance_paths(service, scope, selection):
    if not selection.from_operation_id:
        return None
    if scope['source_scope'] != 'output':
        raise ValueError('已生成结果的选择必须来自当前输出目录')
    task = service._raw(selection.from_operation_id, 'state')
    service._owner(task, scope['conversation_id'])
    if task['status'] != 'succeeded' or task['scope'].get('workspace_id') != scope.get('workspace_id'):
        raise ValueError('来源批次必须已完成且属于当前输出目录')
    source, _ = service._roots(scope)
    if str(source) != task['target_root']:
        raise ValueError('来源批次的输出目录与当前范围不一致')
    paths = set()
    for row in service.repository.items(task['id']):
        if row['action'] == 'delete':
            continue
        preserved = row['action'] == 'copy' and row['path'] == row.get('destination')
        if selection.category == 'preserved' and not preserved:
            continue
        if selection.category == 'changed' and preserved:
            continue
        paths.add(row.get('destination') or row['path'])
    return paths


def selected_inventory(service, scope, selection, *, follow_links=False):
    inventory = service.inventory(scope, selection.path, follow_links=follow_links)
    paths = provenance_paths(service, scope, selection)
    selected = [r for r in inventory['items'] if selection.matches(r['path']) and (paths is None or r['path'] in paths)]
    return {**inventory, 'items': selected, 'candidate_file_count': inventory['file_count'],
            'file_count': len(selected), 'total_bytes': sum(r['size'] for r in selected),
            'selection': selection.model_dump()}
