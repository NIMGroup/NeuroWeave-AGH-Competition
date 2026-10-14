from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field
from .task_contract import TaskContract

from .dataset_models import (
    DatasetPathPreview,
    DatasetRecord,
    SamplePathCoverage,
    SamplePathMatch,
)


WorkflowStatus = Literal[
    "awaiting_method_choice",
    "awaiting_input_binding",
    "chain_ready",
    "awaiting_parameter_review",
    "awaiting_dry_run",
    "ready",
    "confirmed",
]


class ParameterOption(BaseModel):
    value: Any
    label: str
    description: str = ""


class ParameterSpec(BaseModel):
    id: str
    argument: str
    label: str
    description: str = ""
    type: Literal["string", "integer", "number", "boolean"]
    control: Literal["auto", "select", "number", "text", "switch"] = "auto"
    level: Literal["basic", "advanced"] = "basic"
    group: str = "常用参数"
    unit: str = ""
    required: bool = True
    default: Any = None
    options: list[ParameterOption] = Field(default_factory=list)
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    placeholder: str = ""
    visible_when: dict[str, list[Any]] = Field(default_factory=dict)
    required_when: dict[str, list[Any]] = Field(default_factory=dict)
    enabled_when: dict[str, list[Any]] = Field(default_factory=dict)
    conflicts_with: list[str] = Field(default_factory=list)
    condition_help: str = ""
    usage: str = ""
    software_default: str = ""
    documentation_url: str = ""


class InputSlotSpec(BaseModel):
    requires_features: dict[str, Any] = Field(default_factory=dict)
    id: str
    type: str
    label: str
    description: str = ""
    required: bool = True
    required_when: dict[str, list[Any]] = Field(default_factory=dict)
    # Shared inputs are needed if any consumer needs them (OR of AND conditions).
    required_when_any: list[dict[str, list[Any]]] = Field(default_factory=list)
    cardinality: Literal["one", "many"] = "one"
    binding: Literal["file", "folder", "file_bundle", "artifact"] = "artifact"
    formats: list[str] = Field(default_factory=list)
    companions: list[str] = Field(default_factory=list)
    source_options: list[Literal["dataset", "existing_path", "upstream_output"]] = Field(
        default_factory=lambda: ["dataset", "existing_path", "upstream_output"]
    )
    validation: list[str] = Field(default_factory=list)
    discovery_patterns: list[str] = Field(default_factory=list, max_length=16)
    condition_help: str = ""


class OutputSlotSpec(BaseModel):
    provides_features: dict[str, Any] = Field(default_factory=dict)
    id: str
    type: str
    label: str
    description: str = ""
    format: str = ""
    primary: bool = False
    path_template: str = ""
    generated_when: dict[str, list[Any]] = Field(default_factory=dict)


class HistoricalArtifactReference(BaseModel):
    # Frozen provenance travels with a run to compute nodes; no database lookup
    # or dataset-directory inference is needed by a scientific runner.
    sample_id: str
    step_id: str
    artifact_id: str
    absolute_path: str
    output_directory: str
    size_bytes: int
    mtime_ns: int
    sha256: str = ""
    is_directory: bool = False


class HistoricalInputReference(BaseModel):
    run_id: str
    group_id: str
    dataset_id: str
    artifacts: list[HistoricalArtifactReference]
    allow_partial: bool = False


class SkippedSample(BaseModel):
    sample_id: str
    label: str
    reasons: list[str]


class SampleScope(BaseModel):
    total: int
    eligible_sample_ids: list[str] = Field(default_factory=list)
    skipped_samples: list[SkippedSample] = Field(default_factory=list)


class UpstreamSource(BaseModel):
    step_id: str
    output_slot_id: str


class InputBinding(BaseModel):
    confirmed_by_user: bool = False
    allow_partial: bool = False
    upstream_sources: list[UpstreamSource] = Field(default_factory=list,max_length=63)
    absolute_paths: list[str] = Field(default_factory=list,max_length=64)
    slot_id: str
    artifact_type: str
    source: Literal["dataset", "existing_path", "upstream_output"]
    label: str = ""
    root_id: str | None = None
    relative_path: str | None = None
    absolute_path: str | None = None
    upstream_step_id: str | None = None
    output_slot_id: str | None = None
    binding_mode: Literal["single", "per_sample", "dataset"] = "single"
    dataset_id: str | None = None
    path_template: str = ""
    variables: list[str] = Field(default_factory=list)
    coverage: SamplePathCoverage | None = None
    sample_matches: list[SamplePathMatch] = Field(default_factory=list)
    history: HistoricalInputReference | None = None
    status: Literal["validated", "selected", "planned"] = "selected"
    messages: list[str] = Field(default_factory=list)
    deferred_resolution: bool = False
    declaration_fact_id: str = ""
    per_sample_requested: bool = True


class MethodSourceReference(BaseModel):
    path: str
    label: str = ""
    kind: Literal["skill", "script", "command"] = "skill"
    line_start: int | None = None
    line_end: int | None = None


class CommandReference(BaseModel):
    command: str
    language: str = "bash"
    source_path: str
    line_start: int | None = None
    kind: Literal["operation", "setup", "environment", "example"] = "example"
    executable: bool = False


class RecipeInputBindingSpec(BaseModel):
    additional_sources: list[UpstreamSource] = Field(default_factory=list,max_length=63)
    source: Literal["workflow_input", "step_output"]
    slot_id: str | None = None
    step_id: str | None = None
    output_slot_id: str | None = None


class RecipeOutputBindingSpec(BaseModel):
    step_id: str
    output_slot_id: str


class ExecutionRecipeStepSpec(BaseModel):
    when: dict[str, list[Any]] = Field(default_factory=dict)
    id: str
    title: str
    capability: str
    method_id: str
    fixed_parameters: dict[str, Any] = Field(default_factory=dict)
    # Explicit parameter routing avoids collisions such as the three distinct
    # MRtrix "algorithm" options in response/FOD/tractography stages.
    parameter_bindings: dict[str, str] = Field(default_factory=dict)
    inherit_parameters: bool = True
    input_bindings: dict[str, RecipeInputBindingSpec] = Field(default_factory=dict)
    workspace: str = "default"


class ExecutionRecipeSpec(BaseModel):
    recipe_id: str
    label: str
    operation_value: str
    interface_capability: str
    interface_method_id: str
    exposed_parameters: list[str] = Field(default_factory=list)
    parameters: list[ParameterSpec] = Field(default_factory=list)
    input_slots: list[InputSlotSpec] = Field(default_factory=list)
    output_slots: list[OutputSlotSpec] = Field(default_factory=list)
    steps: list[ExecutionRecipeStepSpec]
    output_bindings: dict[str, RecipeOutputBindingSpec] = Field(default_factory=dict)


class MethodOption(BaseModel):
    skill_role: str = ""
    definition_id: str = ""
    definition_version: str = ""
    entry_kind: Literal["method", "pipeline", "capability"] = "method"
    id: str
    skill_id: str
    label: str
    description: str
    runner_id: str
    execution_mode: Literal["planning", "runner"] = "runner"
    input_types: list[str] = Field(default_factory=list)
    output_types: list[str] = Field(default_factory=list)
    requires_features: list[str] = Field(default_factory=list)
    compatible: bool = True
    blocked_reasons: list[str] = Field(default_factory=list)
    input_warnings: list[str] = Field(default_factory=list)
    scope_warnings: list[str] = Field(default_factory=list)
    recommended: bool = False
    recommendation_reason: str = ""
    parameters: list[ParameterSpec] = Field(default_factory=list)
    input_slots: list[InputSlotSpec] = Field(default_factory=list)
    output_slots: list[OutputSlotSpec] = Field(default_factory=list)
    structure_level: Literal["profile", "evidence", "reviewed"] = "reviewed"
    source_references: list[MethodSourceReference] = Field(default_factory=list)
    command_references: list[CommandReference] = Field(default_factory=list)
    delegate_skill_ids: list[str] = Field(default_factory=list)
    execution_recipe: ExecutionRecipeSpec | None = None
    included_capabilities: list[str] = Field(default_factory=list)
    execution_resolution: dict[str, Any] = Field(default_factory=dict)


class WorkflowStep(BaseModel):
    id: str
    order: int
    title: str
    description: str
    capability: str
    origin: Literal["requested", "dependency"] = "requested"
    selection: Literal["auto", "user"] = "user"
    depends_on: list[str] = Field(default_factory=list)
    status: Literal[
        "configured",
        "needs_method",
        "method_selected",
        "parameters_ready",
        "parameters_reviewed",
        "confirmed",
    ]
    method_options: list[MethodOption] = Field(default_factory=list)
    selected_method_id: str | None = None
    selected_parameters: dict[str, Any] = Field(default_factory=dict)
    input_bindings: dict[str, InputBinding] = Field(default_factory=dict)
    # Suggestions never satisfy a port. Only a user action creates a binding.
    input_suggestions: dict[str, dict[str, Any]] = Field(default_factory=dict)
    manual_input_types: list[str] = Field(default_factory=list)
    missing_required_inputs: list[str] = Field(default_factory=list)
    command_preview: str | None = None


class DryRunCommand(BaseModel):
    step_id: str
    runner_id: str
    command_preview: str
    execution_mode: Literal["planning", "runner"] = "runner"
    valid: bool = True
    messages: list[str] = Field(default_factory=list)


class DryRunReport(BaseModel):
    input_checks: dict[str, dict] = Field(default_factory=dict)
    quality_checks: list[dict[str, Any]] = Field(default_factory=list)
    quality_summary: dict[str, int] = Field(default_factory=dict)
    quality_review_required: bool = False
    quality_acknowledged: bool = False
    valid: bool
    execution_enabled: bool = False
    dataset_path_validated: bool = False
    commands: list[DryRunCommand] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    checked_at: str


class Workflow(BaseModel):
    conversation_id: str | None = None
    owner_principal: str | None = None
    managed_task_id: str | None = None
    # Preserve historic snapshots; new assistant drafts explicitly use user selection.
    input_selection: Literal['automatic', 'user'] = 'automatic'
    input_check_cache: dict[str, dict] = Field(default_factory=dict)
    # Historic snapshots without this field retain their original flat layout.
    output_layout: Literal['legacy', 'sample_steps'] = 'legacy'
    task_contract: TaskContract = Field(default_factory=TaskContract)
    planning_diagnostics: dict[str, Any] = Field(default_factory=dict)
    contract_errors: list[str] = Field(default_factory=list)
    workspace_id: str | None = None
    workspace_root_path: str | None = None
    output_relative_path: str = "outputs"
    workflow_id: str
    template_id: str
    revision: int = 1
    title: str
    summary: str
    original_request: str
    status: WorkflowStatus
    dataset_id: str | None = None
    dataset: DatasetRecord | None = None
    planner_source: str = "model"
    execution_mode: Literal["planning", "runner", "mixed"] = "runner"
    plan_kind: Literal["atomic", "composite"] = "composite"
    requested_capabilities: list[str] = Field(default_factory=list)
    excluded_capabilities: list[str] = Field(default_factory=list)
    input_capabilities: list[str] = Field(default_factory=list)
    dependency_policy: Literal["target_only", "complete"] = "target_only"
    steps: list[WorkflowStep]
    warnings: list[str] = Field(default_factory=list)
    can_lock: bool = False
    can_unlock: bool = False
    can_review_parameters: bool = False
    can_dry_run: bool = False
    can_confirm: bool = False
    locked_at: str | None = None
    parameters_reviewed_at: str | None = None
    dry_run_report: DryRunReport | None = None
    sample_scope: SampleScope | None = None
    confirmed_at: str | None = None
    created_at: str
    updated_at: str


class WorkflowDraftRequest(BaseModel):
    workspace_id: str | None = None
    message: str = Field(min_length=1, max_length=20_000)
    selected_skills: list[str] = Field(default_factory=list)
    dataset_id: str | None = None
    language: str = "zh-CN"


class WorkflowMutationRequest(BaseModel):
    expected_revision: int = Field(ge=1)


class WorkflowConfirmRequest(WorkflowMutationRequest):
    acknowledge_quality: bool = False


class MethodSelectionRequest(WorkflowMutationRequest):
    method_id: str = Field(min_length=1, max_length=200)


class WorkflowDatasetRequest(WorkflowMutationRequest):
    dataset_id: str = Field(min_length=1, max_length=200)


class ParameterUpdateRequest(WorkflowMutationRequest):
    parameters: dict[str, Any]


class InputBindingRequest(WorkflowMutationRequest):
    root_id: str = Field(min_length=1, max_length=100)
    relative_path: str = Field(default="", max_length=4_000)
    binding_mode: Literal['auto', 'single', 'per_sample', 'template'] = 'auto'


class InputPreviewRequest(BaseModel):
    root_id: str = Field(min_length=1, max_length=100)
    relative_path: str = Field(default="", max_length=4_000)
    binding_mode: Literal['auto', 'single', 'per_sample', 'template'] = 'auto'


class InputPreviewResponse(DatasetPathPreview):
    pass
