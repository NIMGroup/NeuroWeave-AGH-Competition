from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.capability_registry import CapabilityRegistry
from app.library_routes import library_router
from app.local_store import LocalStore
from app.pipeline_library import PipelineLibrary
from app.pipeline_models import ComposeRequest, DraftSave


@pytest.fixture
def library(tmp_path):
    return PipelineLibrary(LocalStore(tmp_path / "drafts.db"),
                           CapabilityRegistry(Path(__file__).resolve().parents[2] / "skills"))


def draft(revision=0, name="Test draft"):
    return DraftSave(expected_revision=revision, editor={"name": name, "description": "", "steps": []})


def test_deletion_is_recoverable_and_preserves_published_versions(library):
    published = library.save(ComposeRequest(name="Published", steps=[dict(id="one", method_id="mrtrix.command.mrconvert")]))
    library.save_draft("owned", draft())
    library.delete_draft("owned", draft(1))
    assert library.drafts() == []
    trash = library.draft_trash()
    assert trash["deleted_ids"] == ["owned"]
    assert trash["deleted_items"][0]["editor"]["name"] == "Test draft"
    assert library.get(published["id"])["version"] == "1"
    restored = library.restore_draft("owned", 1)
    assert restored["revision"] == 2
    assert library.drafts()[0]["editor"]["name"] == "Test draft"
    assert library.draft_trash()["deleted_ids"] == []


def test_stale_delete_or_autosave_cannot_destroy_or_resurrect_a_draft(library):
    library.save_draft("owned", draft())
    library.save_draft("owned", draft(1, "Newer content"))
    with pytest.raises(ValueError):
        library.delete_draft("owned", draft(1))
    assert library.drafts()[0]["editor"]["name"] == "Newer content"
    library.delete_draft("owned", draft(2, "Newer content"))
    with pytest.raises(ValueError, match="草稿已删除"):
        library.save_draft("owned", draft(2))
    assert library.drafts() == []
    library.restore_draft("owned", 2)
    with pytest.raises(ValueError):
        library.save_draft("owned", draft(2))
    library.save_draft("owned", draft(3, "Edited after restore"))
    assert library.drafts()[0]["revision"] == 4


def test_local_only_recovery_can_be_deleted_and_restored(library):
    library.delete_draft("browser-only", draft())
    assert library.drafts() == []
    assert library.draft_trash()["deleted_ids"] == ["browser-only"]
    with pytest.raises(ValueError):
        library.save_draft("browser-only", draft())
    library.restore_draft("browser-only", 0)
    assert library.drafts()[0]["revision"] == 1


def test_http_conflicts_validation_and_restore(library):
    app = FastAPI()
    app.include_router(library_router(library, None, None, None))
    with TestClient(app) as client:
        body = draft().model_dump()
        assert client.put("/api/library/drafts/owned", json=body).status_code == 200
        assert client.request("DELETE", "/api/library/drafts/owned", json=body).status_code == 409
        body["expected_revision"] = 1
        assert client.request("DELETE", "/api/library/drafts/owned", json=body).status_code == 200
        assert client.get("/api/library/drafts").json()["deleted_ids"] == ["owned"]
        rejected = client.put("/api/library/drafts/owned", json=body)
        assert rejected.status_code == 409 and rejected.json()["detail"]["code"] == "draft_deleted"
        assert client.post("/api/library/drafts/owned/restore", json={"expected_revision": 0}).status_code == 409
        assert client.post("/api/library/drafts/owned/restore", json={"expected_revision": 1}).status_code == 200
        assert client.post("/api/library/drafts/missing/restore", json={"expected_revision": 0}).status_code == 404
        assert client.request("DELETE", "/api/library/drafts/bad!", json=body).status_code == 422
