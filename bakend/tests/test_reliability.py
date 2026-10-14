"""Regressions for durable state, bounded reads, retry evidence and backup safety."""
from contextlib import closing
from pathlib import Path
from threading import Event
import json
import sqlite3
import time

import pytest

from app.local_store import LocalStore
from app.run_models import RunExecutionOptions, RunStep, WorkflowRun
from app.operation_service import OperationService
from app.operation_context import checkpoint
from app.maintenance import backup_database, retain_backups, restore_database, verify_backup, expire_operations
from app.conversation_service import ConversationService
from app.chat_service import ChatService
from app.execution.base import RunnerResult
from app.execution.run_service import RunConflict
from app.run_models import RunArtifact
from app.workflow_models import InputBinding, Workflow
import test_slurm_run_service as slurm_fixtures

from datetime import datetime, timezone
NOW = datetime.now(timezone.utc).isoformat()


@pytest.fixture
def store(tmp_path):
    value = LocalStore(tmp_path / "test.db")
    value.save_workflow(Workflow(workflow_id='wf', template_id='fixture', title='fixture',
        summary='fixture', original_request='fixture', status='confirmed', revision=1,
        steps=[], created_at=NOW, updated_at=NOW), event_type='fixture')
    return value


def new_run():
    return WorkflowRun(run_id="run_test", workflow_id="wf", workflow_revision=1,
        created_at=NOW, updated_at=NOW, output_directory="/synthetic/run_test",
        plan_snapshot={"large_immutable_plan": "x" * 100000},
        steps=[RunStep(step_id=key, title=key, runner_id="fixture", method_id="fixture") for key in ("a", "b")])


def test_incremental_state_keeps_plan_out_of_polling_and_writes_only_dirty_steps(store):
    run = new_run()
    store.save_run(run)
    initial = store.monitor_run(run.run_id)
    assert initial["reset"] and len(initial["steps"]) == 2
    assert "plan_snapshot" not in initial["run"]
    revision = initial["run"]["state_revision"]
    run.steps[1].progress = 55
    store.save_run(run, step_id="b", event_type="progress")
    delta = store.monitor_run(run.run_id, revision)
    assert not delta["reset"] and [step["step_id"] for step in delta["steps"]] == ["b"]
    assert store.monitor_run(run.run_id, delta["run"]["state_revision"])["steps"] == []
    assert store.get_run(run.run_id).plan_snapshot == run.plan_snapshot
    with store._connect() as connection:
        revisions = dict(connection.execute("SELECT step_id,state_revision FROM run_steps"))
    assert revisions["a"] == revision and revisions["b"] == revision + 1
    assert len(json.dumps(store.list_runs(summary=True))) < 3000


def test_old_inline_records_remain_readable_and_migrate_on_first_write(store):
    run = new_run()
    with store._connect() as connection:
        connection.execute("""INSERT INTO runs(run_id,workflow_id,workflow_revision,status,payload_json,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?)""", (run.run_id,"wf",1,"queued",run.model_dump_json(),NOW,NOW))
    assert store.get_run(run.run_id).plan_snapshot == run.plan_snapshot
    assert store.monitor_run(run.run_id)["reset"]
    restored = store.get_run(run.run_id)
    restored.steps[0].status = "succeeded"
    store.save_run(restored, step_id="a")
    assert len(store.get_run(run.run_id).steps) == 2
    assert store.get_run(run.run_id).plan_snapshot == run.plan_snapshot


def test_parallel_completion_artifact_order_survives_normalization(store):
    run = new_run()
    for step in run.steps:
        step.artifacts = [RunArtifact(artifact_id=step.step_id,step_id=step.step_id,
            artifact_type="synthetic",label=step.step_id,absolute_path="/synthetic/"+step.step_id,
            relative_path=step.step_id)]
    run.artifacts = [*run.steps[1].artifacts,*run.steps[0].artifacts]
    store.save_run(run)
    assert [a.artifact_id for a in store.get_run(run.run_id).artifacts] == ["b","a"]


def test_event_cursors_cross_1000_and_history_pages_do_not_skip_or_repeat(store):
    run = new_run()
    store.save_run(run)
    with store._connect() as connection:
        connection.executemany("""INSERT INTO run_events(run_id,event_type,level,message,payload_json,created_at)
            VALUES(?,'log','info',?,'{}',?)""", [(run.run_id,str(index),NOW) for index in range(1250)])
    cursor, identifiers = 0, []
    while True:
        page = store.run_event_page(run.run_id, after_event_id=cursor, limit=200)
        identifiers.extend(event["event_id"] for event in page["events"])
        cursor = page["next_event_id"]
        if not page["has_more"]:
            break
    assert len(identifiers) == len(set(identifiers)) == 1250
    tail = store.run_event_page(run.run_id, tail=True, limit=20)
    assert [event["message"] for event in tail["events"]] == list(map(str,range(1230,1250)))
    older = store.run_event_page(run.run_id, before_event_id=tail["before_event_id"], limit=20)
    assert [event["message"] for event in older["events"]] == list(map(str,range(1210,1230)))
    store.append_run_event(run.run_id, event_type="log", message="after-1250")
    assert store.run_event_page(run.run_id, after_event_id=cursor)["events"][0]["message"] == "after-1250"


def test_execution_drafts_survive_store_restart_and_reject_stale_tabs(store):
    execution = RunExecutionOptions(backend="slurm", node="node02", cpus=8)
    assert store.execution_draft("wf")["execution"] is None
    saved = store.save_execution_draft("wf", 1, 0, execution)
    assert saved["draft_revision"] == 1
    with pytest.raises(ValueError, match="其他页面"):
        store.save_execution_draft("wf", 1, 0, RunExecutionOptions())
    fresh = LocalStore(store.database_path)
    assert fresh.execution_draft("wf")["execution"]["node"] == "node02"
    with fresh._connect() as connection:
        connection.execute("UPDATE workflows SET revision=2")
    with pytest.raises(ValueError, match="流程已更新"):
        fresh.save_execution_draft("wf", 1, 1, execution)


def wait_operation(service, identifier):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        value = service.get(identifier)
        if value["status"] in {"failed","cancelled","succeeded"}:
            return value
        time.sleep(.01)
    raise AssertionError("Background operation did not finish")


def test_background_checks_deduplicate_report_and_cancel_without_blocking_other_reads(store):
    service = OperationService(store)
    entered, release = Event(), Event()
    def check():
        checkpoint("checking")
        entered.set()
        assert release.wait(3)
        checkpoint("finishing")
        return {"done": True}
    first = service.submit("inspect","dataset",check)
    try:
        assert entered.wait(2)
        assert service.submit("inspect","dataset",check)["operation_id"] == first["operation_id"]
        assert store.list_runs(summary=True) == []
        assert service.cancel(first["operation_id"])["status"] == "cancel_requested"
        release.set()
        assert wait_operation(service,first["operation_id"])["status"] == "cancelled"
    finally:
        release.set()
        service.shutdown()


def test_check_restart_marks_only_unfinished_records_and_expiry_keeps_active_audit(store):
    service = OperationService(store)
    first = service.submit("inspect","one",lambda: {"done": 1})
    assert wait_operation(service,first["operation_id"])["result"] == {"done": 1}
    service.shutdown()
    with store._connect() as connection:
        connection.execute("UPDATE operations SET updated_at='2000-01-01T00:00:00+00:00'")
        connection.execute("INSERT INTO operations VALUES('unfinished','inspect','two','running','waiting',NULL,NULL,?,?)",(NOW,NOW))
    next_service = OperationService(store)
    try:
        assert next_service.get("unfinished")["status"] == "failed"
        assert expire_operations(store.database_path) == 1
        assert next_service.get("unfinished")["status"] == "failed"
    finally:
        next_service.shutdown()


def test_paged_conversations_keep_order_and_context_outside_visible_page(store):
    service = ConversationService(store,ChatService())
    conversation = service.create("synthetic")
    for index in range(105):
        metadata = {"workflow_id":"wf","dataset_id":"old"} if index == 0 else {"dataset_id":None} if index == 100 else {"workflow_id":None}
        store.append_conversation_message(conversation.conversation_id,role="user",content=str(index),
            estimated_tokens=1,metadata=metadata,created_at=NOW)
    page = service.snapshot(conversation.conversation_id,limit=40)
    assert len(page.messages) == 40 and page.has_more and page.recent_messages == []
    assert page.messages[0].content == "65" and page.messages[-1].content == "104"
    assert page.context["workflow_id"] == "wf" and page.context["dataset_id"] is None
    older = service.snapshot(conversation.conversation_id,limit=40,before_sequence=page.before_sequence)
    oldest = service.snapshot(conversation.conversation_id,limit=40,before_sequence=older.before_sequence)
    assert len(oldest.messages) == 25 and not oldest.has_more
    assert [int(message.content) for message in [*oldest.messages,*older.messages,*page.messages]] == list(range(105))


def test_live_wal_backup_restores_to_new_file_and_refuses_overwrite_or_tampering(store,tmp_path):
    store.save_run(new_run())
    directory = tmp_path / "backups"
    # Keep an uncommitted writer open: the backup must contain a consistent committed snapshot.
    with closing(sqlite3.connect(store.database_path)) as writer:
        writer.execute("UPDATE workflows SET revision=2")
        manifest = backup_database(store.database_path,directory)
        writer.rollback()
    backup = directory / manifest["file"]
    destination = tmp_path / "restored.db"
    restore_database(backup,destination)
    with closing(sqlite3.connect(destination)) as connection:
        assert connection.execute("SELECT revision FROM workflows").fetchone()[0] == 1
    with pytest.raises(FileExistsError):
        restore_database(backup,store.database_path)
    with backup.open("ab") as stream:
        stream.write(b"tampered")
    with pytest.raises(ValueError,match="checksum"):
        verify_backup(backup)


def test_retention_preserves_unknown_files_and_latest_two_verified_backups(store,tmp_path):
    directory = tmp_path / "backups"
    names = [backup_database(store.database_path,directory)["file"] for _ in range(3)]
    research = directory / "research.sqlite3"
    research.write_bytes(b"keep")
    assert retain_backups(directory,keep=2) == [names[0]]
    assert (directory / names[0]).exists()
    retain_backups(directory,keep=2,apply=True)
    assert not (directory / names[0]).exists() and research.read_bytes() == b"keep"
    assert all((directory / name).exists() for name in names[1:])


@pytest.fixture
def retry_fixture():
    fixture = slurm_fixtures.SlurmRunServiceTests()
    fixture.setUp()
    try:
        source = fixture.dataset_root / "input.txt"
        source.write_text("original")
        workflow = fixture.workflow
        workflow.steps[0].input_bindings["source"] = InputBinding(
            slot_id="source",artifact_type="test_file",source="existing_path",absolute_path=str(source))
        second = workflow.steps[0].model_copy(deep=True)
        second.id, second.order, second.depends_on = "second",2,[workflow.steps[0].id]
        workflow.steps.append(second)
        fixture.store.save_workflow(workflow,event_type="fixture")
        def execute(context,options,state,on_job):
            if context.step.id == "second":
                raise RuntimeError("synthetic failure")
            return successful_artifact(context)
        fixture.backend.execute.side_effect = execute
        original = fixture._finished(fixture._create().run_id)
        assert original.status == "failed" and original.steps[0].status == "succeeded"
        fixture.backend.execute.side_effect = lambda context,*args: successful_artifact(context)
        fixture.backend.execute.reset_mock()
        yield fixture,source,original
    finally:
        fixture.tearDown()
        fixture.doCleanups()


def successful_artifact(context):
    context.output_directory.mkdir(parents=True,exist_ok=True)
    path = context.output_directory / "result.txt"
    path.write_text("verified")
    return RunnerResult([RunArtifact(artifact_id=context.run_id+context.step.id,step_id=context.step.id,
        artifact_type="synthetic",label="test",absolute_path=str(path),relative_path=path.name,
        size_bytes=path.stat().st_size,sha256=context.file_sha256(path))],"done")


def test_retry_keeps_original_and_reuses_verified_successful_ancestor(retry_fixture):
    fixture,source,original = retry_fixture
    retried = fixture._finished(fixture.service.retry(original.run_id,["second"]).run_id)
    assert retried.status == "succeeded" and retried.retry_of == original.run_id
    assert retried.reused_step_ids == ["inspect"]
    assert [call.args[0].step.id for call in fixture.backend.execute.call_args_list] == ["second"]
    assert fixture.store.get_run(original.run_id).status == "failed"
    assert Path(original.steps[0].artifacts[0].absolute_path).exists()


@pytest.mark.parametrize("change",["input","output","implementation"])
def test_retry_reruns_ancestors_if_evidence_changes(retry_fixture,change):
    fixture,source,original = retry_fixture
    if change == "input":
        source.write_text("changed!")
    elif change == "output":
        Path(original.steps[0].artifacts[0].absolute_path).write_text("tampered")
    else:
        original.steps[0].implementation_fingerprint = "old-code"
        fixture.store.save_run(original)
    retried = fixture._finished(fixture.service.retry(original.run_id).run_id)
    assert retried.status == "succeeded" and not retried.reused_step_ids
    assert [call.args[0].step.id for call in fixture.backend.execute.call_args_list] == ["inspect","second"]


def test_retry_refuses_uncertain_remote_jobs(retry_fixture):
    fixture,source,original = retry_fixture
    original.steps[1].scheduler = {"job_id":"42","state":"RUNNING"}
    fixture.store.save_run(original)
    with pytest.raises(RunConflict,match="状态不确定"):
        fixture.service.retry(original.run_id)
    fixture.backend.execute.assert_not_called()
