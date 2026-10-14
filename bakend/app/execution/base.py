from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import Any, Callable, Protocol

from ..run_models import RunArtifact
from ..workflow_models import MethodOption, Workflow, WorkflowStep


class ExecutionCancelled(RuntimeError):
    pass


class ExecutionDetached(RuntimeError):
    """The API worker stopped observing a durable remote job during shutdown."""


LogCallback = Callable[[str, float | None, str], None]


@dataclass(frozen=True)
class RunnerContext:
    run_id: str
    workflow: Workflow
    step: WorkflowStep
    method: MethodOption
    output_directory: Path
    cancel_event: Event
    log: LogCallback
    detach_event: Event | None = None
    remote_gpu_available: bool = False
    is_dry_run: bool = False
    quality_checks: list[dict] = field(default_factory=list)
    input_checks: dict = field(default_factory=dict)
    prepared_options: dict = field(default_factory=dict)

    def input_is_deferred(self, slot_id: str) -> bool:
        """Only a dry-run may inspect a graph before its upstream files exist."""
        binding = self.step.input_bindings.get(slot_id)
        return bool(
            self.is_dry_run
            and binding is not None
            and binding.source == "upstream_output"
            and binding.upstream_step_id
            and binding.output_slot_id
        )

    def raise_if_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise ExecutionCancelled("任务已由用户取消")

    def file_sha256(self, path: Path) -> str:
        """共用分块校验逻辑，读取大文件时仍能响应取消，不一次性载入内存。"""
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                self.raise_if_cancelled()
                digest.update(chunk)
        return digest.hexdigest()


@dataclass(frozen=True)
class RunnerResult:
    artifacts: list[RunArtifact]
    message: str


class TrustedRunner(Protocol):
    runner_id: str
    label: str

    def runtime_status(self) -> dict[str, Any]: ...

    def preflight(self, context: RunnerContext) -> list[str]: ...

    def run(self, context: RunnerContext) -> RunnerResult: ...
