"""Literal filename transformations over a full bounded selection, preview only."""
from pathlib import PurePosixPath

from .data_models import DataEdit, GeneratedDataPlan
from .data_organization import suffix_and_stem
from .data_selection import selected_inventory
from .operation_context import checkpoint


def transform(service, request):
    scope = {k: getattr(request, k) for k in ('conversation_id', 'dataset_id', 'workspace_id', 'source_scope', 'mode')}
    inventory = selected_inventory(service, scope, request.selection)
    edits = []
    for row in inventory['items']:
        checkpoint()
        relative = row['path']
        suffix, stem = suffix_and_stem(relative)
        name = stem if request.match_on == 'stem' else PurePosixPath(relative).name
        for change in request.transforms:
            if change.action == 'replace':
                name = name.replace(change.old, change.new)
            elif change.action == 'strip_prefix' and name.startswith(change.old):
                name = name[len(change.old):]
            elif change.action == 'strip_suffix' and name.endswith(change.old):
                name = name[:-len(change.old)]
        if not name or name in {'.', '..'} or any(c in name for c in '/\\:\x00'):
            raise ValueError('变换产生无效文件名：' + relative)
        destination = (PurePosixPath(relative).parent / (name + suffix if request.match_on == 'stem' else name)).as_posix()
        if destination != relative:
            edits.append(DataEdit(action='rename' if request.mode == 'inplace' else 'copy', path=relative, destination=destination))
    if not inventory['items']:
        raise ValueError('当前选择没有可处理文件；请检查实际目录和来源批次')
    source, target = service._roots(scope)
    task = (service._propose(GeneratedDataPlan(**scope, title=request.title, edits=edits), ready=False, status='preparing') if edits
            else service._record(scope, source, target, request.title, [], [], ready=False, status='preparing'))
    task.update(transform={'selection': request.selection.model_dump(), 'match_on': request.match_on,
                           'transforms': [t.model_dump() for t in request.transforms]},
                scan={k: v for k, v in inventory.items() if k != 'items'})
    task['scan'].update(matched_files=len(inventory['items']), unmatched_files=0,
                        unchanged_files=len(inventory['items']) - len(edits))
    task.update(status='pending' if edits else 'succeeded', manifest_ready=True,
                quality={'complete': not task['conflicts'], 'blocking_issues': ['conflicts'] if task['conflicts'] else []})
    service._save(task)
    return service._finish_request(task, request.execution)
