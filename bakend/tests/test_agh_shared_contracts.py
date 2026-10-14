"""Shared ownership and ROI regressions retained after removing the old demo."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.application import create_app
from app.data_auth import Principal, principal
from app.local_store import LocalStore
from app.workflow_models import Workflow
from app.pipeline_models import ComposeRequest, UsePipeline

ROOT = Path(__file__).resolve().parents[2]
SCIENCE = Path(os.getenv("LOCALFLOW_SKILL_PYTHON", sys.executable))


@pytest.fixture(autouse=True)
def require_science_for_direct_roi_tests(request):
    if request.node.name.startswith("test_shared_roi_"):
        try:
            result = subprocess.run([str(SCIENCE), "-c", "import nibabel, numpy, pandas"],
                                    capture_output=True, timeout=30)
        except OSError:
            pytest.skip("The configured scientific Python is unavailable")
        if result.returncode:
            pytest.skip("ROI checks require nibabel, numpy and pandas in the scientific Python")


@pytest.fixture
def services(tmp_path, monkeypatch):
    output = tmp_path / "outputs"
    output.mkdir()
    for key, value in {
        "LOCALFLOW_DATABASE_PATH": str(tmp_path / "state.sqlite"),
        "LOCALFLOW_OUTPUT_ROOT": str(output),
        "LOCALFLOW_DATA_ROOTS": str(tmp_path),
        "LOCALFLOW_RUN_INPUT_ROOTS": str(tmp_path),
        "WORKSPACE_DEFAULT_ROOT": str(output),
        "WORKSPACE_ALLOWED_ROOTS": str(output),
        "LOCALFLOW_SLURM_ENABLED": "false",
        "NEUROWEAVE_DATA_USERS": "{}",
    }.items():
        monkeypatch.setenv(key, value)
    app = create_app()
    yield app.state.services
    app.state.services.run_service.shutdown()
    app.state.services.operation_service.shutdown()
    app.state.services.data_operations.shutdown()


def test_index_matches_detail_before_pagination(tmp_path):
    store = LocalStore(tmp_path / 'acl.sqlite')
    # New pipelines, conversation-owned legacy rows, and truly unowned legacy rows.
    cases = [('alice_pipeline', 'alice', None, 'shared', None),
             ('bob_pipeline', 'bob', None, 'shared', None),
             ('alice_chat', 'alice', 'alice_chat', 'shared', None),
             ('legacy_chat', None, 'alice_chat', 'shared', None),
             ('legacy_unowned', None, None, 'shared', None),
             ('mismatched_chat', 'alice', 'bob_chat', 'shared', None),
             ('other_output', 'alice', None, 'other', None),
             ('other_dataset', 'alice', None, 'shared', 'other')]
    for name, owner, conversation, workspace, dataset in cases:
        wf = Workflow(workflow_id=name, template_id='pipeline:test', title=name,
                      summary='private', original_request='private', status='chain_ready',
                      planner_source='pipeline-library', workspace_id=workspace,
                      workspace_root_path=str(tmp_path),
                      steps=[], created_at='2026-10-12T00:00:00Z', updated_at='2026-10-12T00:00:00Z')
        store.save_workflow(wf, event_type='fixture_created')
        # Simulate historical persisted ownership, without changing production migration rules.
        with store._connect() as db:
            payload = wf.model_dump(); payload.update(owner_principal=owner, conversation_id=conversation, dataset_id=dataset)
            db.execute('UPDATE workflows SET payload_json=? WHERE workflow_id=?', (json.dumps(payload), name))
    with store._connect() as db:
        db.executemany('INSERT INTO data_conversation_owners (id,actor) VALUES (?,?)',
                       [('alice_chat', 'alice'), ('bob_chat', 'bob')])
    for actor in (Principal('alice', True, (), ('shared',)), Principal('bob', True, (), ('shared',)),
                  Principal('admin', True), Principal()):
        token = principal.set(actor)
        try:
            allowed = []
            for name, *_ in cases:
                try:
                    store.get_workflow(name)
                    allowed.append(name)
                except PermissionError:
                    pass
            actual = []
            for offset in range(len(allowed)):
                page = store.workflow_index(offset=offset, limit=1)
                assert len(page['items']) == 1
                assert page['has_more'] is (offset + 1 < len(allowed))
                actual.append(page['items'][0]['workflow_id'])
            assert actual == sorted(allowed)
            assert store.workflow_index(offset=len(allowed), limit=1)['items'] == []
        finally:
            principal.reset(token)


@pytest.mark.parametrize('case', ['volume', 'atlas', 'file_bytes'])
def test_shared_roi_accepts_ordinary_inputs(tmp_path, case):
    script = '''
import importlib.util, sys
from pathlib import Path
import nibabel as nib
import numpy as np
import pandas as pd
root, folder, case = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
shape = (101,101,101) if case == 'volume' else ((129,129,129) if case == 'file_bytes' else (10,10,3))
metric = np.ones(shape, dtype=np.float32)
labels = np.arange(1,301,dtype=np.int16).reshape(shape) if case == 'atlas' else np.ones(shape,dtype=np.int16)
m, r = folder/'metric.nii', folder/'roi.nii'
nib.save(nib.Nifti1Image(metric,np.eye(4)),m); nib.save(nib.Nifti1Image(labels,np.eye(4)),r)
if case == 'file_bytes': assert m.stat().st_size > 8*1024*1024
spec=importlib.util.spec_from_file_location('roi_reference',root/'skills/dipy-tool/scripts/roi_stats_reference.py')
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
module.roi_stats(m,r,None,folder/'out.csv',1)
table=pd.read_csv(folder/'out.csv')
assert len(table)==(300 if case=='atlas' else 1)
assert (table['mean']==1).all() and (table['std']==0).all()
assert table['n_vox'].sum()==metric.size
'''
    result = subprocess.run([str(SCIENCE), '-c', script, str(ROOT), str(tmp_path), case], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_new_pipeline_without_conversation_keeps_owner(services, monkeypatch):
    s = services
    # This is an ownership check, independent of installed scientific software.
    runner = s.run_service.runner_registry.get('mrtrix.command.mrthreshold')
    monkeypatch.setattr(runner, 'runtime_status', lambda: {'available': True, 'reason': 'Ownership fixture; no execution'})
    token = principal.set(Principal('alice', True, (), ('shared',)))
    try:
        pipeline = s.pipeline_library.save(ComposeRequest(name='Alice private pipeline',
            steps=[{'id': 'threshold', 'method_id': 'mrtrix.command.mrthreshold'}]))
        wf = s.pipeline_library.workflow(pipeline['id'], UsePipeline(), s.workflow_service, s.workspace_service)
        assert wf.conversation_id is None and wf.owner_principal == 'alice'
        assert s.local_store.workflow_index()['items'][0]['workflow_id'] == wf.workflow_id
    finally:
        principal.reset(token)
    token = principal.set(Principal('bob', True, (), ('shared',)))
    try:
        with pytest.raises(PermissionError): s.local_store.get_workflow(wf.workflow_id)
        assert s.local_store.workflow_index()['items'] == []
    finally:
        principal.reset(token)


def test_shared_roi_oracle_still_bounds_resources(tmp_path):
    script = '''
import importlib.util, sys, gzip
from pathlib import Path
import numpy as np
import nibabel as nib
root, folder = map(Path,sys.argv[1:])
spec=importlib.util.spec_from_file_location('roi',root/'scripts/hackathon/roi-input-contract.py')
roi=importlib.util.module_from_spec(spec); spec.loader.exec_module(roi)
def rejected(call, reason):
    try: call()
    except ValueError as error: assert reason in str(error),str(error)
    else: raise AssertionError('expected '+reason)
p=folder/'large.nii'
with p.open('wb') as stream: stream.truncate(roi.DEFAULT_LIMITS['file_bytes']+1)
rejected(lambda:roi.bounded_image(p),'file_too_large')
header=nib.Nifti1Header(); header.set_data_shape((401,401,401));header.set_data_dtype(np.float32)
p.write_bytes(header.binaryblock+b'\\0'*4)
rejected(lambda:roi.bounded_image(p),'volume_limit_or_not_3d')
z=folder/'compressed.nii.gz'
with gzip.open(z,'wb') as stream:stream.write(b'x'*1000)
rejected(lambda:roi.bounded_image(z,limits={**roi.DEFAULT_LIMITS,'expanded_bytes':100}),'expanded_file_too_large')
m,r=folder/'metric.nii',folder/'labels.nii'
nib.save(nib.Nifti1Image(np.ones((1,1,4097),dtype=np.float32),np.eye(4)),m)
nib.save(nib.Nifti1Image(np.arange(1,4098,dtype=np.int16).reshape(1,1,4097),np.eye(4)),r)
rejected(lambda:roi.validated_arrays(m,r),'too_many_labels')
'''
    result = subprocess.run([str(SCIENCE), '-c', script, str(ROOT), str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
