"""Json Response policy; depends on the narrow model gateway, never on HTTP or storage."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import httpx
from pydantic import ValidationError

from .gateway import ModelGateway
from ..model_validation import ModelValidationError

logger = logging.getLogger(__name__)


async def json_response(
    client: ModelGateway, messages, *, stage, validate=None, max_tokens=2000
):
    """At most two attempts. Log error classes, never keys, prompts or file text."""
    from ..operation_context import OperationCancelled, checkpoint

    names = {
        "assistant_task": "理解当前需求",
        "assistant_verification": "核对方案与用户目标",
        "task_intent": "解析需求",
        "library_semantic_scan": "扩展检索方法",
        "method_graph": "构建并校验流程",
        "planning_advice": "整理可行建议",
        "pipeline_description": "生成 Pipeline 描述",
    }
    checkpoint(names.get(stage, "检索与评估方法"))
    diagnostics = []
    attempt_messages = list(messages)
    for attempt in range(1, 3):
        checkpoint()
        try:
            content = await client.complete(attempt_messages, max_tokens=max_tokens)
            checkpoint()
            payload = extract_json_object(content)
            if not isinstance(payload, dict):
                raise ValueError("Expected an object")
            if validate is not None:
                validate(payload)
            return payload, diagnostics
        except OperationCancelled:
            raise
        except (
            httpx.HTTPError,
            RuntimeError,
            KeyError,
            IndexError,
            TypeError,
            ValueError,
        ) as exc:
            detail = {
                "stage": stage,
                "attempt": attempt,
                "error_type": type(exc).__name__,
            }
            if isinstance(exc, httpx.HTTPStatusError):
                detail["http_status"] = exc.response.status_code
            if isinstance(exc, ValidationError):
                detail["schema_errors"] = [
                    {"location": list(error["loc"]), "type": error["type"]}
                    for error in exc.errors(
                        include_input=False, include_context=False, include_url=False
                    )
                ][:16]
                for error in exc.errors(include_input=False, include_url=False):
                    cause = error.get('ctx', {}).get('error')
                    if isinstance(cause, ModelValidationError):
                        for hint in cause.schema_errors:
                            location, nested = list(error['loc']), hint['location']
                            if nested and location[-len(nested):] == nested:
                                resolved = location
                            elif location and nested[:len(location)] != location:
                                resolved = location + nested
                            else:
                                resolved = nested or location
                            detail['schema_errors'].append({**hint, 'location': resolved})
            elif isinstance(exc, ModelValidationError):
                detail['schema_errors'] = exc.schema_errors
            diagnostics.append(detail)
            logger.warning(
                "planning_call_failed stage=%s attempt=%s error_type=%s http_status=%s",
                stage,
                attempt,
                detail["error_type"],
                detail.get("http_status"),
            )
            # Authentication/client errors will not improve with a second call.
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {
                400,
                401,
                403,
                404,
            }:
                break
            if attempt == 1:
                if isinstance(exc, (ValueError, TypeError, KeyError)):
                    # Repeating an identical prompt often repeats a schema
                    # mistake. Send only field locations/error codes, not raw
                    # response contents, paths or exception input values.
                    attempt_messages = [
                        *messages,
                        {
                            "role": "system",
                            "content": "Validation feedback, not a new user request. The previous response did not validate. Keep interpreting the original user message and its conversation context. Return only corrected JSON following the supplied schema. "
                            + json.dumps(
                                detail.get(
                                    "schema_errors",
                                    [{"type": "invalid_json_or_schema"}],
                                ),
                                ensure_ascii=False,
                            ),
                        },
                    ]
                await asyncio.sleep(0.1)
    return None, diagnostics


def extract_json_object(content: str) -> dict[str, Any]:
    cleaned = re.sub(
        r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.IGNORECASE
    )
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("No JSON object found")
    payload = json.loads(cleaned[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("Planner result must be an object")
    return payload
