from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from .workflow_models import SampleScope


RunStatus = Literal[
    "queued",
    "running",
    "cancel_requested",
    "cancelled",
    "succeeded",
    "failed",
]

RunStepStatus = Literal["queued", "running", "cancelled", "succeeded", "failed"]


class RunArtifact(BaseModel):
    artifact_id: str
    step_id: str
    artifact_type: str
    label: str
    absolute_path: str
    relative_path: str
    media_type: str = "application/octet-stream"
    size_bytes: int = 0
    sha256: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunStep(BaseModel):
    output_relative_path: str = ''
    step_id: str
    title: str
    runner_id: str
    method_id: str
    status: RunStepStatus = "queued"
    progress: float = 0.0
    message: str = "等待执行"
    artifacts: list[RunArtifact] = Field(default_factory=list)
    error_code: str = ""
    error_message: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    scheduler: dict[str, Any] = Field(default_factory=dict)
    sample_id: str | None = None
    sample_label: str = ""
    node_name: str | None = None
    input_fingerprint: dict[str, str] = Field(default_factory=dict)
    implementation_fingerprint: str = ""


class RunResources(BaseModel):
    cpus: int = Field(default=4, ge=1, le=128)
    memory_gb: int = Field(default=8, ge=1, le=512)
    gpus: int = Field(default=0, ge=0, le=1)
    time_minutes: int = Field(default=60, ge=1, le=1440)


class NodeAllocation(RunResources):
    node: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    parallel_samples: int = Field(default=1, ge=1, le=128, strict=True)


class RunExecutionOptions(RunResources):
    output_layout: Literal['legacy', 'sample_steps'] | None = None
    backend: Literal["local", "slurm"] = "local"
    partition: str = Field(default="all", pattern=r"^[A-Za-z0-9_-]{1,64}$")
    node: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    nodes: list[NodeAllocation] = Field(default_factory=list, max_length=32)
    sample_mode: Literal["single", "per_sample"] = "single"

    @model_validator(mode="after")
    def validate_nodes(self):
        names = [item.node for item in self.nodes]
        if len(set(names)) != len(names):
            raise ValueError("不能重复选择同一个节点")
        if self.node and self.nodes:
            raise ValueError("单节点与节点列表不能同时指定")
        if len(self.nodes) > 1 and self.sample_mode != "per_sample":
            raise ValueError("多节点分配需要按样本运行")
        if self.sample_mode != "per_sample" and any(item.parallel_samples != 1 for item in self.nodes):
            raise ValueError("样本并行数量仅适用于按样本运行")
        return self


class WorkflowRun(BaseModel):
    workspace_id: str | None = None
    workspace_root_path: str | None = None
    output_relative_path: str = "outputs"
    state_revision: int = 0
    retry_of: str | None = None
    reused_step_ids: list[str] = Field(default_factory=list)
    selected_step_ids: list[str] = Field(default_factory=list)
    run_id: str
    workflow_id: str
    workflow_revision: int
    status: RunStatus = "queued"
    progress: float = 0.0
    current_step_id: str | None = None
    output_directory: str
    plan_snapshot: dict[str, Any] = Field(default_factory=dict)
    sample_scope: SampleScope | None = None
    steps: list[RunStep] = Field(default_factory=list)
    artifacts: list[RunArtifact] = Field(default_factory=list)
    error_code: str = ""
    error_message: str = ""
    cancellation_requested: bool = False
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    updated_at: str
    execution: RunExecutionOptions = Field(default_factory=RunExecutionOptions)


class RunCreateRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    execution: RunExecutionOptions = Field(default_factory=RunExecutionOptions)


class ExecutionDraftRequest(RunCreateRequest):
    expected_draft_revision: int = Field(ge=0)


class OutputPreviewRequest(RunCreateRequest):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=2, ge=1, le=5)


class RunRetryRequest(BaseModel):
    step_ids: list[str] = Field(default_factory=list, max_length=10000)
    sample_ids: list[str] = Field(default_factory=list, max_length=10000)


class RunEvent(BaseModel):
    event_id: int
    event_type: str
    level: Literal["info", "warning", "error"] = "info"
    message: str = ""
    progress: float | None = None
    step_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str
