import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from types import SimpleNamespace

from app.api.workflows import router_for
from app.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    store = LocalStore(tmp_path / "tasks.db")
    with store.transaction() as db:
        for index, (source, states) in enumerate([
            ("pipeline-library", []), ("llm", []),
            ("pipeline-library", ["failed", "running"]),
            ("llm", ["succeeded"]), ("pipeline-library", ["failed"]),
            ("llm", ["cancel_requested"]), ("pipeline-library", ["cancelled"]),
        ]):
            identity = f"wf-{index}"
            payload = {"title": f"Task {index}", "planner_source": source,
                       "dataset": {"display_name": "Input"}, "workspace_root_path": "/output"}
            db.execute("INSERT INTO workflows (workflow_id,dataset_id,status,revision,payload_json,created_at,updated_at) VALUES (?,NULL,?,1,?,?,?)",
                       (identity, "confirmed", json.dumps(payload), "2026-01-01", f"2026-01-{index+1:02}"))
            for run_index, status in enumerate(states):
                created = f"2026-02-{run_index+1:02}"
                db.execute("INSERT INTO runs (run_id,workflow_id,workflow_revision,status,payload_json,created_at,updated_at) VALUES (?,?,1,?,?,?,?)",
                           (f"run-{index}-{run_index}", identity, status, "{}", created, created))
    return store


def test_index_combines_chat_and_standalone_pipeline_tasks_with_context(store):
    items = store.workflow_index()["items"]
    assert len(items) == 7
    assert items[0]["workflow_id"] == "wf-2", "recent run activity should surface its task"
    assert {item["source"] for item in items} == {"pipeline", "conversation"}
    assert all(item["dataset_label"] == "Input" and item["workspace_path"] == "/output" for item in items)
    repeated = next(item for item in items if item["workflow_id"] == "wf-2")
    assert repeated["run_count"] == 2
    assert repeated["latest_run_status"] == "running"


@pytest.mark.parametrize("status,identities", [
    ("draft", {"wf-0", "wf-1"}), ("active", {"wf-2", "wf-5"}),
    ("succeeded", {"wf-3"}), ("failed", {"wf-4"}), ("cancelled", {"wf-6"}),
])
def test_filter_uses_latest_run_and_includes_queued_cancellations(store, status, identities):
    assert {item["workflow_id"] for item in store.workflow_index(status=status)["items"]} == identities


def test_filtering_precedes_pagination_and_query_remains_literal(store):
    first = store.workflow_index(source="pipeline", status="draft", limit=1)
    assert first["items"][0]["workflow_id"] == "wf-0"
    assert not first["has_more"]
    first = store.workflow_index(source="conversation", limit=1)
    second = store.workflow_index(source="conversation", limit=1, offset=1)
    assert first["has_more"] and second["has_more"]
    assert first["items"][0]["workflow_id"] != second["items"][0]["workflow_id"]
    assert store.workflow_index(query="%' OR 1=1 --")["items"] == []
    assert len(store.workflow_index(query="task 2")["items"]) == 1


def test_http_task_filters_reject_unknown_values(store):
    app = FastAPI()
    app.include_router(router_for(SimpleNamespace(local_store=store)))
    with TestClient(app) as client:
        response = client.get("/api/workflow-index?source=pipeline&status=active&limit=1")
        assert response.status_code == 200
        assert response.json()["items"][0]["workflow_id"] == "wf-2"
        for query in ["source=other", "status=other", "offset=-1", "limit=101"]:
            assert client.get("/api/workflow-index?" + query).status_code == 422
