"""Frontend-only promotion retains source and can roll back without backend writes."""
import hashlib
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "frontend_deployment", Path(__file__).resolve().parents[2] / "scripts/deploy_frontend_release.py"
)
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


def test_frontend_source_swap_and_rollback_preserve_old_files(tmp_path, monkeypatch):
    production, source, backup = [tmp_path / name for name in ("production", "release", "backup")]
    for directory in (production, source, backup):
        directory.mkdir()
    monkeypatch.setattr(deploy, "PRODUCTION", production)
    for directory in (production, source):
        (directory / "frontend").mkdir()
    (production / "frontend/local-notes.txt").write_text("keep local file")
    (source / "frontend/index.html").write_text("new frontend")
    backend = production / "bakend/app.py"
    backend.parent.mkdir()
    backend.write_text("running backend")
    for name in deploy.MANAGED_FILES:
        incoming = source / name
        incoming.parent.mkdir(parents=True, exist_ok=True)
        incoming.write_text("new " + name)
        if name != "deploy/frontend-migration.md":
            current = production / name
            current.parent.mkdir(parents=True, exist_ok=True)
            current.write_text("old " + name)
    deploy.install_source(source, backup)
    assert (production / "frontend/index.html").read_text() == "new frontend"
    assert (backup / "previous-frontend/local-notes.txt").read_text() == "keep local file"
    assert backend.read_text() == "running backend"
    deploy.restore_source(backup)
    assert (production / "frontend/local-notes.txt").read_text() == "keep local file"
    assert (production / "compose.yaml").read_text() == "old compose.yaml"
    assert not (production / "deploy/frontend-migration.md").exists()
    assert (backup / "added-files/deploy/frontend-migration.md").exists()
    assert (backup / "replaced-frontend/index.html").read_text() == "new frontend"
    assert backend.read_text() == "running backend"


def test_preflight_rejects_changed_or_unexpected_files(tmp_path):
    (tmp_path / "file.txt").write_bytes(b"original")
    manifest = {"file.txt": hashlib.sha256(b"original").hexdigest(), "new.txt": None}
    deploy.verify_files(tmp_path, manifest)
    (tmp_path / "file.txt").write_bytes(b"server edit")
    with pytest.raises(RuntimeError, match="File changed"):
        deploy.verify_files(tmp_path, manifest)
    (tmp_path / "new.txt").write_bytes(b"keep")
    with pytest.raises(RuntimeError, match="Unexpected server file"):
        deploy.verify_files(tmp_path, {"new.txt": None})


def test_preflight_rejects_parent_escape(tmp_path):
    with pytest.raises(RuntimeError, match="Unsafe manifest path"):
        deploy.verify_files(tmp_path, {"../outside": None})


def test_legacy_image_only_backup_can_be_restored(tmp_path):
    deploy.restore_source(tmp_path)
