"""Sequence-local evidence and partial/unknown results, without image computation."""
import asyncio
import gzip
import struct

import pytest

from app.data_sequences import gradient_summary, image_header, inspect_sequences, profile_sequence
from app.dataset_service import DatasetService
from app.agent_team import AgentTeam
from test_agent_team import TeamProvider, setup
from test_data_operations import data


def nifti(path, volumes, order='<'):
    raw = bytearray(352)
    struct.pack_into(order+'i', raw, 0, 348)
    struct.pack_into(order+'8h', raw, 40, 4, 2, 2, 2, volumes, 1, 1, 1)
    raw[344:348] = b'n+1\0'
    path.write_bytes(gzip.compress(raw) if path.name.endswith('.gz') else raw)


def sequence(root, stem, bvals):
    nifti(root/(stem+'.nii.gz'), len(bvals))
    (root/(stem+'.bval')).write_text(' '.join(map(str, bvals)))
    (root/(stem+'.bvec')).write_text('\n'.join(' '.join('1' for _ in bvals) for _ in range(3)))


def test_different_sequences_keep_their_own_shells_and_header_matches(data):
    sequence(data.source, 'single', [0, 1000, 1000])
    sequence(data.source, 'multi', [0, 500, 1000, 2000])
    single = profile_sequence(data.source, 'single.nii.gz')
    multi = profile_sequence(data.source, 'multi.nii.gz')
    assert single['status'] == multi['status'] == 'found'
    assert single['gradients']['shell_count'] == 1 and multi['gradients']['shell_count'] == 3
    assert multi['header']['volume_count'] == multi['gradients']['volume_count'] == 4
    assert single['features']['json'] == 'missing'
    assert single['features']['preprocessing_verified'] == 'unknown'
    service = DatasetService(data.source.parent, data.service.store)
    inspection = service._inspect(data.source)
    assert inspection.shell_count is None and len(inspection.sequence_profiles) == 2
    assert inspection.sequence_profiles_complete


@pytest.mark.parametrize('order', ['<', '>'])
def test_header_read_is_bounded_and_handles_byte_order(tmp_path, order):
    path = tmp_path/'image.nii'; nifti(path, 7, order)
    assert image_header(path)['volume_count'] == 7


def test_mif_embedded_gradients_need_no_external_bval_bvec(data):
    (data.source/'image.mif').write_bytes(b'mrtrix image\ndim: 2,2,2,3\ndw_scheme: 0,0,0,0\ndw_scheme: 1,0,0,1000\ndw_scheme: 0,1,0,2000\nEND\n')
    result = profile_sequence(data.source, 'image.mif')
    assert result['status'] == 'found' and result['gradient_source'] == 'mif_header'
    assert result['gradients']['shell_count'] == 2


def test_mismatched_counts_invalid_values_and_missing_pairs_remain_unknown(data):
    sequence(data.source, 'image', [0, 1000])
    nifti(data.source/'image.nii.gz', 4)
    result = profile_sequence(data.source, 'image.nii.gz')
    assert result['status'] == 'unknown' and result['issues']
    (data.source/'image.bval').write_text('0 nan')
    assert profile_sequence(data.source, 'image.nii.gz')['status'] == 'unknown'
    (data.source/'image.bval').unlink()
    result = profile_sequence(data.source, 'image.nii.gz')
    assert result['features']['bval'] == 'missing' and result['features']['paired_gradients'] == 'unknown'


def test_partial_registration_does_not_claim_absence_or_a_global_shell(data, monkeypatch):
    sequence(data.source, 'image', [0, 1000])
    service = DatasetService(data.source.parent, data.service.store)
    monkeypatch.setattr(service, 'MAX_FILES', 1)
    result = service._inspect(data.source)
    assert result.truncated and result.scan_complete is False
    assert result.feature_states['has_t1w'] == 'unknown' and result.shell_count is None


def test_empty_partial_index_unknown_and_complete_index_missing(data, monkeypatch):
    owner, req = setup(data, TeamProvider())
    inventory = {'complete_scan': False, 'scope_complete': False, 'index_id': 'one'}
    monkeypatch.setattr('app.data_sequences.rows_for', lambda *a: (inventory, []))
    result = inspect_sequences(data.service, req, {**data.scope, 'mode': 'inplace'}, {'source_scope': 'dataset'})
    assert result['scope_status'] == 'unknown' and result['complete'] is False
    inventory.update(complete_scan=True, scope_complete=True)
    result = inspect_sequences(data.service, req, {**data.scope, 'mode': 'inplace'}, {'source_scope': 'dataset'})
    assert result['scope_status'] == 'missing' and result['complete']


def test_sequence_query_reuses_fact_summary_and_does_not_run_software(data):
    sequence(data.source, 'image', [0, 1000, 2000])
    owner, req = setup(data, TeamProvider()); team = AgentTeam(owner, req)
    first = asyncio.run(team.query('data', 'inspect_data_sequences', {'source_scope': 'dataset'}))
    second = asyncio.run(team.query('data', 'inspect_data_sequences', {'source_scope': 'dataset'}))
    assert first['series'][0]['gradients']['shell_count'] == 2 and second['reused']
    assert first['entity_id'] == second['entity_id'] and not data.service.futures
    assert not list(data.output.iterdir())


def test_representative_page_does_not_claim_every_sequence_verified(data):
    sequence(data.source, 'a', [0, 1000]); sequence(data.source, 'b', [0, 2000])
    owner, req = setup(data, TeamProvider())
    result = inspect_sequences(data.service, req, {**data.scope, 'mode': 'inplace'}, {'source_scope': 'dataset', 'limit': 1})
    assert result['matched_series_count'] == 2 and result['verified_series_count'] == 1
    assert result['complete'] is False and result['next_offset'] == 1


def test_sequence_reader_rejects_out_of_scope_companion_link(data, tmp_path):
    sequence(data.source, 'image', [0, 1000])
    (data.source/'image.bval').unlink()
    outside = tmp_path/'outside.bval'; outside.write_text('0 1000')
    try:
        (data.source/'image.bval').symlink_to(outside)
    except OSError:
        pytest.skip('Symlink creation unavailable')
    result = profile_sequence(data.source, 'image.nii.gz')
    assert result['features']['bval'] == 'unknown' and 'gradients' not in result


def test_gradient_validation_supports_transposed_vectors_without_bulk_rows():
    result = gradient_summary('0 1000 2000 3000', '0 0 0\n1 0 0\n0 1 0\n0 0 1')
    assert result['gradients_consistent'] and result['vector_shape'] == [4, 3]
    with pytest.raises(ValueError):
        gradient_summary('0 inf')


def test_gradient_change_invalidates_cache_even_when_selected_directory_did_not_change(data):
    (data.source/'subject').mkdir()
    sequence(data.source/'subject', 'image', [0, 1000, 2000])
    owner, req = setup(data, TeamProvider()); team = AgentTeam(owner, req)
    args = {'source_scope': 'dataset'}
    first = asyncio.run(team.query('data', 'inspect_data_sequences', args))
    root_time = data.source.stat().st_mtime_ns
    (data.source/'subject/image.bval').write_text('0 1000 1000')
    assert data.source.stat().st_mtime_ns == root_time
    second = asyncio.run(team.query('data', 'inspect_data_sequences', args))
    assert not second.get('reused') and second['series'][0]['gradients']['shell_count'] == 1
    assert second['content_hash'] != first['content_hash']


def test_sequence_pages_merge_profiles_instead_of_forgetting_previous_images(data):
    sequence(data.source, 'a', [0, 1000]); sequence(data.source, 'b', [0, 2000])
    owner, req = setup(data, TeamProvider()); team = AgentTeam(owner, req)
    first = asyncio.run(team.query('data', 'inspect_data_sequences', {'source_scope': 'dataset', 'limit': 1}))
    second = asyncio.run(team.query('data', 'inspect_data_sequences', {'source_scope': 'dataset', 'limit': 1, 'offset': 1}))
    assert first['entity_id'] == second['entity_id']
    entity = team.facts.typed.read(req, first['entity_id'])
    assert set(entity['content']['series']) == {'a.nii.gz', 'b.nii.gz'}
    assert entity['summary']['structured']['stored_series_profiles_complete']
