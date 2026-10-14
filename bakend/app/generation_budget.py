"""Per-turn, bounded recovery for incomplete provider output; never run grants."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json


requested_generation_tokens = ContextVar("requested_generation_tokens", default=None)


@contextmanager
def generation_limit(tokens):
    token = requested_generation_tokens.set(tokens)
    try:
        yield
    finally:
        requested_generation_tokens.reset(token)


class GenerationRecovery:
    def __init__(self, chat):
        self.maximum = getattr(chat, "generation_max_tokens", 32768)
        initial = getattr(chat, "assistant_max_tokens", 2400)
        if getattr(chat, "thinking", "enabled") == "enabled":
            initial = max(initial, getattr(chat, "thinking_max_tokens", 8192))
        self.current = min(initial, self.maximum)
        self.consecutive = self.total = 0
        self.last = None

    def truncated(self, answer):
        self.consecutive += 1
        self.total += 1
        usage = answer.get("usage") or {}
        output, reasoning = usage.get("output_tokens"), usage.get("reasoning_tokens")
        kind = "thinking" if output and output == reasoning else "tool_arguments" if answer.get("tool_calls") else "reply"
        previous = self.current
        self.current = min(self.maximum, max(previous + 4096, previous * 2))
        calls = answer.get('tool_calls') or []
        partial = json.dumps({k: answer.get(k) for k in ('content', 'tool_calls')}, ensure_ascii=False)
        self.last = {"finish_reason": "length", "kind": kind, "requested_tokens": previous,
                     "next_tokens": self.current, "maximum_tokens": self.maximum,
                     "output_tokens": output, "reasoning_tokens": reasoning,
                     "consecutive": self.consecutive, "total": self.total,
                     "content_chars": len(answer.get('content') or ''),
                     "tool_arguments": [{'name': c.get('function', {}).get('name'),
                         'chars': len(c.get('function', {}).get('arguments') or '')} for c in calls[:12]],
                     "partial_output_sha256": hashlib.sha256(partial.encode()).hexdigest()}
        return self.consecutive < 3 and self.total < 6

    def succeeded(self):
        self.consecutive = 0
