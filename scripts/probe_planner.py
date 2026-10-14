"""Run read-only task-routing probes without creating conversations or workflows."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "bakend"))

from app.main import app  # noqa: E402
from app.models import ChatRequest  # noqa: E402

DEFAULT_MESSAGES = [
    "我只需要做纤维追踪",
    "我想用FreeSurfer对T1图像做组织分割",
    "请检查这个数据集的结构和元数据",
]


async def probe(message: str) -> dict[str, object]:
    decision = await app.state.services.workflow_service.assess_task(
        ChatRequest(message=message, interaction_mode="task")
    )
    return {
        "message": message,
        "capability_ids": decision.get("capability_ids", []),
        "decision_source": decision.get("decision_source", ""),
        "clarification_required": decision.get("clarification_required", False),
    }


async def main(messages: list[str]) -> None:
    for message in messages:
        print(json.dumps(await probe(message), ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("messages", nargs="*", help="Task messages to route")
    arguments = parser.parse_args()
    asyncio.run(main(arguments.messages or DEFAULT_MESSAGES))
