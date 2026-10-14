#!/usr/bin/env python3
"""Synthetic copy/undo benchmark. Requires fresh roots; never reads user batches."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bakend'))
from app.chat_service import ChatService
from app.conversation_service import ConversationService
from app.data_models import DataConfirmation, DataEdit, DataPlanRequest
from app.data_operations import DataOperations, digest
from app.dataset_models import DatasetInspection, DatasetRecord
from app.local_store import LocalStore
from app.workspace_models import WorkspaceCreate
from app.workspace_service import WorkspaceService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True, help='Fresh private synthetic file directory, may be NFS')
    parser.add_argument('--state', type=Path, required=True, help='Fresh local disk directory for SQLite')
    parser.add_argument('--counts', default='100,1000,6194')
    args = parser.parse_args()
    counts = [int(v) for v in args.counts.split(',')]
    assert all(1 <= v <= 10000 for v in counts)
    root, state = args.root.absolute(), args.state.absolute()
    assert root != state and not root.exists() and not state.exists()
    root.mkdir(parents=True, exist_ok=False); state.mkdir(parents=True, exist_ok=False)
    os.environ['WORKSPACE_ALLOWED_ROOTS'] = str(root)
    os.environ['WORKSPACE_DEFAULT_ROOT'] = str(root)
    results = []
    for count in counts:
        base = root / str(count); base.mkdir()
        source, output = base / 'input', base / 'output'
        source.mkdir(); output.mkdir()
        for index in range(count):
            (source / f'{index:05}.bin').write_bytes((index.to_bytes(4, 'big') * 1024))
        store = LocalStore(state / str(count) / 'db.sqlite')
        with store.transaction() as db:
            db.execute('PRAGMA journal_mode=WAL')
        workspace = WorkspaceService(store, output)
        ws = workspace.create(WorkspaceCreate(name='synthetic benchmark', root_path=str(output)))
        conv = ConversationService(store, ChatService()).create('synthetic benchmark')
        workspace.bind_conversation(conv.conversation_id, ws.id)
        store.save_dataset(DatasetRecord(dataset_id='ds_benchmark', root_id='synthetic', relative_path='input',
            display_name='synthetic benchmark', absolute_path=str(source), inspection=DatasetInspection(scanned_at='now'),
            created_at='now', updated_at='now'))
        datasets = SimpleNamespace(reader=lambda identity: SimpleNamespace(root=lambda: source))
        service = DataOperations(workspace, datasets)
        scope = dict(conversation_id=conv.conversation_id, dataset_id='ds_benchmark', workspace_id=ws.id, source_scope='dataset', mode='copy')
        try:
            start = time.monotonic()
            task = service.propose(DataPlanRequest(**scope, edits=[DataEdit(action='copy', path=f'{i:05}.bin', destination=f'copied/{i:05}.bin') for i in range(count)]))
            planned = time.monotonic() - start
            before = dict(service.repository.metrics)
            approval = DataConfirmation(confirmed=True, conversation_id=conv.conversation_id, manifest_hash=task['manifest_hash'])
            start = time.monotonic()
            service.confirm(task['id'], approval)
            service.futures[task['id']].result(timeout=1800)
            elapsed = time.monotonic() - start
            done = service.get(task['id'], conv.conversation_id)
            assert done['status'] == 'succeeded' and done['completed'] == count, done
            assert done['processed_bytes'] == count * 4096
            for index in range(count):
                assert digest(source / f'{index:05}.bin') == digest(output / f'copied/{index:05}.bin')
            progress_bytes = len(json.dumps(service.progress(task['id'], conv.conversation_id)).encode())
            metrics = {key: service.repository.metrics[key] - before[key] for key in before}
            start = time.monotonic()
            service.undo(task['id'], approval)
            service.futures[task['id']].result(timeout=1800)
            restored = service.get(task['id'], conv.conversation_id)
            assert restored['status'] == 'rolled_back' and not (output / 'copied').exists(), restored
            assert len(list(source.iterdir())) == count
            result = {'files': count, 'bytes': count * 4096, 'plan_seconds': round(planned, 3),
                      'execute_seconds': round(elapsed, 3), 'undo_seconds': round(time.monotonic() - start, 3),
                      'files_per_second': round(count / elapsed, 2), 'progress_response_bytes': progress_bytes,
                      'storage_writes': metrics, 'checksum_verified': True, 'undo_verified': True,
                      'workers': service.file_workers}
            results.append(result)
            print(json.dumps(result), flush=True)
        finally:
            service.shutdown()
    (state / 'report.json').write_text(json.dumps({'synthetic_root': str(root), 'results': results}, indent=2) + '\n', encoding='utf8')


if __name__ == '__main__':
    main()
