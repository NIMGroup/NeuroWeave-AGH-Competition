"""One conversation fact set, partitioned by owner; raw evidence is never a prompt."""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from uuid import uuid4

from .agent_identity import query_signature, is_read
from .fact_memory import TypedFactMemory, digest, stable, fact_type
from .evidence_fields import read_field


def packed(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def responsibility(name):
    if name == 'read_method_routes':
        return 'methods'
    if name in {'read_conversation_memory', 'search_conversation_history', 'read_task_artifact'}:
        return 'coordinator'
    if 'skill' in name or name == 'list_workflow_artifact_types':
        return 'methods'
    if name in {'list_data_environments', 'read_execution_configuration', 'get_workflow_result', 'read_data_result', 'get_data_operation',
                'list_data_operations', 'list_data_operation_files', 'inspect_data_coverage'}:
        return 'runtime'
    return 'data'


def essential_result(name, result):
    """Typed views omit optional bulk rows, never method ports or parameter constraints."""
    if name == 'read_skill_method' and 'input_slots' in result:
        keys = ('method_id', 'skill_id', 'capability', 'definition_revision', 'definition_id',
                'definition_version', 'planning_capability_ids', 'label', 'description', 'input_slots',
                'output_slots', 'essential_parameters', 'parameters', 'parameter_count', 'input_count', 'output_count', 'section',
                'execution_mode', 'runtime_status')
        keys = (*keys,'catalog_ref','output_refs')
        return {key: result[key] for key in keys if key in result}
    value = {key: item for key, item in result.items() if key not in {'items', 'content', 'text', 'coverage'}}
    if isinstance(result.get('coverage'), dict):
        value['coverage'] = {key: item for key, item in result['coverage'].items() if key != 'groups'}
        value['coverage']['group_examples'] = result['coverage'].get('groups', [])[:3]
    if isinstance(result.get('items'), list):
        value['sample_items'] = result['items'][:3]
        value['stored_item_count'] = len(result['items'])
        value['sample_only'] = len(result['items']) > 3
    for field in ('content', 'text'):
        if field in result:
            value[field + '_available'] = True
            value[field + '_chars'] = len(result[field])
    return value


class SharedFacts:
    def __init__(self, memory):
        self.memory, self.store = memory, memory.store
        with self.store.transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS assistant_facts(
                conversation_id TEXT, actor TEXT, scope TEXT, fact_id TEXT, owner TEXT,
                payload TEXT, PRIMARY KEY(conversation_id,actor,scope,fact_id),
                FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE)''')
        self.typed = TypedFactMemory(self.store, self.memory)
        self._hydrated = set()

    @staticmethod
    def key(name, arguments):
        args = dict(arguments)
        for key in ('need_id', 'refresh'):
            args.pop(key, None)
        if name == 'read_skill_method':
            args.setdefault('section', 'contract')
            if args['section'] == 'contract':
                for key in ('offset', 'limit', 'cursor'):
                    args.pop(key, None)
        if name == 'inventory_data_files' and args.get('view', 'files') == 'summary':
            for key in ('group_offset', 'limit', 'inventory_id', 'offset'):
                args.pop(key, None)
            args.setdefault('path', '.')
        return 'fact:' + hashlib.sha256(packed(query_signature(name, args)).encode()).hexdigest()[:24]

    def rows(self, request, owner=None):
        identity = self.memory.identity(request)
        with self.store.connection() as db:
            rows = db.execute('SELECT payload FROM assistant_facts WHERE conversation_id=? AND actor=? AND scope=?'
                              + (' AND owner=?' if owner else ''), (*identity, *([owner] if owner else []))).fetchall()
        return sorted((json.loads(row[0]) for row in rows), key=lambda row: row['observed_at'])

    def stamp(self, owner, request, name, arguments, result=None):
        role = responsibility(name)
        if role == 'runtime':
            from .agent_execution_config import configuration_stamp
            return configuration_stamp(owner, name, arguments)  # Live state has no cache stamp.
        if role == 'methods':
            registry = getattr(getattr(owner, 'skill_catalog', None), 'registry', None)
            if registry and name == 'read_skill_method' and hasattr(registry, 'method_for'):
                method = registry.method_for('method', arguments.get('method_id', ''))
                if method is not None:
                    return digest(method.model_dump(mode='json'))
            if registry and hasattr(registry, 'all_methods'):
                definitions = [m.model_dump(mode='json') for m in registry.all_methods()]
                return digest(['port-goal-candidates-v2', definitions]) if name == 'list_skill_methods' else hashlib.sha256(packed(definitions).encode()).hexdigest()
            return os.getenv('NEUROWEAVE_RELEASE_REVISION', 'development')
        service = getattr(owner, 'data_operations', None)
        if not service:
            return None
        source_scope = arguments.get('source_scope') or ('dataset' if '_dataset_' in name else 'output')
        scope = {'conversation_id': request.conversation_id, 'dataset_id': request.dataset_id,
                 'workspace_id': request.workspace_id, 'source_scope': source_scope, 'mode': 'inplace'}
        source, _ = service._roots(scope)
        selection = arguments.get('selection', {})
        relative = selection.get('path', '.') if isinstance(selection, dict) else '.'
        relative = arguments.get('path', relative)
        # Resolving a selected path is still done by the actual tool. This stamp
        # cannot authorize a path, and only observes paths below the granted root.
        path = Path(source) / relative
        if path.is_absolute() and path.is_relative_to(Path(source)) and '..' not in path.parts:
            try:
                stat = path.lstat()
                revision = [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns]
            except OSError:
                revision = None
        else:
            return None
        indexes = getattr(service, '_query_indexes', None)
        if indexes is None and hasattr(service, 'query_index_lock'):
            from .query_index import QueryIndexes
            with service.query_index_lock:
                indexes = getattr(service, '_query_indexes', None)
                if indexes is None:
                    indexes = service._query_indexes = QueryIndexes(service)
        mutations = indexes.mutation_version(source) if indexes else None
        if name == 'inspect_data_sequences':
            if result is None:
                row = next((r for r in self.rows(request) if r['id'] == self.key(name, arguments)), None)
                if row:
                    try:
                        result = self.raw_result(request, row)[0]
                    except ValueError:
                        result = None
            from .data_sequences import file_version
            files = {}
            for relative in (result or {}).get('source_file_versions', {}):
                child = Path(source) / relative
                if not child.is_relative_to(Path(source)) or '..' in child.parts:
                    return None
                files[relative] = file_version(child)
            if result is not None and files != result.get('source_file_versions', {}):
                return None  # Never cache a read whose files changed during observation.
            return packed([str(source), revision, mutations, files])
        return packed([str(source), revision, mutations])

    def lookup(self, owner, request, name, arguments):
        if name == 'read_method_routes':
            return None  # The planner owns resume/cache identity including data revisions.
        fact_id = self.key(name, arguments)
        row = next((r for r in self.rows(request) if r['id'] == fact_id), None)
        if not row or row['summary'].get('complete_scan') is False or row['summary'].get('index_status') == 'indexing':
            return None
        if name == 'inventory_data_files' and arguments.get('view') == 'summary' and self.page_key(arguments) not in row.get('pages', {}):
            return None
        if responsibility(name) == 'data' and time.time() - row['observed_at'] >= 900:
            return None
        stamp = self.stamp(owner, request, name, arguments)
        if stamp is None or stamp != row.get('source_version'):
            return None
        return row

    def record(self, request, role, name, arguments, result, reference, source_version):
        if not reference or 'error' in result:
            return None
        fact_id = self.key(name, arguments)
        previous = next((r for r in self.rows(request, role) if r['id'] == fact_id), {})
        fingerprint = digest(stable(result))
        row = {'id': fact_id, 'owner': role, 'tool': name, 'arguments': arguments,
               'reference': reference, 'source_version': source_version, 'observed_at': time.time(),
               'source_sequence': request._user_sequence, 'fact_type': fact_type(name, arguments), 'content_hash': fingerprint,
               'summary': previous['summary'] if previous.get('content_hash') == fingerprint else essential_result(name, result)}
        row['available_fields'] = list(result)
        if previous.get('imported_ids'):
            row['imported_ids'] = previous['imported_ids']
        if name == 'inventory_data_files' and arguments.get('view') == 'summary':
            same_snapshot = (bool(result.get('index_id')) and previous.get('summary', {}).get('index_id') == result.get('index_id')
                             and previous.get('summary', {}).get('file_count') == result.get('file_count')
                             and previous.get('summary', {}).get('complete_scan') == result.get('complete_scan'))
            pages = dict(previous.get('pages', {})) if same_snapshot else {}
            coverage = result.get('coverage', {})
            offset = coverage.get('group_offset', 0)
            count = len(coverage.get('groups', []))
            pages[self.page_key(arguments)] = {'reference': reference, 'offset': offset, 'count': count,
                                              'next_offset': coverage.get('next_group_offset')}
            row['pages'] = pages
            intervals = {i for p in pages.values() for i in range(p['offset'], p['offset']+p['count'])}
            total = coverage.get('group_count', 0)
            row['summary']['coverage'].update(stored_group_count=len(intervals),
                groups_available_complete=bool(result.get('complete_scan')) and len(intervals) == total,
                next_missing_group_offset=next((i for i in range(total) if i not in intervals), None))
        with self.store.transaction() as db:
            db.execute('INSERT OR REPLACE INTO assistant_facts VALUES(?,?,?,?,?,?)',
                       (*self.memory.identity(request), fact_id, role, packed(row)))
        self.typed.merge(request, row, result)
        return row

    @staticmethod
    def page_key(arguments):
        return str(arguments.get('group_offset', 0)) + ':' + str(min(arguments.get('limit', 30), 12))

    def declare(self, request, declaration):
        reference = declaration['reference']
        if not reference.startswith('message:') or not reference[8:].isdigit():
            raise ValueError('输入声明必须引用当前会话的用户消息')
        with self.store.connection() as db:
            message = db.execute('SELECT role,content FROM conversation_messages WHERE conversation_id=? AND sequence=?',
                (request.conversation_id, int(reference[8:]))).fetchone()
        if not message or message['role'] != 'user' or declaration['quote'] not in message['content']:
            raise ValueError('输入声明需要逐字引用用户原话')
        if declaration.get('path'):
            from .data_selection import DataSelection
            if declaration['path'] not in declaration['quote']:
                raise ValueError('声明路径必须出现在引用的用户原话中')
            declaration['path'] = DataSelection(path=declaration['path']).path
        # Latest assertion for this role replaces the old assertion, inside the
        # same scoped fact set. It never becomes a file observation or a grant.
        args = {'source_scope':'dataset', 'artifact_type':declaration['artifact_type'],
                'slot_id':declaration.get('slot_id','')}
        result = {**declaration, 'source_scope':'dataset', 'dataset_id':request.dataset_id,
                  'workspace_id':request.workspace_id, 'provenance':'user_declared',
                  'validation':'pending_dry_run'}
        fact_id = self.key('user_declaration', args)
        previous = next((r for r in self.rows(request) if r['id']==fact_id), None)
        if previous and previous.get('source_sequence', 0) > int(reference[8:]):
            return previous
        evidence = self.memory.remember_evidence(request, 'user_declaration', args, result)
        return self.record(request, 'data', 'user_declaration', args, result, evidence, digest(result))

    def declarations(self, request):
        return [r for r in self.rows(request, 'data') if r['tool']=='user_declaration'
                and r['summary'].get('dataset_id')==request.dataset_id]

    def resolve_reference(self, request, reference):
        """One resolver for fact IDs, raw evidence and exact conversation messages."""
        if not isinstance(reference, str):
            raise ValueError('事实引用须为 fact:<ID>、evidence:<ID> 或 message:<序号>')
        if reference.startswith('fact:'):
            row = next((r for r in self.rows(request) if r['id'] == reference), None)
            if not row:
                raise ValueError('当前会话和授权范围内不存在该 fact 引用；请使用 read_shared_facts 返回的实际 ID')
            reference = row['reference']
        self.memory.execute(request, 'read_conversation_memory', {'reference': reference, 'max_chars': 1})
        return reference

    def raw_result(self, request, row, arguments=None):
        reference = row['reference']
        if row.get('pages') and arguments is not None:
            reference = row['pages'][self.page_key(arguments)]['reference']
        with self.store.connection() as db:
            evidence = db.execute('SELECT payload FROM assistant_evidence WHERE id=? AND conversation_id=? AND actor=? AND scope=?',
                                 (reference, *self.memory.identity(request))).fetchone()
        if not evidence:
            raise ValueError('事实原始引用不存在')
        return json.loads(evidence[0])['result'], reference

    def import_history(self, owner, request):
        """Promote scoped read evidence from the previous chat path, without inventing freshness."""
        identity = self.memory.identity(request)
        with self.store.connection() as db:
            records = db.execute('SELECT * FROM assistant_evidence WHERE conversation_id=? AND actor=? ORDER BY created_at', identity[:2]).fetchall()
        imported = {p['reference'] for r in self.rows(request) for p in r.get('pages', {}).values()}
        imported.update(r['reference'] for r in self.rows(request))
        imported.update(ref for r in self.rows(request) for ref in r.get('imported_ids', []))
        for saved in records:
            if saved['id'] in imported:
                continue
            evidence = json.loads(saved['payload'])
            name, args, result = evidence['tool'], evidence['arguments'], evidence['result']
            if not is_read(name) or name in {'read_conversation_memory', 'search_conversation_history'} or 'error' in result:
                continue
            if (evidence.get('source_sequence') or 0) > (request._user_sequence or 0):
                continue
            reference = saved['id']
            if saved['scope'] != identity[2]:
                # Old scope hashes cannot be trusted. Rebind only explicit root facts.
                try:
                    source, _ = owner.data_operations._roots({'conversation_id': request.conversation_id,
                        'dataset_id': request.dataset_id, 'workspace_id': request.workspace_id,
                        'source_scope': args.get('source_scope') or ('dataset' if '_dataset_' in name else 'output'), 'mode': 'inplace'})
                except (ValueError, KeyError, PermissionError):
                    continue
                if not result.get('root_path') or Path(result['root_path']) != Path(source):
                    continue
                # Already migrated evidence is recorded as a single alias.
                existing = next((r for r in self.rows(request) if reference in r.get('imported_ids', [])), None)
                if existing:
                    continue
                reference = 'evidence:' + uuid4().hex
                with self.store.transaction() as db:
                    db.execute('INSERT INTO assistant_evidence VALUES(?,?,?,?,?,?)',
                               (reference, *identity, saved['payload'], saved['created_at']))
            previous = next((r for r in self.rows(request) if r['id'] == self.key(name, args)), None)
            if previous and previous['observed_at'] > evidence['observed_at']:
                same_index = previous.get('pages') and previous['summary'].get('index_id') == result.get('index_id')
                if not same_index:
                    continue
                if self.page_key(args) in previous['pages']:
                    continue  # A current page must not be replaced by older evidence.
            version = previous.get('source_version') if previous and previous['summary'].get('index_id') == result.get('index_id') else None
            row = self.record(request, responsibility(name), name, args, result, reference, version)
            row['observed_at'] = max(evidence['observed_at'], (previous or {}).get('observed_at', 0))
            row['source_sequence'] = evidence.get('source_sequence')
            row['imported_ids'] = list(dict.fromkeys([*(previous or {}).get('imported_ids', []), saved['id'], reference]))
            with self.store.transaction() as db:
                db.execute('UPDATE assistant_facts SET payload=? WHERE conversation_id=? AND actor=? AND scope=? AND fact_id=?',
                           (packed(row), *identity, row['id']))

    @staticmethod
    def view(row):
        return {**{key: row[key] for key in ('id', 'owner', 'tool', 'arguments', 'reference', 'observed_at', 'source_version', 'summary')},
                'freshness': 'historical_needs_validation' if row.get('source_version') is None else 'bounded_snapshot',
                'available_fields': row.get('available_fields', []),
                **({'stored_pages': len(row['pages']), 'detail_field': 'coverage.groups'} if row.get('pages') else {})}

    def context(self, request, owner=None, budget=24000, *, offset=0, limit=50):
        rows = self.rows(request, owner)
        scope = self.memory.identity(request)
        from .fact_memory import identity as entity_identity
        current_entities = {entity_identity(row)[1] for row in self.rows(request)}
        if scope not in self._hydrated:
            represented = {ref for e in self.typed.rows(request) if e['entity_id'] in current_entities for ref in e['fact_ids']}
            for row in self.rows(request):
                if row['id'] not in represented or row['tool'] == 'list_workflow_artifact_types':
                    try:
                        self.typed.merge(request, row, self.raw_result(request, row)[0])
                    except (ValueError, KeyError):
                        pass
            self._hydrated.add(scope)
        selected = set(request.assistant_task.get('method_selection', {}).get('method_ids', []))
        # A fresh auxiliary contract must not displace the selected plan or all
        # data evidence. Entities merge overlap; raw query references stay paged.
        ordered = sorted(rows, key=lambda r: (r['summary'].get('method_id') not in selected, -r['observed_at']))
        page = ordered[offset:offset+limit]
        manifest = [{key: row[key] for key in ('id', 'owner', 'tool')} | (
            {'method_id': row['summary']['method_id']} if row['summary'].get('method_id') else {}) for row in page]
        value = {'manifest': manifest, 'facts': [], 'omitted_fact_ids': [r['id'] for r in page],
                 'total_fact_count': len(rows), 'offset': offset,
                 'next_offset': offset+len(page) if offset+len(page)<len(rows) else None,
                 'notice': '同一会话的后端事实；按 owner 分工。sample_items 仅是样例，未完成扫描不能判定缺失。事实不是执行授权。'}
        ids = {r['id'] for r in page}
        entities = [e for e in self.typed.rows(request, owner) if e['entity_id'] in current_entities and ids.intersection(e['fact_ids'])]
        entities.sort(key=lambda e: (e['content'].get('result', {}).get('method_id') not in selected,
                                    e['fact_type'] == 'methods.candidates', -e['observed_at']))
        queues = {role: [e for e in entities if e['owner'] == role] for role in ('data', 'methods', 'runtime', 'coordinator')}
        balanced = []
        while any(queues.values()):
            for queue in queues.values():
                if queue:
                    balanced.append(queue.pop(0))
        for entity in balanced:
            fact = self.typed.view(entity)
            candidate = {**value, 'facts': [*value['facts'], fact],
                'omitted_fact_ids': [ref for ref in value['omitted_fact_ids'] if ref not in entity['fact_ids']]}
            if len(packed(candidate)) <= budget:
                value = candidate
        return value

    def read(self, request, arguments):
        if arguments.get('entity_id'):
            entity = self.typed.read(request, arguments['entity_id'])
            field = arguments.get('field')
            if field:
                content = entity['content']
                if field not in content:
                    return {'entity_id': entity['entity_id'], 'status': 'field_not_found',
                            'available_fields': list(content), 'summary': entity['summary']['structured'],
                            'next_step': '按available_fields读取已保存内容，不重新查询来源。'}
                value = content[field]
                if isinstance(value, dict) and field in {'items', 'candidates', 'series', 'coverage_groups'}:
                    offset, limit = arguments.get('offset', 0), arguments.get('limit', 10)
                    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
                        raise ValueError('事实分页参数无效')
                    items = list(value.values())
                    return {'entity_id': entity['entity_id'], 'fact_ids': entity['fact_ids'], 'items': items[offset:offset+limit],
                            'total': len(items), 'next_offset': offset+limit if offset+limit < len(items) else None}
                return {'entity_id': entity['entity_id'], 'fact_ids': entity['fact_ids'], 'value': value}
            return self.typed.view(entity)
        fact_id = arguments.get('fact_id')
        owner = arguments.get('owner')
        if owner not in {None, 'data', 'methods', 'runtime'}:
            raise ValueError('未知事实归属')
        rows = self.rows(request, owner)
        offset, limit = arguments.get('offset', 0), arguments.get('limit', 10 if fact_id else 50)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('事实分页参数无效')
        if not fact_id:
            return self.context(request, owner, offset=offset, limit=limit)
        row = next((r for r in rows if r['id'] == fact_id), None)
        if not row:
            raise ValueError('当前会话和授权范围内不存在该事实')
        field = arguments.get('field')
        if not field:
            return {**self.view(row), 'available_fields': list(self.raw_result(request, row)[0])}
        if field == 'coverage.groups' and row.get('pages'):
            groups = {}
            for page in row['pages'].values():
                result, _ = self.raw_result(request, {**row, 'reference': page['reference']})
                for index, group in enumerate(result.get('coverage', {}).get('groups', []), page['offset']):
                    groups[index] = group
            items = [groups[i] for i in sorted(groups)]
            return {'fact_id': fact_id, 'field': field, 'items': items[offset:offset+limit],
                    'total_stored': len(items), 'total': row['summary']['coverage']['group_count'],
                    'coverage_complete': row['summary']['coverage']['groups_available_complete'],
                    'next_missing_group_offset': row['summary']['coverage']['next_missing_group_offset'],
                    'next_offset': offset+limit if offset+limit < len(items) else None}
        result, _ = self.raw_result(request, row)
        try:
            value = read_field(result, field)
        except (KeyError, IndexError, ValueError, TypeError):
            return {'fact_id': fact_id, 'field': field, 'status': 'field_not_found',
                    'available_fields': list(result), 'summary': row['summary'],
                    'next_step': '按available_fields读取已保存字段；键中含点号时使用 JSON Pointer，不重新查询来源。'}
        if isinstance(value, list):
            return {'fact_id': fact_id, 'field': field, 'items': value[offset:offset+limit],
                    'total': len(value), 'next_offset': offset+limit if offset+limit < len(value) else None}
        if isinstance(value, str):
            # Text is explicitly paged; never stringify and cut structured JSON.
            chars = min(12000, limit * 200)
            return {'fact_id': fact_id, 'field': field, 'text': value[offset:offset+chars],
                    'total_chars': len(value), 'next_offset': offset+chars if offset+chars < len(value) else None}
        return {'fact_id': fact_id, 'field': field, 'value': value}


FACT_TOOL = {'type': 'function', 'function': {'name': 'read_shared_facts',
    'description': '读取同一会话事实库。省略 fact_id 按 owner 查看索引；指定 ID 读完整必要事实，field 可分页读取原始字段。先读已保存事实，再补查具体缺口。',
    'parameters': {'type': 'object', 'properties': {'owner': {'type': 'string', 'enum': ['data', 'methods', 'runtime']},
        'fact_id': {'type': 'string'}, 'entity_id': {'type': 'string', 'description': '类型合并事实的实际 entity_id；只读available_fields已列出的字段。'},
        'field': {'type': 'string', 'description': '省略先看摘要及available_fields；仅使用返回的字段名，支持coverage.groups等字典路径。'}, 'offset': {'type': 'integer', 'minimum': 0},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}}, 'additionalProperties': False}}}
