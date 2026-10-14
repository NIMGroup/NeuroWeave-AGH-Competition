from __future__ import annotations

"""Migrate structured paths from the Docker backend namespace to the host.

Run only while the Docker backend is stopped.  The script creates a consistent
SQLite backup before changing any rows and is idempotent.
"""

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


JSON_COLUMNS = {
    "datasets": "payload_json",
    "workflows": "payload_json",
    "workflow_events": "payload_json",
    "runs": "payload_json",
    "run_events": "payload_json",
    "conversation_messages": "metadata_json",
}


def root_id(path: str) -> str:
    return "root_" + hashlib.sha256(path.lower().encode("utf-8")).hexdigest()[:10]


def transform(value: Any, output_root: str, old_root_id: str, new_root_id: str) -> Any:
    if isinstance(value, dict):
        return {
            key: transform(item, output_root, old_root_id, new_root_id)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [transform(item, output_root, old_root_id, new_root_id) for item in value]
    if not isinstance(value, str):
        return value
    if value == old_root_id:
        return new_root_id
    if value == "/server":
        return "/"
    if value == "/outputs":
        return output_root
    value = value.replace("/server/", "/")
    value = value.replace("/outputs/", output_root.rstrip("/") + "/")
    return value


def migrate(database: Path, output_root: Path) -> dict[str, Any]:
    if not database.is_file():
        raise FileNotFoundError(f"数据库不存在：{database}")
    backup_directory = database.parent / "backups"
    backup_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = backup_directory / f"{database.stem}-before-native-{timestamp}{database.suffix}"

    source = sqlite3.connect(database, timeout=30)
    destination = sqlite3.connect(backup)
    try:
        source.backup(destination)
    finally:
        destination.close()

    old_root_id = root_id("/server")
    new_root_id = root_id("/")
    counts: dict[str, int] = {}
    try:
        source.execute("PRAGMA foreign_keys = ON")
        source.execute("BEGIN IMMEDIATE")
        for table, column in JSON_COLUMNS.items():
            changed = 0
            rows = source.execute(f"SELECT rowid, {column} FROM {table}").fetchall()
            for rowid, payload_text in rows:
                payload = json.loads(payload_text)
                migrated = transform(payload, str(output_root), old_root_id, new_root_id)
                if migrated == payload:
                    continue
                source.execute(
                    f"UPDATE {table} SET {column} = ? WHERE rowid = ?",
                    (json.dumps(migrated, ensure_ascii=False), rowid),
                )
                changed += 1
            counts[f"{table}.{column}"] = changed
        root_cursor = source.execute(
            "UPDATE datasets SET root_id = ? WHERE root_id = ?",
            (new_root_id, old_root_id),
        )
        counts["datasets.root_id"] = root_cursor.rowcount
        source.commit()
        integrity = source.execute("PRAGMA integrity_check").fetchone()[0]
    except Exception:
        source.rollback()
        raise
    finally:
        source.close()
    return {
        "database": str(database),
        "backup": str(backup),
        "old_root_id": old_root_id,
        "new_root_id": new_root_id,
        "updated_rows": counts,
        "integrity_check": integrity,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    arguments = parser.parse_args()
    result = migrate(arguments.database.resolve(), arguments.output_root.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
