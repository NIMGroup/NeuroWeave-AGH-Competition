"""Provider-reported token accounting, scoped to a conversation and physical request."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel


class TokenUsageSummary(BaseModel):
    calls: int = 0
    reported_calls: int = 0
    unavailable_calls: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_input_tokens: int | None = None
    complete: bool = False


FIELDS = ("input_tokens", "output_tokens", "total_tokens", "reasoning_tokens", "cached_input_tokens")


def normalize_usage(payload: Any) -> dict[str, int | None]:
    usage = payload.get("usage") if isinstance(payload, dict) else None
    usage = usage if isinstance(usage, dict) else {}

    def count(value):
        # Missing, malformed or negative counts must never become a measured zero.
        return value if type(value) is int and 0 <= value <= 2**63 - 1 else None

    def detail(key, field):
        value = usage.get(key)
        return count(value.get(field)) if isinstance(value, dict) else None

    incoming = count(usage.get("prompt_tokens", usage.get("input_tokens")))
    outgoing = count(usage.get("completion_tokens", usage.get("output_tokens")))
    total = count(usage.get("total_tokens"))
    if total is None and incoming is not None and outgoing is not None:
        total = count(incoming + outgoing)
    cached = detail("prompt_tokens_details", "cached_tokens")
    if cached is None:
        cached = count(usage.get("prompt_cache_hit_tokens"))
    reasoning = detail("completion_tokens_details", "reasoning_tokens")
    if incoming is not None and cached is not None and cached > incoming:
        cached = None
    if outgoing is not None and reasoning is not None and reasoning > outgoing:
        reasoning = None
    return dict(zip(FIELDS, (incoming, outgoing, total, reasoning, cached)))


class TokenUsageLedger:
    def __init__(self, store):
        self.store = store

    def start(self, conversation_id: str, turn_id: str | None, model: str, kind: str) -> str:
        identity = uuid4().hex
        with self.store._lock, self.store._connect() as db:
            db.execute(
                """INSERT INTO model_token_usage
                   (id,conversation_id,turn_id,model,kind,status,started_at)
                   VALUES(?,?,?,?,?,'pending',?)""",
                (identity, conversation_id, turn_id, model, kind, datetime.now(timezone.utc).isoformat()),
            )
        return identity

    def finish(self, identity: str, payload: Any, *, returned: bool) -> None:
        values = normalize_usage(payload)
        with self.store._lock, self.store._connect() as db:
            db.execute(
                """UPDATE model_token_usage SET status=?,input_tokens=?,output_tokens=?,
                   total_tokens=?,reasoning_tokens=?,cached_input_tokens=?,completed_at=?
                   WHERE id=? AND status='pending'""",
                ("returned" if returned else "failed", *values.values(),
                 datetime.now(timezone.utc).isoformat(), identity),
            )

    @staticmethod
    def _summary(row) -> TokenUsageSummary:
        calls, reported = row["calls"], row["reported_calls"] or 0
        return TokenUsageSummary(calls=calls, reported_calls=reported,
            unavailable_calls=calls - reported, complete=bool(calls and calls == reported),
            **{field: row[field] for field in FIELDS})

    @staticmethod
    def _columns() -> str:
        return """COUNT(*) AS calls,
            SUM(CASE WHEN input_tokens IS NOT NULL AND output_tokens IS NOT NULL
                 AND total_tokens IS NOT NULL THEN 1 ELSE 0 END) AS reported_calls,
            """ + ",".join(f"SUM({field}) AS {field}" for field in FIELDS)

    def summary(self, conversation_id: str, turn_id: str | None = None) -> TokenUsageSummary:
        self.store.get_conversation(conversation_id)  # Apply existing ownership rules.
        clause = " AND turn_id=?" if turn_id is not None else ""
        args = (conversation_id, turn_id) if turn_id is not None else (conversation_id,)
        with self.store._connect() as db:
            row = db.execute("SELECT " + self._columns()
                + " FROM model_token_usage WHERE conversation_id=?" + clause, args).fetchone()
        return self._summary(row)

    def turn_summaries(self, conversation_id: str, turn_ids: list[str]) -> dict[str, TokenUsageSummary]:
        self.store.get_conversation(conversation_id)
        if not turn_ids:
            return {}
        with self.store._connect() as db:
            rows = db.execute("SELECT turn_id," + self._columns()
                + " FROM model_token_usage WHERE conversation_id=? AND turn_id IN ("
                + ",".join("?" for _ in turn_ids) + ") GROUP BY turn_id",
                (conversation_id, *turn_ids)).fetchall()
        return {row["turn_id"]: self._summary(row) for row in rows}


@dataclass(frozen=True)
class UsageScope:
    ledger: TokenUsageLedger
    conversation_id: str
    turn_id: str | None

    def start(self, model: str, kind: str) -> str:
        return self.ledger.start(self.conversation_id, self.turn_id, model, kind)


current_usage: ContextVar[UsageScope | None] = ContextVar("model_token_usage", default=None)


@contextmanager
def usage_scope(ledger: TokenUsageLedger, conversation_id: str, turn_id: str | None = None,
                *, reuse_current: bool = False):
    current = current_usage.get()
    if reuse_current and current and current.ledger is ledger and current.conversation_id == conversation_id:
        yield current
        return
    token = current_usage.set(UsageScope(ledger, conversation_id, turn_id))
    try:
        yield current_usage.get()
    finally:
        current_usage.reset(token)
