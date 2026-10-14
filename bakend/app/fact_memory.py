"""Typed, merged projections of sourced facts; cached summaries are never evidence.

Query records retain exact raw references. Entities are derived from those records,
inside the same conversation/actor/scope, rather than another agent's truth store.
"""
from __future__ import annotations

import hashlib
import json


SUMMARY_POLICY = 'typed-facts-v1'
VOLATILE = {'observed_at', 'snapshot_observed_at', 'scanned_at', 'inventory_cached',
            'metadata_cached', 'index_reused', 'reused', 'duration_seconds',
            'inventory_id', 'index_id', 'source_file_versions', 'next_step', 'freshness_note'}
PAGING = {'cursor', 'offset', 'group_offset', 'limit', 'max_results', 'inventory_id', 'refresh', 'need_id'}


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def stable(value):
    if isinstance(value, dict):
        return {k: stable(v) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, list):
        return [stable(v) for v in value]
    return value


def fact_type(name, arguments=None):
    if name == 'user_declaration':
        return 'data.declarations'
    if name == 'read_method_routes':
        return 'methods.routes'
    if name == 'list_skill_methods':
        return 'methods.candidates'
    if name == 'read_skill_method':
        return 'methods.contract' if (arguments or {}).get('section', 'contract') in {'contract', 'all'} else 'methods.reference'
    if name == 'read_skill_pipeline':
        return 'methods.pipeline'
    if name == 'inspect_data_sequences':
        return 'data.sequences'
    if name == 'inspect_data_metadata':
        return 'data.metadata'
    if name.startswith('read_') and ('dataset' in name or 'output' in name):
        return 'data.content'
    if 'skill' in name or name == 'list_workflow_artifact_types':
        return 'methods.reference'
    if name == 'read_execution_configuration':
        return 'runtime.scientific_configuration'
    if name == 'list_data_environments':
        return 'runtime.script_configuration'
    if name in {'get_workflow_result', 'read_data_result', 'get_data_operation',
                'list_data_operations', 'list_data_operation_files', 'inspect_data_coverage'}:
        return 'runtime.results'
    return 'data.layout'


def identity(row):
    kind = fact_type(row['tool'], row['arguments'])
    args = {k: v for k, v in row['arguments'].items() if k not in PAGING}
    if kind == 'methods.candidates':
        args = {'kind': args.get('kind', 'method'), 'skill_id': args.get('skill_id', ''),
                'executable_only': args.get('executable_only', False), 'output_type': args.get('output_type', '')}
    if kind == 'methods.contract':
        args = {'method_id': args.get('method_id'), 'section': args.get('section', 'contract')}
    return kind, 'entity:' + digest([kind, row['tool'], args])[:24]


def short_candidate(item):
    keys = ('method_id', 'definition_id', 'entry_kind', 'skill_id', 'label', 'runner_id',
            'execution_mode', 'input_types', 'output_types', 'input_count', 'output_count', 'parameter_count')
    value = {k: item[k] for k in keys if k in item}
    value['description'] = item.get('description', '')[:220]
    value['description_available'] = item.get('description_available', False) or len(item.get('description', '')) > 220
    return value


def row_key(item):
    if not isinstance(item, dict):
        return digest(item)
    return str(item.get('path') or item.get('relative_path') or item.get('series_id') or item.get('id') or digest(item))


def artifact_catalog(result):
    """A type vocabulary is an exact catalog, never a representative sample."""
    return {**{k: v for k, v in result.items() if k != 'items'}, 'items': [
        {**{k: item[k] for k in ('id', 'label') if k in item},
         'description': item.get('description', '')[:160],
         'description_available': len(item.get('description', '')) > 160}
        for item in result.get('items', [])]}


def projection(kind, content):
    if content.get('catalog_kind') == 'artifact_types':
        return artifact_catalog({**content['result'], 'items': list(content.get('items', {}).values())})
    if kind == 'methods.candidates':
        candidates = content.get('candidates', {})
        return {'candidate_count': len(candidates), 'candidates': list(candidates.values())[:6],
                'sample_only': len(candidates) > 6,
                'notice': '候选仅是发现结果，不是已选步骤。完整候选按 entity_id 读取；选中方法需真实契约。'}
    result = content['result']
    if kind == 'methods.contract':
        # Required ports, conditional requirements, defaults and all constraints
        # remain exact. Prose lives in the source and can be summarized separately.
        keys = ('method_id', 'definition_revision', 'definition_id', 'definition_version',
                'planning_capability_ids', 'label', 'input_slots', 'output_slots',
                'essential_parameters', 'parameters', 'input_count', 'output_count', 'parameter_count', 'section')
        return {k: result[k] for k in keys if k in result} | {
            'description': result.get('description', '')[:240],
            'description_available': len(result.get('description', '')) > 240}
    if kind == 'methods.pipeline':
        # Keep the full typed graph. Parameter prose is descriptive, not a value
        # constraint; the original pipeline remains individually readable.
        def metadata(value):
            if not isinstance(value, dict):
                return value
            return {**value, **({'description': value['description'][:180]}
                if isinstance(value.get('description'), str) else {})}
        value = metadata(result)
        for field in ('parameters', 'input_slots', 'output_slots', 'steps'):
            if isinstance(value.get(field), list):
                value[field] = [metadata(v) for v in value[field]]
        return value  # Never descend into executable defaults, bindings or conditions.
    value = {k: v for k, v in result.items() if k not in {'items', 'content', 'text', 'coverage', 'series'}}
    if isinstance(result.get('coverage'), dict):
        value['coverage'] = {k: v for k, v in result['coverage'].items() if k != 'groups'}
        value['coverage']['group_examples'] = list(content.get('coverage_groups', {}).values())[:3]
    items = list(content.get('items', {}).values())
    if items:
        value.update(sample_items=items[:3], stored_item_count=len(items), sample_only=len(items) > 3)
    if kind == 'data.sequences':
        series = list(content.get('series', {}).values())
        value['series'] = series[:4]
        value['stored_series_count'] = len(series)
        value['series_sample_only'] = len(series) > 4
        value['stored_series_profiles_complete'] = result.get('complete_scan') is True and result.get('scope_complete') is True and len(series) == result.get('matched_series_count')
    for key in ('content', 'text'):
        if isinstance(result.get(key), str):
            value.update({key + '_available': True, key + '_chars': len(result[key]),
                          key + '_sha256': hashlib.sha256(result[key].encode()).hexdigest()})
    return value


class TypedFactMemory:
    def __init__(self, store, memory):
        self.store, self.memory = store, memory
        with store.transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS assistant_fact_entities(
                conversation_id TEXT, actor TEXT, scope TEXT, entity_id TEXT,
                payload TEXT, PRIMARY KEY(conversation_id,actor,scope,entity_id),
                FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE)''')
            db.execute('''CREATE TABLE IF NOT EXISTS assistant_fact_summaries(
                conversation_id TEXT, actor TEXT, scope TEXT, fact_type TEXT,
                content_hash TEXT, policy TEXT, payload TEXT,
                PRIMARY KEY(conversation_id,actor,scope,fact_type,content_hash,policy),
                FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE)''')

    def rows(self, request, owner=None):
        with self.store.connection() as db:
            rows = db.execute('SELECT payload FROM assistant_fact_entities WHERE conversation_id=? AND actor=? AND scope=?',
                              self.memory.identity(request)).fetchall()
        return [v for r in rows if (v := json.loads(r[0])) and (owner is None or v['owner'] == owner)]

    def merge(self, request, row, result):
        kind, key = identity(row)
        scope = self.memory.identity(request)
        with self.store.transaction() as db:
            saved = db.execute('SELECT payload FROM assistant_fact_entities WHERE conversation_id=? AND actor=? AND scope=? AND entity_id=?',
                               (*scope, key)).fetchone()
            previous = json.loads(saved[0]) if saved else {}
            same_snapshot = bool(previous) and previous.get('source_version') == row.get('source_version')
            if result.get('index_id'):
                same_snapshot = same_snapshot and previous.get('index_id') == result['index_id']
                if kind == 'data.sequences' and previous.get('index_id') == result['index_id']:
                    try:
                        # Different pages verify different files. Their root and
                        # mutation snapshot must agree; page file stamps needn't.
                        same_snapshot = json.loads(previous['source_version'])[:3] == json.loads(row['source_version'])[:3]
                    except (ValueError, TypeError, KeyError):
                        same_snapshot = False
            if kind == 'methods.candidates':
                candidates = dict(previous.get('content', {}).get('candidates', {})) if same_snapshot else {}
                candidates.update({v['method_id']: short_candidate(v) for v in result.get('items', []) if 'method_id' in v})
                content = {'candidates': dict(sorted(candidates.items()))}
            else:
                current = stable(result)
                items = dict(previous.get('content', {}).get('items', {})) if same_snapshot else {}
                # Only pages from an identified snapshot can be accumulated.
                if not result.get('index_id'):
                    items = {}
                items.update({row_key(v): stable(v) for v in result.get('items', [])})
                content = {'result': current, 'items': dict(sorted(items.items()))}
                if isinstance(current.get('coverage'), dict):
                    groups = dict(previous.get('content', {}).get('coverage_groups', {})) if same_snapshot and result.get('index_id') else {}
                    groups.update({row_key(v): stable(v) for v in current['coverage'].get('groups', [])})
                    content['coverage_groups'] = dict(sorted(groups.items()))
                    for field in ('groups', 'group_offset', 'next_group_offset', 'groups_truncated', 'summarized_file_count'):
                        current['coverage'].pop(field, None)
                if kind == 'data.sequences':
                    series = dict(previous.get('content', {}).get('series', {})) if same_snapshot else {}
                    series.update({v['series_id']: stable(v) for v in result.get('series', [])})
                    content['series'] = dict(sorted(series.items()))
                    content['result'].pop('series', None)
                    content['result'].pop('verified_series_count', None)
                for field in ('offset', 'returned_count', 'next_offset', 'next_cursor', 'group_offset',
                              'next_group_offset', 'has_more', 'page_complete', 'items'):
                    content['result'].pop(field, None)
                if row['tool'] == 'list_workflow_artifact_types':
                    content['catalog_kind'] = 'artifact_types'
                # Coverage and exceptions remain attached to their exact query;
                # counts from overlapping pages are never added.
            fingerprint = digest(content)
            cached = db.execute('SELECT payload FROM assistant_fact_summaries WHERE conversation_id=? AND actor=? AND scope=? AND fact_type=? AND content_hash=? AND policy=?',
                                (*scope, kind, fingerprint, SUMMARY_POLICY)).fetchone()
            summary = json.loads(cached[0]) if cached else {'structured': projection(kind, content)}
            if not cached:
                db.execute('INSERT INTO assistant_fact_summaries VALUES(?,?,?,?,?,?,?)',
                           (*scope, kind, fingerprint, SUMMARY_POLICY, encode(summary)))
            refs = list(dict.fromkeys([*(previous.get('fact_ids', []) if same_snapshot else []), row['id']]))
            entity = {'entity_id': key, 'fact_type': kind, 'owner': row['owner'], 'tool': row['tool'],
                      'arguments': row['arguments'], 'id': row['id'], 'fact_ids': refs,
                      'reference': row['reference'], 'source_version': row.get('source_version'),
                      'index_id': result.get('index_id'), 'content': content, 'content_hash': fingerprint,
                      'summary': summary, 'summary_policy': SUMMARY_POLICY,
                      'observed_at': row['observed_at'], 'summary_reused': bool(cached)}
            db.execute('INSERT OR REPLACE INTO assistant_fact_entities VALUES(?,?,?,?,?)', (*scope, key, encode(entity)))
        return entity

    @staticmethod
    def view(entity):
        return {k: entity[k] for k in ('id', 'entity_id', 'fact_type', 'owner', 'tool', 'content_hash')} | {
            'fact_ids': entity['fact_ids'][-4:], 'source_fact_count': len(entity['fact_ids']),
            'summary': entity['summary']['structured'],
            'available_fields': list(entity['content']),
            **({'interpretation': entity['summary']['notes']} if entity['summary'].get('notes') else {}),
            'freshness': 'historical_needs_validation' if entity.get('source_version') is None else 'bounded_snapshot'}

    def read(self, request, key):
        entity = next((r for r in self.rows(request) if r['entity_id'] == key), None)
        if entity is None:
            raise ValueError('当前会话和授权范围内不存在该类型事实')
        return entity

    def save_notes(self, request, entity, notes):
        # A completion based on an old version must not overwrite a newer fact.
        scope = self.memory.identity(request)
        with self.store.transaction() as db:
            saved = db.execute('SELECT payload FROM assistant_fact_entities WHERE conversation_id=? AND actor=? AND scope=? AND entity_id=?',
                               (*scope, entity['entity_id'])).fetchone()
            current = json.loads(saved[0]) if saved else None
            if not current or current['content_hash'] != entity['content_hash']:
                return False
            current['summary'] = {**current['summary'], 'notes': notes, 'notes_kind': 'model_interpretation'}
            db.execute('UPDATE assistant_fact_summaries SET payload=? WHERE conversation_id=? AND actor=? AND scope=? AND fact_type=? AND content_hash=? AND policy=?',
                       (encode(current['summary']), *scope, entity['fact_type'], entity['content_hash'], SUMMARY_POLICY))
            db.execute('UPDATE assistant_fact_entities SET payload=? WHERE conversation_id=? AND actor=? AND scope=? AND entity_id=?',
                       (encode(current), *scope, entity['entity_id']))
        return True
