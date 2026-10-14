"""Compact task headers, immutable manifests and durable per-file journals.

Legacy payloads remain byte-for-byte untouched until an explicit mutation.
Each file transition commits only its own journal entry and a small header.
"""
import json
import time
from pathlib import Path


STATIC_FIELDS = {'script', 'execution_log', 'scan', 'sandbox_packages', 'sandbox_resources', 'recipe', 'transform', 'quality', '_python_request'}


def statistics(task, manifest):
    counts, extensions = {}, {}
    for row in manifest:
        counts[row['action']] = counts.get(row['action'], 0) + 1
        path = row['path'].lower()
        extension = '.nii.gz' if path.endswith('.nii.gz') else Path(path).suffix or '(none)'
        extensions[extension] = extensions.get(extension, 0) + 1
    scope = task['scope']
    output = scope.get('workspace_id') and (scope['mode'] == 'copy' or scope['source_scope'] == 'output')
    return {'action_counts': counts, 'extension_counts': extensions,
            'overwrite': bool(output and any(r['action'] != 'delete' and r.get('target_before') is not None for r in manifest))}


class DataOperationStore:
    def __init__(self, store):
        self.store = store
        self.metrics = {'header_bytes': 0, 'detail_bytes': 0, 'commits': 0}
        with store.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS data_operations (id TEXT PRIMARY KEY, status TEXT, payload TEXT)')
            db.execute('CREATE INDEX IF NOT EXISTS data_operation_status ON data_operations(status)')
            db.execute('CREATE TABLE IF NOT EXISTS data_operation_inventories (id TEXT PRIMARY KEY, payload TEXT)')
            db.execute('''CREATE TABLE IF NOT EXISTS data_operation_items(
                operation_id TEXT, item_index INTEGER, payload TEXT, PRIMARY KEY(operation_id,item_index))''')
            db.execute('''CREATE TABLE IF NOT EXISTS data_operation_journal(
                operation_id TEXT, item_index INTEGER, payload TEXT, PRIMARY KEY(operation_id,item_index))''')
            db.execute('''CREATE TABLE IF NOT EXISTS data_operation_metadata(
                operation_id TEXT, key TEXT, payload TEXT, PRIMARY KEY(operation_id,key))''')
            db.execute('''CREATE TABLE IF NOT EXISTS data_operation_lookup(
                id TEXT PRIMARY KEY, actor TEXT, conversation_id TEXT)''')
            db.execute('CREATE INDEX IF NOT EXISTS data_operation_conversation ON data_operation_lookup(actor,conversation_id)')
            rows = db.execute('''SELECT d.id,d.payload FROM data_operations d
                LEFT JOIN data_operation_lookup l ON l.id=d.id WHERE l.id IS NULL''').fetchall()
            for identity, payload in rows:
                task = json.loads(payload)
                db.execute('INSERT INTO data_operation_lookup VALUES(?,?,?)',
                           (identity, task['actor'], task['conversation_id']))

    def save(self, task):
        task['updated_at'] = time.time()
        detail_bytes = 0
        with self.store.transaction() as db:
            saved = db.execute('SELECT payload FROM data_operations WHERE id=?', (task['id'],)).fetchone()
            previous = json.loads(saved[0]) if saved else {}
            normalized = previous.get('_storage_version') == 2
            seal = not normalized or (previous.get('manifest_ready') is False and task.get('manifest_ready') is True)
            header = {k: v for k, v in task.items() if k not in {'manifest', 'journal'} | STATIC_FIELDS}
            if seal:
                manifest = task['manifest']
                db.execute('DELETE FROM data_operation_items WHERE operation_id=?', (task['id'],))
                values = [(task['id'], i, json.dumps(row, ensure_ascii=False, separators=(',', ':')))
                          for i, row in enumerate(manifest)]
                db.executemany('INSERT INTO data_operation_items VALUES(?,?,?)', values)
                detail_bytes += sum(len(row[2].encode()) for row in values)
                header['_stats'] = statistics(task, manifest)
            else:
                header['_stats'] = previous['_stats']
            header['_storage_version'] = 2
            journal_count = previous.get('_journal_count', 0) if normalized else 0
            for entry in task.get('journal', []):
                value = json.dumps(entry, ensure_ascii=False, separators=(',', ':'))
                inserted = db.execute('INSERT OR IGNORE INTO data_operation_journal VALUES(?,?,?)',
                                      (task['id'], entry['index'], value)).rowcount
                journal_count += inserted
                if not inserted:
                    db.execute('UPDATE data_operation_journal SET payload=? WHERE operation_id=? AND item_index=?',
                               (value, task['id'], entry['index']))
                detail_bytes += len(value.encode())
            header['_journal_count'] = journal_count
            for key in STATIC_FIELDS:
                if key in task:
                    value = json.dumps(task[key], ensure_ascii=False, separators=(',', ':'))
                    db.execute('''INSERT INTO data_operation_metadata VALUES(?,?,?) ON CONFLICT(operation_id,key)
                        DO UPDATE SET payload=excluded.payload WHERE payload!=excluded.payload''', (task['id'], key, value))
            payload = json.dumps(header, ensure_ascii=False, separators=(',', ':'))
            db.execute('INSERT OR REPLACE INTO data_operations VALUES(?,?,?)', (task['id'], task['status'], payload))
            db.execute('INSERT OR REPLACE INTO data_operation_lookup VALUES(?,?,?)',
                       (task['id'], task['actor'], task['conversation_id']))
        task.update(_storage_version=2, _journal_count=journal_count, _stats=header['_stats'])
        self.metrics['header_bytes'] += len(payload.encode())
        self.metrics['detail_bytes'] += detail_bytes
        self.metrics['commits'] += 1

    def load(self, identity, detail='full'):
        with self.store.connection() as db:
            row = db.execute('SELECT payload FROM data_operations WHERE id=?', (identity,)).fetchone()
            if not row:
                raise KeyError('数据操作不存在')
            task = json.loads(row[0])
            if task.get('_storage_version') != 2:
                task['_stats'] = statistics(task, task['manifest'])
                task['_journal_count'] = len(task['journal'])
                return task
            task['manifest'], task['journal'] = [], []
            if detail != 'state':
                query = 'SELECT payload FROM data_operation_items WHERE operation_id=? ORDER BY item_index'
                if detail == 'preview':
                    query += ' LIMIT 100'
                task['manifest'] = [json.loads(r[0]) for r in db.execute(query, (identity,))]
                task.update({r[0]: json.loads(r[1]) for r in db.execute(
                    'SELECT key,payload FROM data_operation_metadata WHERE operation_id=?', (identity,))})
            if detail == 'full':
                task['journal'] = [json.loads(r[0]) for r in db.execute(
                    'SELECT payload FROM data_operation_journal WHERE operation_id=? ORDER BY item_index', (identity,))]
            return task

    def identities(self, actor, conversation_id):
        with self.store.connection() as db:
            return [r[0] for r in db.execute('''SELECT d.id FROM data_operations d
                JOIN data_operation_lookup l ON l.id=d.id WHERE l.actor=? AND l.conversation_id=?
                ORDER BY d.rowid DESC LIMIT 50''', (actor, conversation_id))]

    def interrupted(self):
        with self.store.connection() as db:
            return [r[0] for r in db.execute("SELECT id FROM data_operations WHERE status IN ('preparing','validating','running','rolling_back','queued','analyzing','cancel_requested')")]

    def items(self, identity, offset=0, limit=None):
        task = self.load(identity, 'state')
        if task.get('_storage_version') != 2:
            return task['manifest'][offset:offset + limit if limit is not None else None]
        with self.store.connection() as db:
            query = 'SELECT payload FROM data_operation_items WHERE operation_id=? AND item_index>=? ORDER BY item_index'
            params = (identity, offset)
            if limit is not None:
                query += ' LIMIT ?'; params += (limit,)
            return [json.loads(r[0]) for r in db.execute(query, params)]
