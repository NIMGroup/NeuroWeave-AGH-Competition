"""Generic naming recipes, full-scope accounting and large generated manifests."""
import pytest
from pydantic import ValidationError

from app.data_models import DataPlanRequest, OrganizeRequest
from test_data_operations import data, approval, finished


def organize(data, **kwargs):
    return data.service.organize(OrganizeRequest(**data.scope, **kwargs))


def test_incremental_rules_preserve_previous_coverage_and_exclusions(data):
    for stem in ['old_alpha', 'old_beta', 'skip/old_gamma']:
        path = data.source / (stem + '.nii.gz')
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b'a')
        path.with_name(path.name[:-7] + '.json').write_text('{}')
    first = organize(data, rules=[{'pattern': '*alpha*', 'name': 'alpha'}], exclude_paths=['skip'], require_complete=True)
    assert first['scan']['matched_files'] == 2 and first['scan']['excluded_files'] == 2
    assert not first['can_confirm']
    second = organize(data, base_operation_id=first['id'], rules=[{'pattern': '*beta*', 'name': 'beta'}])
    assert second['scan']['matched_files'] == 4 and second['scan']['unmatched_files'] == 0
    assert second['scan']['file_count'] == 6 and not second['scan']['source_changed']
    assert second['quality']['accounted_files'] == 6 and second['can_confirm']
    assert len(second['recipe']['rules']) == 2
    assert data.service.get(first['id'], data.scope['conversation_id'])['scan']['unmatched_files'] == 2


def test_regressed_coverage_blocks_execution_and_confirmation(data):
    for name in ['a_alpha.nii.gz', 'b_beta.nii.gz']:
        (data.source / name).write_bytes(b'a')
    first = organize(data, rules=[{'pattern': '*', 'name': 'scan'}], require_complete=True)
    second = organize(data, base_operation_id=first['id'], remove_patterns=['*'],
        rules=[{'pattern': '*alpha*', 'name': 'alpha'}], execution='output')
    assert second['quality']['regressed_files'] == 1
    assert 'coverage_regression' in second['quality']['blocking_issues']
    assert second['status'] == 'pending' and not second['can_confirm']
    with pytest.raises(ValueError):
        data.service.confirm(second['id'], approval(second))
    assert not list(data.output.iterdir())


def test_fixing_match_target_replaces_one_rule_and_explicit_narrowing_is_supported(data):
    (data.source/'known.nii.gz').write_bytes(b'a')
    (data.source/'keep.txt').write_text('b')
    first = organize(data, rules=[{'pattern':'known*','name':'known'}, {'pattern':'keep.txt','match_on':'stem','kind':'file','name':'keep'}], require_complete=True)
    fixed = organize(data, base_operation_id=first['id'], rules=[{'pattern':'keep.txt','match_on':'filename','kind':'file','name':'keep'}])
    assert len(fixed['recipe']['rules'])==2 and fixed['scan']['unmatched_files']==0
    narrowed = organize(data, base_operation_id=fixed['id'], replace_rules=True, rules=[{'pattern':'known*','name':'known'}], require_complete=False)
    assert narrowed['scan']['unmatched_files']==1 and narrowed['can_confirm']


def test_stem_matching_priority_and_exclusion_disambiguate_generic_names(data):
    for name in ['series_fast_7.nii.gz', 'series_fast_7_ph.nii.gz']:
        (data.source / name).write_bytes(b'a')
    task = organize(data, rules=[{'pattern': 'series_fast*[0-9]', 'match_on': 'stem', 'name': 'magnitude'},
        {'pattern': 'series_fast*_ph', 'match_on': 'stem', 'name': 'phase'}], require_complete=True)
    assert not task['conflicts'] and task['scan']['unmatched_files'] == 0
    assert {i['destination'] for i in task['items']} == {'magnitude.nii.gz', 'phase.nii.gz'}
    overlapping = organize(data, rules=[{'pattern': 'series*', 'name': 'common'},
        {'pattern': '*_ph*', 'name': 'phase', 'priority': 1}], require_complete=True)
    assert not overlapping['conflicts']
    excluded = organize(data, rules=[{'pattern': 'series*', 'name': 'common', 'exclude': ['*_ph*']},
        {'pattern': '*_ph*', 'name': 'phase'}], require_complete=True)
    assert not excluded['conflicts']


def test_file_rules_and_conservative_fallback_account_for_entire_scope_and_restore(data):
    (data.source / 'raw_alpha.nii.gz').write_bytes(b'image')
    (data.source / 'raw_alpha.json').write_text('{}')
    (data.source / 'motion_record.csv').write_text('x\n1\n')
    (data.source / 'uncertain.bin').write_bytes(b'unknown')
    task = organize(data, rules=[{'pattern': '*alpha*', 'name': 'alpha'},
        {'pattern': 'motion*', 'kind': 'file', 'name': 'motion'}], unmatched_policy='preserve', require_complete=True, execution='output')
    assert task['scan']['matched_files'] == 4 and task['scan']['preserved_files'] == 1
    assert task['quality']['complete']
    assert task['status'] == 'pending' and not list(data.output.iterdir())
    data.service.confirm(task['id'], approval(task))
    assert finished(data, task)['status'] == 'succeeded'
    assert (data.output / 'motion.csv').read_text() == 'x\n1\n'
    assert (data.output / 'uncertain.bin').read_bytes() == b'unknown'
    assert (data.output / 'alpha.json').read_text() == '{}'
    data.service.undo(task['id'], approval(task))
    assert finished(data, task)['status'] == 'rolled_back'
    assert not list(data.output.iterdir()) and len(list(data.source.iterdir())) == 4


def test_full_snapshot_grouping_covers_beyond_first_page(data):
    (data.source / 'known.nii.gz').write_bytes(b'a')
    for i in range(220):
        (data.source / f'unknown_{i:04}.csv').write_text('a')
    task = organize(data, rules=[{'pattern': 'known*', 'name': 'known'}])
    report = data.service.coverage(task['id'], data.scope['conversation_id'])
    assert report['snapshot'] and report['unmatched']['file_count'] == 220
    assert report['unmatched']['groups'][0]['count'] == 220
    assert report['unmatched']['extension_counts'] == {'.csv': 220}


def test_server_generated_manifest_exceeds_external_edit_limit_without_loss(data):
    for i in range(5001):
        for suffix in ['.nii.gz', '.json']:
            (data.source / f'series_{i:05}{suffix}').write_bytes(b'a')
    task = organize(data, rules=[{'pattern': 'series*', 'name': 'series'}], require_complete=True)
    assert task['file_count'] == 10002 and task['scan']['unmatched_files'] == 0
    assert len(data.service.repository.items(task['id'])) == 10002
    assert task['can_confirm'] and not list(data.output.iterdir())
    with pytest.raises(ValidationError):
        DataPlanRequest(**data.scope, edits=[{'action': 'copy', 'path': 'a', 'destination': 'b'}] * 10001)


def test_recipe_cannot_follow_changed_output_scope_or_bypass_dataset_grants(data):
    (data.source / 'raw.nii.gz').write_bytes(b'a')
    first = organize(data, mode='inplace', rules=[{'pattern': '*', 'name': 'alpha'}])
    with pytest.raises(PermissionError):
        organize(data, base_operation_id=first['id'], rules=[{'pattern': '*', 'name': 'beta'}])
    second = organize(data, mode='inplace', base_operation_id=first['id'],
        rules=[{'pattern': '*', 'name': 'beta'}], execution='output')
    assert second['requires_inplace_confirmation'] and second['status'] == 'pending'
    assert first['manifest_hash'] != second['manifest_hash']
    with pytest.raises(PermissionError):
        data.service.confirm(second['id'], approval(first, inplace=True))
    assert (data.source / 'raw.nii.gz').exists()


def test_legacy_recipe_recovery_reads_frozen_event_without_rewriting_old_record(data):
    (data.source / 'raw_alpha.nii.gz').write_bytes(b'a')
    args = {'rules': [{'pattern': '*alpha*', 'name': 'alpha'}]}
    first = organize(data, **args)
    data.workspace.event('legacy-turn', {'tool': 'propose_data_organization', 'status': 'pending', 'arguments': args, 'data_operation': first})
    with data.service.store.transaction() as db:
        db.execute("DELETE FROM data_operation_metadata WHERE operation_id=? AND key='recipe'", (first['id'],))
        frozen = db.execute('SELECT payload FROM data_operations WHERE id=?', (first['id'],)).fetchone()[0]
    read = data.service.get(first['id'], data.scope['conversation_id'])
    assert read['recipe']['provenance'] == 'frozen_tool_event'
    next_task = organize(data, base_operation_id=first['id'], rules=[])
    assert next_task['file_count'] == 1
    with data.service.store.connection() as db:
        assert db.execute('SELECT payload FROM data_operations WHERE id=?', (first['id'],)).fetchone()[0] == frozen
