"""Draft recovery and lifecycle review, composed with version storage and a pure compiler."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from .pipeline_models import ComposeRequest, ComposeStep, InputPort


class DraftDeletedError(ValueError):
    pass


class PipelineAuthoring:
    def __init__(self, repository, compiler):
        self.repository, self.compiler = repository, compiler
        self.store, self.registry = repository.store, repository.registry

    def drafts(self):
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT id,revision,payload,updated_at FROM pipeline_drafts ORDER BY updated_at DESC LIMIT 100"
            ).fetchall()
        return [
            dict(
                id=r["id"],
                revision=r["revision"],
                updated_at=r["updated_at"],
                **json.loads(r["payload"]),
            )
            for r in rows
        ]

    def draft_trash(self):
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT * FROM pipeline_draft_trash ORDER BY deleted_at DESC"
            ).fetchall()
        return {
            "deleted_ids": [r["id"] for r in rows],
            "deleted_items": [dict(id=r["id"], revision=r["revision"],
                                   deleted_at=r["deleted_at"], **json.loads(r["payload"]))
                              for r in rows[:100]],
        }

    def _draft_payload(self, request):
        editor = request.editor
        if set(editor) - {
            "name",
            "description",
            "steps",
            "shared_inputs",
            "expected_version",
            "controls",
        }:
            raise ValueError("Unknown draft fields")
        if (
            not isinstance(editor.get("name", ""), str)
            or len(editor.get("name", "")) > 160
            or not isinstance(editor.get("description", ""), str)
            or len(editor.get("description", "")) > 4000
        ):
            raise ValueError("Invalid draft name or description")
        if not isinstance(editor.get("steps"), list) or len(editor["steps"]) > 50:
            raise ValueError("A draft allows up to 50 steps")
        # Shape/size is validated, but incomplete or temporarily invalid graphs remain saveable.
        import math

        for step in editor["steps"]:
            parsed = ComposeStep.model_validate(step)
            if parsed.position and (
                set(parsed.position) != {"x", "y"}
                or any(
                    not math.isfinite(v) or abs(v) > 1_000_000
                    for v in parsed.position.values()
                )
            ):
                raise ValueError("Invalid canvas coordinates")
        from .workflow_models import ParameterSpec
        controls = editor.get("controls", [])
        if not isinstance(controls, list) or len(controls) > 50:
            raise ValueError("Too many pipeline controls")
        for control in controls:
            ParameterSpec.model_validate(control)
        groups = editor.get("shared_inputs", [])
        if not isinstance(groups, list) or len(groups) > 500:
            raise ValueError("Too many shared input groups")
        for group in groups:
            if not isinstance(group, list) or len(group) > 500:
                raise ValueError("Invalid shared input group")
            for port in group:
                InputPort.model_validate(port)
        payload = json.dumps(
            dict(
                editor=editor,
                pipeline_id=request.pipeline_id,
                pipeline_version=request.pipeline_version,
            ),
            ensure_ascii=False,
        )
        if len(payload) > 1_000_000:
            raise ValueError("Draft is too large")
        return payload

    def save_draft(self, identity, request):
        payload = self._draft_payload(request)
        with self.store.transaction() as db:
            if db.execute("SELECT 1 FROM pipeline_draft_trash WHERE id=?", (identity,)).fetchone():
                raise DraftDeletedError("草稿已删除，请从已删除草稿中恢复后再编辑。")
            row = db.execute(
                "SELECT revision FROM pipeline_drafts WHERE id=?", (identity,)
            ).fetchone()
            current = row[0] if row else 0
            if current != request.expected_revision:
                raise ValueError(
                    "草稿已在其他窗口更新，请重新打开后比较；当前修改仍保存在浏览器。"
                )
            now = datetime.now(timezone.utc).isoformat()
            db.execute(
                "INSERT INTO pipeline_drafts VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload,updated_at=excluded.updated_at",
                (identity, current + 1, payload, now),
            )
        return dict(id=identity, revision=current + 1, updated_at=now)

    def delete_draft(self, identity, request):
        recovery = self._draft_payload(request)
        with self.store.transaction() as db:
            deleted = db.execute("SELECT revision FROM pipeline_draft_trash WHERE id=?", (identity,)).fetchone()
            if deleted:
                if deleted[0] != request.expected_revision:
                    raise ValueError("草稿版本已变化，请刷新后再删除。")
                return dict(id=identity, deleted=True)
            row = db.execute("SELECT revision,payload FROM pipeline_drafts WHERE id=?", (identity,)).fetchone()
            current = row["revision"] if row else 0
            if current != request.expected_revision:
                raise ValueError("草稿已在其他窗口更新，请刷新后再删除。")
            db.execute("INSERT INTO pipeline_draft_trash VALUES(?,?,?,?)",
                       (identity, current, recovery, datetime.now(timezone.utc).isoformat()))
            db.execute("DELETE FROM pipeline_drafts WHERE id=?", (identity,))
        return dict(id=identity, deleted=True)

    def restore_draft(self, identity, expected_revision):
        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM pipeline_draft_trash WHERE id=?", (identity,)).fetchone()
            if not row:
                raise KeyError("已删除草稿不存在")
            if row["revision"] != expected_revision:
                raise ValueError("草稿版本已变化，请刷新后再恢复。")
            now = datetime.now(timezone.utc).isoformat()
            revision = row["revision"] + 1
            db.execute("INSERT INTO pipeline_drafts VALUES(?,?,?,?)", (identity, revision, row["payload"], now))
            db.execute("DELETE FROM pipeline_draft_trash WHERE id=?", (identity,))
        return dict(id=identity, revision=revision, updated_at=now)

    def lifecycle(self, identity, version, status):
        if status not in {"published", "archived"}:
            raise ValueError("Invalid lifecycle status")
        if not identity.startswith("user-"):
            raise ValueError("Only user pipelines can be archived")
        self.repository.get(identity, version)
        with self.store.transaction() as db:
            self.repository.require_active(db, identity)
            db.execute(
                "INSERT INTO pipeline_states VALUES(?,?,?) ON CONFLICT(id,version) DO UPDATE SET status=excluded.status",
                (identity, version, status),
            )
        return dict(id=identity, version=version, status=status)

    def migration(self, identity, version=None):
        p = self.repository.get(identity, version)
        changes = []
        for s in p["steps"]:
            current = self.registry.definitions.method(
                s.get("method_ref", s.get("method_id"))
            )
            if s.get("method_version") != current.version:
                changes.append(
                    dict(
                        step_id=s["id"],
                        method_id=current.id,
                        from_version=s.get("method_version"),
                        to_version=current.version,
                        parameters=[
                            v.model_dump(mode="json") for v in current.parameters
                        ],
                        inputs=[v.model_dump(mode="json") for v in current.input_slots],
                        outputs=[
                            v.model_dump(mode="json") for v in current.output_slots
                        ],
                    )
                )
        editor = p.get("editor")
        errors = []
        if editor:
            try:
                self.compiler.compile(ComposeRequest.model_validate(editor))
            except (ValueError, KeyError) as exc:
                errors.append(str(exc))
        else:
            errors.append("This built-in pipeline has no editable source")
        return dict(
            id=identity,
            version=p["version"],
            changes=changes,
            errors=errors,
            editor=editor,
            requires_review=bool(changes),
            can_publish=bool(editor) and not errors,
        )
