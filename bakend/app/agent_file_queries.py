"""One index for filename queries and exact object/input coverage. No arbitrary code."""
import base64
from collections import Counter
import fnmatch
import hashlib
import json
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .assistant_inventory import inventory_query, bounded_rows
from .data_selection import DataSelection
from .operation_context import checkpoint


class FileQuery(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_scope: Literal['dataset', 'output']
    selection: DataSelection = Field(default_factory=DataSelection)
    patterns: list[str] = Field(default_factory=list, max_length=32, description='按文件名 glob 分别统计，一次可查多种文件。')
    required_patterns: dict[str, str] = Field(default_factory=dict, description='输入角色到名称或对象内相对路径 glob；例如 dwi/DTI.nii.gz 相对每个样本根，逐对象区分 found/unknown/missing/ambiguous。')
    group_depth: int = Field(default=1, ge=1, le=5, description='相对根目录前几层作为对象分组；例如1按第一层目录分组。')
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)
    follow_links: bool = False

    @field_validator('patterns', 'required_patterns')
    @classmethod
    def patterns_valid(cls, value):
        entries = list(value.values()) if isinstance(value, dict) else value
        if len(entries) > 32 or any(not p or len(p)>200 or '..' in PurePosixPath(p).parts or p.startswith('/') for p in entries):
            raise ValueError('最多32个安全的相对文件模式，每项1–200字符')
        if isinstance(value, dict) and any(not k or len(k)>80 for k in value):
            raise ValueError('输入角色名称必须为1–80字符')
        return value


FILE_QUERY_TOOL = {'type':'function','function':{'name':'query_data_files',
    'description':'只读查询复用文件索引：一次统计多个文件名模式，并按目录对象校验 required_patterns 对应输入的缺失/多重匹配。返回精确覆盖、异常及可用路径样例；不读JSON内容、不执行脚本。complete_scan=false时同范围继续会续扫，不能据部分扫描判定不存在。',
    'parameters':FileQuery.model_json_schema()}}


def rows_for(service, request, scope, selection, follow_links=False):
    inventory = inventory_query(service, request, scope, {'selection':selection.model_dump(), 'view':'summary', 'follow_links':follow_links})
    snapshot = next(v for v in request._inventory_snapshots.values() if v['id']==inventory['inventory_id'])
    return inventory, snapshot['rows']


def matches(row, pattern):
    return any(fnmatch.fnmatchcase(value.casefold(), pattern.casefold())
               for value in (row['path'], PurePosixPath(row['path']).name))


def query_files(service, request, scope, arguments):
    query = FileQuery.model_validate(arguments)
    inventory, rows = rows_for(service, request, scope, query.selection, query.follow_links)
    patterns = {p:{'count':0,'examples':[]} for p in query.patterns}
    objects = {}
    registered = []
    registered_complete = True
    if scope['source_scope'] == 'dataset' and query.group_depth == 1 and query.selection.path == '.':
        # Include registered samples with zero selected files, so a missing whole
        # subject cannot disappear from the denominator.
        dataset = service.workspace.store.get_dataset(scope['dataset_id'])
        registered = dataset.inspection.samples if dataset else []
        registered_complete = not dataset or dataset.inspection.sample_count == len(registered)
    sample_roots = sorted(((s.relative_root.strip('/'),s.sample_id) for s in registered), key=lambda v:len(v[0]), reverse=True)
    for _, identity in sample_roots:
        objects[identity] = {'id':identity,'file_count':0,'inputs':{role:{'count':0,'examples':[]} for role in query.required_patterns}}
    unassigned = 0
    for row in rows:
        checkpoint()
        object_root = next((root for root, _ in sample_roots if row['path'].startswith(root+'/')), None) if registered else '/'.join(PurePosixPath(row['path']).parts[:query.group_depth])
        object_relative = row['path'][len(object_root)+1:] if object_root else row['path']
        object_row = {**row, 'path': object_relative}
        for pattern, group in patterns.items():
            if matches(row, pattern) or matches(object_row, pattern):
                group['count'] += 1
                if len(group['examples']) < 2:
                    group['examples'].append(row['path'])
        parts = PurePosixPath(row['path']).parts
        identity = next((identity for root,identity in sample_roots if row['path'].startswith(root+'/') or row['path']==root),None) if registered else '/'.join(parts[:query.group_depth]) if len(parts)>query.group_depth else '.'
        if identity is None:
            unassigned += 1
            continue
        group = objects.setdefault(identity, {'id':identity,'file_count':0,'inputs':{
            role:{'count':0,'examples':[]} for role in query.required_patterns}})
        group['file_count'] += 1
        for role, pattern in query.required_patterns.items():
            if matches(row, pattern) or matches(object_row, pattern):
                item = group['inputs'][role]; item['count'] += 1
                if len(item['examples']) < 2:
                    item['examples'].append(row['path'])
    verified = bool(inventory['complete_scan'] and inventory['scope_complete'] and registered_complete)
    missing, unknown, ambiguous = Counter(), Counter(), Counter()
    ready, exceptions = 0, []
    for group in objects.values():
        absent = [k for k,v in group['inputs'].items() if not v['count']]
        multiple = [k for k,v in group['inputs'].items() if v['count']>1]
        (missing if verified else unknown).update(absent); ambiguous.update(multiple)
        for item in group['inputs'].values():
            item['status'] = 'ambiguous' if item['count'] > 1 else 'found' if item['count'] else 'missing' if verified else 'unknown'
        ready += bool(query.required_patterns) and not absent and not multiple
        if absent or multiple:
            exceptions.append({'id':group['id'],'missing':absent if verified else [],'unknown':[] if verified else absent,'ambiguous':multiple})
    page, next_offset = bounded_rows(sorted(objects.values(), key=lambda r:r['id']), query.offset, query.limit, budget=3500)
    report = {k:inventory[k] for k in ('source_scope','root_path','file_count','complete_scan','scope_complete',
              'skipped_symlinks','warnings','inventory_id','index_id','index_status','scanned_entries','snapshot_observed_at') if k in inventory}
    return {**report, 'selection':query.selection.model_dump(), 'pattern_counts':patterns,
            'object_count':len(objects),'ready_object_count':ready,'missing_counts':dict(missing),'unknown_counts':dict(unknown),
            'coverage_state':'not_evaluated' if not query.required_patterns else 'complete' if verified else 'partial',
            'ambiguous_counts':dict(ambiguous),'exceptions':exceptions[:12], 'exception_count':len(exceptions),
            'items':page,'offset':query.offset,'returned_count':len(page),'next_offset':next_offset,
            'has_more':next_offset is not None,'index_reused':inventory.get('index_reused',inventory['inventory_cached']),
            'coverage_complete': bool(inventory['complete_scan'] and inventory['scope_complete']),
            'input_coverage_complete':bool(query.required_patterns and verified and not missing and not ambiguous),
            'object_scope':'registered_samples' if registered else 'file_directories', 'unassigned_file_count':unassigned,
            'note':'统计覆盖普通文件索引；空目录不构成文件对象。排除、缺失、多重匹配必须处理，数量相同不证明输入配齐。路径样例可用于 prepare_workflow.input_bindings；后端会按全部已登记样本校验，不需要逐项列路径。'}


def indexed_search(service, reader, request, source_scope, arguments):
    args=dict(arguments)
    path=args.get('path','.')
    # Retain explicit path and link authorization before using a historical index.
    selection=DataSelection(path=path)
    scope={'conversation_id':request.conversation_id,'dataset_id':request.dataset_id,
           'workspace_id':request.workspace_id,'source_scope':source_scope,'mode':'inplace'}
    inventory, rows=rows_for(service,request,scope,selection,args.get('follow_links',False))
    pattern=args.get('pattern','*'); depth=args.get('max_depth',2); limit=args.get('max_results',50)
    if type(depth) is not int or not 0<=depth<=5 or type(limit) is not int or not 1<=limit<=200 or not isinstance(pattern,str) or len(pattern)>500:
        raise ValueError('搜索深度0–5、结果数1–200，模式必须是500字符以内的文本')
    prefix=PurePosixPath(selection.path)
    found=[]
    for row in rows:
        relative=PurePosixPath(row['path']).relative_to(prefix) if selection.path!='.' else PurePosixPath(row['path'])
        if len(relative.parts)-1<=depth and fnmatch.fnmatchcase(relative.name.casefold(),pattern.casefold()):
            found.append(row)
    signature=hashlib.sha256(json.dumps([inventory['index_id'],path,pattern.casefold(),depth,args.get('follow_links',False)]).encode()).hexdigest()
    offset=0
    if args.get('cursor'):
        try:
            cursor=json.loads(base64.urlsafe_b64decode(args['cursor']))
            if cursor['signature']!=signature or type(cursor['offset']) is not int or cursor['offset']<0:
                raise ValueError()
            offset=cursor['offset']
        except (ValueError,KeyError,TypeError):
            raise ValueError('搜索游标不属于当前索引和范围；移除cursor后按当前索引查询')
    found.sort(key=lambda r:r['path'])
    page=found[offset:offset+limit]
    items=[]
    for row in page:
        checkpoint()
        item=reader.inspect(row.get('resolved_path',row['path']))
        if row.get('resolved_path'):
            item.update(via_path=row['path'],writable=False)
        item['relative_path']=row['path']
        items.append(item)
    next_offset=offset+len(page)
    cursor=base64.urlsafe_b64encode(json.dumps({'signature':signature,'offset':next_offset}).encode()).decode() if next_offset<len(found) else None
    return {k:v for k,v in inventory.items() if k not in {'items','coverage','files_query'}} | {
        'view':'files','items':items,'offset':offset,'total_items':len(found),'returned_count':len(items),
        '_page_cursor':{'signature':signature},
        'has_more':bool(cursor),'next_cursor':cursor,'max_depth':depth,
        'truncated':bool(cursor) or not inventory['complete_scan'],
        'index_reused':inventory.get('index_reused',inventory['inventory_cached']),
        'query_complete':inventory['complete_scan'],'stop_reason':None if inventory['complete_scan'] else inventory.get('stop_reason'),
        'note':'搜索基于授权范围的只读索引，空结果只有query_complete=true时才证明索引内没有匹配。续页用next_cursor；索引未完成时同范围查询从扫描检查点继续。'}
