"""Complete, per-turn query snapshots and bounded, reachable model pages."""
import json
import time
from uuid import uuid4

from .data_auth import principal
from .data_organization import coverage_groups
from .data_selection import DataSelection, provenance_paths
from .query_index import query_index


def bounded_rows(rows, offset, limit, budget=10000):
    page, used = [], 0
    for row in rows[offset:offset+limit]:
        size = len(json.dumps(row, ensure_ascii=False)) + 2
        if page and used + size > budget:
            break
        page.append(row); used += size
    next_offset = offset + len(page)
    return page, next_offset if next_offset < len(rows) else None


def inventory_query(service, request, scope, arguments):
    source, _ = service._roots(scope)  # Recheck permissions/bound roots on every query.
    selection = DataSelection.model_validate(arguments.get('selection', request.assistant_task.get('step', {}).get('selection', {})))
    if 'path' in arguments:
        selection = DataSelection.model_validate({**selection.model_dump(), 'path': arguments['path']})
    offset, limit = arguments.get('offset', 0), arguments.get('limit', 30)
    group_offset = arguments.get('group_offset', 0)
    view = arguments.get('view', 'files')
    if (type(offset) is not int or offset < 0 or type(group_offset) is not int or group_offset < 0
            or type(limit) is not int or not 1 <= limit <= 100 or view not in {'summary','files'}):
        raise ValueError('需要 view=summary/files、非负 offset/group_offset，以及 1–100 的 limit')
    follow_links = arguments.get('follow_links', False)
    if type(follow_links) is not bool:
        raise ValueError('follow_links 必须是布尔值，仅控制本次只读查询')
    key = json.dumps([principal.get().name, scope, str(source), selection.model_dump(), follow_links], sort_keys=True)
    cache = request._inventory_snapshots
    snapshot = cache.get(key)
    identity = arguments.get('inventory_id')
    if identity and (not snapshot or snapshot['id'] != identity):
        raise ValueError('查询清单不属于当前选择范围；请移除 inventory_id 使用当前范围')
    cached = bool(snapshot and snapshot['statistics'].get('complete_scan') and time.monotonic() - snapshot['started'] <= 900)
    if cached and service._query_indexes.mutation_version(source) != snapshot['mutation_version']:
        cached = False
    if not cached:
        index = query_index(service, scope, selection.path, follow_links=follow_links,
                            refresh=arguments.get('refresh', False))
        if identity and snapshot.get('index_id') != index['id']:
            raise ValueError('清单对应的数据已变化；请移除inventory_id从当前范围重新查询')
        permitted = provenance_paths(service, scope, selection)
        rows = [r for r in index['rows'] if selection.matches(r['path']) and (permitted is None or r['path'] in permitted)]
        result = {**index['report'], 'complete_scan': index['complete'],
                  'file_count': len(rows), 'total_bytes': sum(r['size'] for r in rows),
                  'candidate_file_count': len(index['rows']), 'selection': selection.model_dump(),
                  'index_id': index['id'], 'index_reused': index['index_reused'],
                  'index_status': 'complete' if index['complete'] else 'indexing' if index['scanned'] < 100000 else 'limit_reached',
                  'scanned_entries': index['scanned'], 'snapshot_observed_at': index['observed_at']}
        all_groups = coverage_groups(rows, limit=len(rows))
        snapshot = {'id': snapshot['id'] if snapshot and snapshot.get('index_id') == index['id'] else uuid4().hex,
                    'index_id': index['id'], 'mutation_version':index['mutation_version'], 'started':time.monotonic(),'observed_at':index['observed_at'],
                    'rows':rows,'groups':all_groups,'statistics':{k:v for k,v in result.items() if k != 'items'}}
        if key not in cache and len(cache) >= 16:
            cache.pop(next(iter(cache)))
        cache[key] = snapshot
    statistics = {**snapshot['statistics'], 'index_reused': cached or snapshot['statistics']['index_reused']}
    coverage = {k:v for k,v in snapshot['groups'].items() if k != 'groups'}
    groups, next_group = bounded_rows(snapshot['groups']['groups'], group_offset, min(limit,12), budget=10000)
    coverage.update(groups=groups,group_offset=group_offset,groups_truncated=len(groups)<coverage['group_count'],
                    complete_scan=statistics['complete_scan'],
                    next_group_offset=next_group,summarized_file_count=sum(g['count'] for g in groups))
    rows = snapshot['rows']
    if view == 'summary':
        by_path = {r['path']:r for r in rows}
        page, _ = bounded_rows([by_path[g['examples'][0]] for g in groups], 0, len(groups))
        next_offset = None
    else:
        page, next_offset = bounded_rows(rows, offset, limit)
    page = [{k:v for k,v in row.items() if k != 'revision'} for row in page]
    return {**statistics,'source_scope':scope['source_scope'],'root_path':str(source),'coverage':coverage,
            'inventory_id':snapshot['id'],'inventory_cached':cached,'snapshot_observed_at':snapshot['observed_at'],
            'view':view,'items':page,'offset':offset,'page_complete':statistics['complete_scan'] and (len(page)==len(rows) if view=='summary' else next_offset is None),
            'next_offset':next_offset,'returned_count':len(page),
            'files_query':{'source_scope':scope['source_scope'],'selection':selection.model_dump(),
                           'inventory_id':snapshot['id'],'view':'files','offset':0,'limit':30,'follow_links':follow_links},
            'next_step': ('索引尚未完成，保持同范围继续查询会从检查点续扫；部分结果不能证明文件不存在。' if not statistics['complete_scan']
                          else '使用 query_data_files 按名称模式统计并按对象检查缺失；搜索复用索引，无需重新遍历目录。'),
            'freshness_note':'这是有时限的只读索引快照；complete_scan 表示普通文件遍历完成，scope_complete 和排除报告说明范围。统计不是当前文件版本或执行授权；准备及运行会独立校验实际输入。'}
