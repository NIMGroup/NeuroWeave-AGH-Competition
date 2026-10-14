from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, PrivateAttr

from .conversation_models import ConversationMemoryStatus
from .token_usage import TokenUsageSummary
from .workflow_models import Workflow
from .task_contract import TaskContract
from .response_policy import ResponsePolicy


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=20_000)


class ChatRequest(BaseModel):
    _execution_feedback: dict = PrivateAttr(default_factory=dict)
    _prepared_workflow: Workflow | None = PrivateAttr(default=None)
    _workflow_content: str = PrivateAttr(default="")
    # Server-owned memory/provenance never accepted as client authority.
    _conversation_memory: dict = PrivateAttr(default_factory=dict)
    _user_sequence: int | None = PrivateAttr(default=None)
    _history_through_sequence: int | None = PrivateAttr(default=None)
    # Per-turn read evidence; never client input, persisted state or execution authority.
    _inventory_snapshots: dict = PrivateAttr(default_factory=dict)
    _registered_plan: Any = PrivateAttr(default=None)
    _workflow_input_bindings: list = PrivateAttr(default_factory=list)
    _input_selection: Literal['automatic', 'user'] = PrivateAttr(default='automatic')
    _active_plan_snapshot: Workflow | None = PrivateAttr(default=None)
    _plan_parameter_overrides: dict = PrivateAttr(default_factory=dict)
    assistant_task: dict[str, Any] = Field(default_factory=dict, exclude=True, repr=False)
    # Internal turn state is always replaced from server-side records in begin_turn.
    current_workflow_context: dict[str, Any] = Field(default_factory=dict, exclude=True)
    planning_handoff: bool = Field(default=False, exclude=True)
    response_policy: ResponsePolicy | None = Field(default=None, exclude=True)
    task_contract: TaskContract = Field(default_factory=TaskContract, exclude=True)
    workspace_id: str | None = None
    client_turn_id: str | None = Field(default=None, min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    workspace_context: str = Field(default="", exclude=True, repr=False)
    workspace_root_path: str | None = Field(default=None, exclude=True)
    message: str = Field(min_length=1, max_length=20_000)
    task_action: Literal['message','update_plan'] = 'message'
    history: list[Message] = Field(default_factory=list)
    conversation_id: str | None = None
    conversation_summary: str = Field(default="", exclude=True, repr=False)
    selected_skills: list[str] = Field(default_factory=list)
    dataset_id: str | None = None
    interaction_mode: Literal["chat", "task"] = Field(default="task", description="兼容旧客户端字段；聊天与任务统一进入助手，执行仍需手动运行。")
    language: str = "zh-CN"


class CapabilityRecommendationItem(BaseModel):
    capability_id: str
    label: str
    description: str = ""
    execution_level: Literal["advisory", "planning", "structured"]
    skill_ids: list[str] = Field(default_factory=list)
    tree_path: list[str] = Field(default_factory=list)


class CapabilityRecommendation(BaseModel):
    title: str
    summary: str
    plan_kind: Literal["atomic", "composite"]
    fully_executable: bool = False
    items: list[CapabilityRecommendationItem] = Field(default_factory=list)
    notice: str = (
        "推荐项来自当前 Skill、Method 和 Pipeline 目录；规划能力用于讨论，"
        "只有已登记 Method 及其受信任 Runner 才能执行。"
    )


class CapabilityRoutingLevel(BaseModel):
    level: int
    parent_ids: list[str] = Field(default_factory=list)
    candidate_count: int = 0
    payload_chars: int = 0
    selected_node_ids: list[str] = Field(default_factory=list)
    source: str = "model"


class CapabilityRoutingTrace(BaseModel):
    strategy: Literal["hierarchical", "heuristic", "semantic"] = "hierarchical"
    levels: list[CapabilityRoutingLevel] = Field(default_factory=list)
    candidates_seen: int = 0
    payload_chars: int = 0
    full_tree_payload_chars: int = 0
    payload_ratio: float = 0.0
    selected_paths: list[list[str]] = Field(default_factory=list)
    intent_source: str = ""
    normalized_task: str = ""
    response_policy: ResponsePolicy | None = None
    semantic_hints: list[str] = Field(default_factory=list)
    reused_input_capabilities: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    retrieval: dict[str, Any] = Field(default_factory=dict)
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    task_contract: TaskContract = Field(default_factory=TaskContract)


class ChatResponse(BaseModel):
    assistant_task: dict[str, Any] | None = None
    token_usage: TokenUsageSummary | None = None
    planning_offer: str | None = None
    workspace_id: str | None = None
    workspace_tool_events: list[dict[str, Any]] = Field(default_factory=list)
    type: Literal["done", "recommendation", "workflow"] = "done"
    content: str
    provider_used: str
    model_used: str
    mode: Literal["demo", "model"]
    selected_skills: list[str]
    conversation_id: str | None = None
    memory: ConversationMemoryStatus | None = None
    auto_selected: bool = False
    interaction_mode: Literal["chat", "task"] = "task"
    workflow_recommended: bool = False
    workflow_decision_source: str = "not_applicable"
    clarification_required: bool = False
    recommended_capabilities: list[str] = Field(default_factory=list)
    capability_routing: CapabilityRoutingTrace | None = None
    capability_recommendation: CapabilityRecommendation | None = None
    workflow: Workflow | None = None


class SkillSummary(BaseModel):
    name: str
    display_name: str
    description: str
    layer: str = "workflow"
    skill_type: str = "data-processing"
    runtime: str = "Not configured"
    status: str = "imported"
    summary_en: str = ""
    summary_zh: str = ""
    dependencies: list[str] = Field(default_factory=list)
    complementary_skills: list[str] = Field(default_factory=list)
    source: str = "LocalFlow"
    script_count: int = 0
    has_handler: bool = False
    executable: bool = False
    structure_mode: Literal["advisory", "planning", "runner"] = "advisory"
