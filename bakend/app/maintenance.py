"""Online SQLite backups and read-only storage inventory.

Research inputs, run outputs and scheduler evidence are never removed here.
Only verified backups made by this module and expired background-check results
have explicit retention operations.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from uuid import uuid4

BACKUP_NAME = re.compile(r"nw-db-\d{8}T\d{12}Z-[0-9a-f]{8}\.sqlite3")
AUDIT_TABLES = ("conversations", "conversation_messages", "datasets", "workflows",
                "runs", "run_steps", "run_artifact_order", "run_events", "execution_drafts")


def readonly(database: Path):
    if not database.is_file() or database.is_symlink():
        raise ValueError("Database must be an existing regular file")
    return sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=15)


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def check_database(database: Path) -> dict:
    with closing(readonly(database)) as connection:
        if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("SQLite integrity check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("SQLite foreign key check failed")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return {name: connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                for name in AUDIT_TABLES if name in tables}


def copy_database(source: Path, destination: Path) -> None:
    # Exclusive creation prevents overwriting a production DB or an existing backup.
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    deadline = time.monotonic() + 180
    def progress(_status, _remaining, _total):
        if time.monotonic() > deadline:
            raise TimeoutError("Database backup exceeded 180 seconds")
    source_connection = readonly(source)
    target_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(target_connection, pages=256, progress=progress, sleep=0.05)
    finally:
        target_connection.close()
        source_connection.close()


def backup_database(database: Path, directory: Path) -> dict:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink() or directory.resolve() != directory.absolute():
        raise ValueError("Backup directory must not be redirected by symlinks")
    os.chmod(directory, 0o700)
    name = "nw-db-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8] + ".sqlite3"
    final = directory / name
    partial = directory / (name + ".partial")
    copy_database(database, partial)
    counts = check_database(partial)
    # A real restore into an isolated file catches unusable backup artifacts.
    with tempfile.TemporaryDirectory(prefix="nw-restore-", dir=directory) as temporary:
        restored = Path(temporary) / "restored.sqlite3"
        copy_database(partial, restored)
        if check_database(restored) != counts:
            raise ValueError("Restore verification returned different table counts")
    os.replace(partial, final)
    manifest = {"format": 1, "file": name, "source": str(database.resolve()), "sha256": digest(final),
                "bytes": final.stat().st_size, "created_at": datetime.now(timezone.utc).isoformat(),
                "counts": counts, "restore_verified": True}
    with (directory / (name + ".json")).open("x", encoding="utf-8") as stream:
        os.chmod(stream.name, 0o600)
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
    return manifest


def verify_backup(path: Path) -> dict:
    manifest_path = path.with_name(path.name + ".json")
    if not BACKUP_NAME.fullmatch(path.name) or path.is_symlink() or manifest_path.is_symlink():
        raise ValueError("Not a NeuroWeave backup")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("format") != 1 or manifest.get("file") != path.name or
            manifest.get("sha256") != digest(path) or not manifest.get("restore_verified")):
        raise ValueError("Backup manifest or checksum mismatch")
    if check_database(path) != manifest.get("counts"):
        raise ValueError("Backup table counts differ from the verified manifest")
    return manifest


def retain_backups(directory: Path, keep: int = 14, *, apply: bool = False) -> list[str]:
    if keep < 2:
        raise ValueError("At least two verified backups must be kept")
    directory = directory.absolute()
    if directory.resolve() != directory or directory.is_symlink():
        raise ValueError("Backup directory is redirected")
    # Unknown, incomplete or corrupt files stop retention instead of being deleted.
    backups = sorted((p for p in directory.glob("nw-db-*.sqlite3") if BACKUP_NAME.fullmatch(p.name)), reverse=True)
    for path in backups:
        verify_backup(path)
    expired = backups[keep:]
    if apply:
        for path in expired:
            verify_backup(path)
            path.unlink()
            path.with_name(path.name + ".json").unlink()
    return [path.name for path in expired]


def restore_database(backup: Path, destination: Path) -> dict:
    manifest = verify_backup(backup)
    copy_database(backup, destination)  # Refuses an existing file, including production.
    if check_database(destination) != manifest["counts"]:
        raise ValueError("Restored database failed verification")
    return {"destination": str(destination), "counts": manifest["counts"]}


def expire_operations(database: Path, days: int = 14) -> int:
    if days < 1:
        raise ValueError("Completed checks must be kept for at least one day")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with sqlite3.connect(database, timeout=15) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='operations'").fetchone():
            return 0
        cursor = connection.execute("""DELETE FROM operations WHERE status IN ('succeeded','failed','cancelled')
            AND updated_at<?""", (cutoff,))
        return cursor.rowcount


def storage_status(database: Path, backups: Path, roots: list[Path]) -> dict:
    volumes = []
    for root in dict.fromkeys([database.parent, *roots]):
        if not root.is_dir():
            continue
        usage = shutil.disk_usage(root)
        volumes.append({"path": str(root), "total_bytes": usage.total, "free_bytes": usage.free,
                        "low_space": usage.free < max(10 * 1024**3, usage.total * 0.05)})
    manifests = sorted(backups.glob("nw-db-*.sqlite3.json"), reverse=True) if backups.is_dir() else []
    latest = None
    for path in manifests[:1]:
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            latest = {key: item.get(key) for key in ("created_at", "bytes", "restore_verified")}
        except (ValueError, OSError):
            latest = {"error": "Backup manifest could not be read"}
    return {"database_bytes": sum(p.stat().st_size for p in
             [database, Path(str(database) + "-wal"), Path(str(database) + "-shm")] if p.is_file()),
            "volumes": volumes, "latest_backup": latest, "backup_count": len(manifests),
            "retention": "数据库备份保留最近 14 份；后台检查结果保留 14 天；科研输入、输出和运行审计不自动删除"}


def inventory_outputs(database: Path, roots: list[Path]) -> dict:
    from .operation_context import checkpoint
    totals = {"files": 0, "bytes": 0, "unreadable": 0, "symlinks_skipped": 0}
    largest = []
    allowed = [root.resolve() for root in roots]
    with closing(readonly(database)) as connection:
        rows = connection.execute("SELECT run_id,status,payload_json FROM runs").fetchall()
    for run_id, status, payload in rows:
        raw = Path(json.loads(payload)["output_directory"])
        checkpoint("正在统计运行目录占用")
        if not raw.is_absolute() or raw.is_symlink() or raw.resolve().parent not in allowed:
            continue
        size = count = 0
        def failed(_error):
            totals["unreadable"] += 1
        for folder, directories, files in os.walk(raw, followlinks=False, onerror=failed):
            checkpoint()
            retained = [name for name in directories if not (Path(folder) / name).is_symlink()]
            totals["symlinks_skipped"] += len(directories) - len(retained)
            directories[:] = retained
            for name in files:
                path = Path(folder) / name
                try:
                    if path.is_symlink():
                        totals["symlinks_skipped"] += 1
                        continue
                    size += path.stat().st_size
                    count += 1
                except OSError:
                    totals["unreadable"] += 1
        totals["bytes"] += size
        totals["files"] += count
        largest.append({"run_id": run_id, "status": status, "bytes": size, "files": count})
    return {"totals": totals, "largest_runs": sorted(largest, key=lambda item: item["bytes"], reverse=True)[:20],
            "run_count": len(largest), "automatic_output_deletion": False}
