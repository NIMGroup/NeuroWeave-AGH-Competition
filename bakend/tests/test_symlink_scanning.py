"""Real link traversal, reporting, read boundaries and unchanged run approvals."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.filesystem_scan import ScopedScan
from app.workspace_paths import WorkspacePaths
from app.dataset_files import DatasetReader
from app.data_models import DataEdit, DataPlanRequest, OrganizeRequest
from app.models import ChatRequest
from app.workspace_tools import WorkspaceTools
from app.workspace_routes import workspace_router
from app.execution.runners.dataset_inspect import DatasetInspectRunner
from test_data_operations import data, approval, finished


def link(path, target, directory=False):
    try:
        path.symlink_to(target, target_is_directory=directory)
    except OSError:
        pytest.skip('Real symlinks require Linux or Windows link privilege')


def tree(tmp_path):
    root=tmp_path/'scope';root.mkdir()
    (root/'real').mkdir();(root/'real'/'note.txt').write_text('inside')
    (root/'plain.txt').write_text('plain')
    link(root/'file-link',root/'plain.txt')
    link(root/'directory-link',root/'real',True)
    link(root/'broken',root/'missing')
    return root


def test_default_scan_reports_and_skips_file_directory_and_broken_links(tmp_path):
    root=tree(tmp_path);scan=ScopedScan(root,WorkspacePaths(root,allowed_roots=(root,)))
    files=[p.relative_to(root).as_posix() for p,_,i,_ in scan.walk() if p.is_file()]
    assert files==['plain.txt','real/note.txt']
    report=scan.report()
    assert report['skipped_symlinks']==3 and report['complete_scan'] and not report['scope_complete']
    assert {r['path'] for r in report['symlink_examples']}=={'broken','file-link','directory-link'}
    assert report['warnings'] and not report['symlink_examples_truncated']


def test_follow_is_scoped_deduplicated_and_terminates_cycles(tmp_path):
    root=tree(tmp_path);outside=tmp_path/'outside';outside.mkdir();(outside/'hidden.txt').write_text('outside')
    link(root/'escape',outside,True);link(root/'loop',root,True);link(root/'self', 'self')
    (root/'.env').write_text('secret');link(root/'secret-link',root/'.env')
    scan=ScopedScan(root,WorkspacePaths(root,allowed_roots=(root,)),follow_links=True)
    files=[target for _,target,_,_ in scan.walk() if target.is_file()]
    assert set(files)=={root/'plain.txt',root/'real'/'note.txt'} and len(files)==2
    report=scan.report();reasons=report['exclusion_reasons']
    assert reasons['unauthorized']>=2 and reasons['broken']==1 and reasons['cycle']==1
    assert report['duplicate_targets']>=2 and report['skipped_symlinks']>=6


def test_read_target_checks_every_link_hop(tmp_path):
    root=tmp_path/'root';root.mkdir();(root/'ok.txt').write_text('ok')
    outside=tmp_path/'outside';outside.mkdir()
    link(outside/'return',root/'ok.txt');link(root/'roundtrip',outside/'return')
    (root/'.env-area').mkdir();link(root/'.env-area'/'return',root/'ok.txt')
    link(root/'sensitive-hop',root/'.env-area'/'return')
    reader=DatasetReader(root)
    for path in ('roundtrip','sensitive-hop'):
        with pytest.raises(PermissionError):reader.read_text(path)
    link(root/'safe',root/'ok.txt')
    assert reader.read_text('safe')['text']=='ok'
    assert reader.inspect('safe')['resolved_path']==str(root/'ok.txt')
    with pytest.raises(PermissionError):reader.paths.resolve(root,'safe',write=True)


def test_readonly_search_reports_links_and_explicit_followed_selection(tmp_path):
    root=tree(tmp_path);reader=DatasetReader(root)
    skipped=reader.search(pattern='*.txt')
    assert len(skipped['items'])==2 and skipped['skipped_symlinks']==3
    direct=reader.search(path='directory-link',pattern='*.txt',follow_links=True)
    assert len(direct['items'])==1 and direct['followed_symlinks']==1
    assert direct['items'][0]['via_path']=='directory-link/note.txt'
    assert direct['items'][0]['writable'] is False
    default=reader.search(path='directory-link',pattern='*')
    assert not default['items'] and default['skipped_symlinks']==1


def test_link_report_is_bounded_and_explicit(tmp_path):
    root=tmp_path/'root';root.mkdir();(root/'actual').write_text('x')
    for i in range(60):link(root/f'alias-{i:02}',root/'actual')
    scan=ScopedScan(root,WorkspacePaths(root,allowed_roots=(root,)));list(scan.walk())
    r=scan.report();assert r['skipped_symlinks']==60 and len(r['symlink_examples'])==50
    assert r['symlink_examples_truncated']


def test_truncated_walk_does_not_claim_complete_scope(tmp_path):
    root=tmp_path/'root';root.mkdir()
    for i in range(5):(root/str(i)).write_text('x')
    scan=ScopedScan(root,WorkspacePaths(root,allowed_roots=(root,)),max_entries=2,strict=False)
    list(scan.walk());assert not scan.report()['complete_scan'] and scan.stop_reason
    strict=ScopedScan(root,WorkspacePaths(root,allowed_roots=(root,)),max_entries=2)
    with pytest.raises(ValueError):list(strict.walk())


def test_inventory_and_organization_skip_links_without_running(data):
    (data.source/'old.txt').write_text('old');link(data.source/'alias.txt',data.source/'old.txt')
    task=data.service.organize(OrganizeRequest(**data.scope,mode='copy',
        rules=[{'pattern':'*','name':'clean','kind':'file'}]))
    assert task['status']=='pending' and task['file_count']==1
    assert task['scan']['skipped_symlinks']==1 and task['scan']['warnings']
    assert not list(data.output.iterdir())
    data.service.confirm(task['id'],approval(task))
    assert finished(data,task)['status']=='succeeded' and (data.output/'clean.txt').read_text()=='old'
    assert (data.source/'alias.txt').is_symlink()


def test_read_query_follow_policy_is_cached_separately_and_cannot_grant_writes(data):
    (data.source/'real').mkdir();(data.source/'real'/'note.txt').write_text('inside')
    link(data.source/'alias',data.source/'real',True)
    request=ChatRequest(message='inspect',**{k:data.scope[k] for k in ('conversation_id','dataset_id','workspace_id')})
    tools=WorkspaceTools(data.workspace,object());tools.data_operations=data.service
    args={'source_scope':'dataset','path':'alias','view':'summary'}
    skipped=tools.execute('inventory_data_files',args,request)
    assert skipped['file_count']==0 and skipped['skipped_symlinks']==1
    followed=tools.execute('inventory_data_files',{**args,'follow_links':True},request)
    assert followed['file_count']==1 and followed['items'][0]['resolved_path']=='real/note.txt'
    assert followed['inventory_id']!=skipped['inventory_id']
    with pytest.raises(PermissionError):
        data.service.propose(DataPlanRequest(**data.scope,mode='inplace',
            edits=[DataEdit(action='rename',path='alias/note.txt',destination='new.txt')]))


def test_http_readonly_follow_flag_and_existing_access_scope(tmp_path):
    root=tree(tmp_path);reader=DatasetReader(root)
    files=SimpleNamespace(reader=lambda _:reader)
    app=FastAPI();app.include_router(workspace_router(SimpleNamespace(),files))
    with TestClient(app) as client:
        url='/api/datasets/test/files/search'
        result=client.get(url,params={'path':'directory-link','pattern':'*.txt','follow_links':'true'})
        assert result.status_code==200 and result.json()['followed_symlinks']==1
        assert client.get(url,params={'path':'../outside','follow_links':'true'}).status_code in (400,403,422)


def test_dataset_runner_applies_same_skip_and_follow_rules(tmp_path):
    root=tree(tmp_path);output=tmp_path/'report';output.mkdir()
    context=SimpleNamespace(workflow=SimpleNamespace(dataset=SimpleNamespace(absolute_path=str(root),dataset_id='d',display_name='test')),
        step=SimpleNamespace(selected_parameters={'bids_validation':False},id='s'),output_directory=output,
        raise_if_cancelled=lambda:None,log=lambda *args:None,file_sha256=lambda _: 'fake')
    runner=DatasetInspectRunner();runner.run(context)
    report=json.loads((output/'dataset_profile.json').read_text())
    assert report['file_count']==2 and report['skipped_symlinks']==3 and report['warnings']
    context.step.selected_parameters['follow_links']=True
    runner.run(context);report=json.loads((output/'dataset_profile.json').read_text())
    assert report['file_count']==2 and report['duplicate_targets']==2 and report['skipped_symlinks']==3


def test_model_preview_preserves_exclusions_under_context_pressure():
    from app.assistant_runtime import preview
    result={'items':[{'path':str(i)*300,'size':1} for i in range(100)],
            'complete_scan':True,'scope_complete':False,'skipped_symlinks':73,
            'symlink_policy':'skip','warnings':['跳过链接，目标不在范围内'],
            'symlink_examples':[{'path':str(i)*120,'reason':'not_followed'} for i in range(50)],
            'symlink_examples_truncated':True}
    small=preview(result,2000)
    assert small['skipped_symlinks']==73 and small['scope_complete'] is False and small['symlink_policy']=='skip'


def test_link_only_selection_does_not_claim_task_completion(data):
    target=data.output/'unselected.txt';target.write_text('outside selected input')
    link(data.source/'alias.txt',target)
    task=data.service.organize(OrganizeRequest(**data.scope,mode='copy',
        rules=[{'pattern':'*','name':'clean','kind':'file'}]))
    assert task['status']=='pending' and not task['can_confirm']
    assert task['quality']['blocking_issues']==['no_eligible_files']
    assert task['scan']['skipped_symlinks']==1 and not (data.output/'clean.txt').exists()
