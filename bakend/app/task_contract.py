"""User scope is independent of file discovery and of the selected software.

These are planning constraints, not filesystem permissions. Generated intermediates
are distinguished from existing inputs so excluding an old FOD does not ban CSD.
"""
from __future__ import annotations

from typing import Literal, Annotated

from pydantic import BaseModel, ConfigDict, Field


class ArtifactConstraint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_type: str = Field(min_length=1, max_length=100)
    # Exact relative paths or basenames mentioned by the user; never glob/shell.
    paths: list[str] = Field(default_factory=list, max_length=50)
    slot_id: str = Field(default='', max_length=100,
        description='仅限制指定输入角色；留空表示该类型的全部角色。')

    def matches(self, artifact_type: str, path: str | None = None, slot_id: str = '') -> bool:
        if self.artifact_type != artifact_type or self.slot_id and self.slot_id != slot_id:
            return False
        if not self.paths:
            return True
        if path is None:
            return False
        normalized = path.replace("\\", "/")
        return any(normalized == p.replace("\\", "/") or (
            not p.startswith(("/", "\\")) and
            normalized.endswith("/" + p.replace("\\", "/"))) for p in self.paths)


class OutputReference(BaseModel):
    """A selected catalog port, never a model-authored artifact type."""
    model_config = ConfigDict(extra="forbid")
    method_id: str = Field(min_length=1, max_length=160)
    output_slot_id: str = Field(min_length=1, max_length=100)


class StageOutputReference(OutputReference):
    stage_index: int = Field(ge=0, strict=True,
        description='主控 required_stages 中的序号，从0开始；说明这个真实输出实现哪项必需阶段。')


class TaskContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal: str = Field(default="", max_length=800)
    required_stages: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(default_factory=list, max_length=12,
        description='用户明确要求在流程内完成的中间阶段，用自然语言描述；不填内部类型名称。')
    goal_output_refs: list[OutputReference] = Field(default_factory=list, max_length=12)
    required_output_refs: list[OutputReference] = Field(default_factory=list, max_length=12)
    goal_artifact_types: list[str] = Field(default_factory=list, max_length=12)
    required_output_types: list[str] = Field(default_factory=list, max_length=12,
        description='用户明确要求在本流程内部生成的中间产物；不能改成用户选择的外部输入。')
    required_method_ids: list[str] = Field(default_factory=list, max_length=20,
        description='用户要求的已登记处理步骤。普通实现偏好使用preferred_methods，不强制增加步骤。')
    retrieval_queries: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(default_factory=list, max_length=8)
    input_policy: Literal["unspecified", "only_listed", "user_selected"] = "unspecified"
    allowed_existing_inputs: list[ArtifactConstraint] = Field(default_factory=list, max_length=30)
    forbidden_existing_inputs: list[ArtifactConstraint] = Field(default_factory=list, max_length=30)
    forbidden_capability_ids: list[str] = Field(default_factory=list, max_length=30)
    plan_scope: Literal["requested_steps", "goal_from_inputs"] = "requested_steps"
    allow_generated_intermediates: bool = True
    sample_policy: Literal["eligible", "all"] = Field(default="eligible",
        description="默认为满足必要输入的样本准备方案并列明跳过原因；仅用户明确要求不可跳过任何样本时使用all。")

    def allows_external(self, artifact_type: str, path: str | None = None) -> bool:
        if any(item.matches(artifact_type, path) for item in self.forbidden_existing_inputs):
            return False
        if self.input_policy in {"unspecified", "user_selected"}:
            return True
        return any(item.artifact_type == artifact_type and
                   (path is None or item.matches(artifact_type, path))
                   for item in self.allowed_existing_inputs)

    def allows_slot(self, slot, path: str | None = None) -> bool:
        # The catalog has both the gradient-table parent and two precise subtypes.
        # Resolve from declared port formats, never from the user's wording. A
        # b-value permission alone must not authorize the b-vector port (or vice versa).
        types = {slot.type}
        if slot.type == 'gradient_table':
            formats = set(slot.formats)
            if '.bval' in formats and '.bvec' not in formats:
                types.add('b_values')
            if '.bvec' in formats and '.bval' not in formats:
                types.add('b_vectors')
        elif slot.type in {'b_values', 'b_vectors'}:
            types.add('gradient_table')
        if any(item.artifact_type in types and item.matches(item.artifact_type, path, slot.id)
               for item in self.forbidden_existing_inputs):
            return False
        return self.input_policy in {'unspecified', 'user_selected'} or any(item.artifact_type in types and
            (not item.slot_id or item.slot_id == slot.id) and
            (path is None or item.matches(item.artifact_type, path, slot.id)) for item in self.allowed_existing_inputs)


def declared_input_scope(contract: TaskContract, declarations, artifact_types=None) -> TaskContract:
    """Project scoped user assertions without changing the canonical goal.

    A missing unbound input must be produced by the graph. A missing *path*
    only excludes that path; it does not prove all files of that type absent.
    Rebuild from the base contract each time so corrections retract old assertions.
    """
    result = contract.model_copy(deep=True)
    for row in declarations:
        declaration = row['summary']
        if declaration.get('available', True):
            continue
        if artifact_types is not None and declaration['artifact_type'] not in artifact_types:
            continue  # An old interpretation cannot classify or block user-selected files.
        exclusion = ArtifactConstraint(artifact_type=declaration['artifact_type'],
            slot_id=declaration.get('slot_id', ''),
            paths=[declaration['path']] if declaration.get('path') else [])
        if exclusion not in result.forbidden_existing_inputs:
            result.forbidden_existing_inputs.append(exclusion)
    return result


def input_declaration_signature(declarations):
    # Quotes/references are provenance; only intent relevant to input selection
    # changes the graph and its cached summary.
    return sorted((r['summary']['artifact_type'], r['summary'].get('slot_id', ''),
        r['summary'].get('path', ''), r['summary'].get('available', True),
        r['summary'].get('per_sample', True)) for r in declarations)


class PlanNotMatched(ValueError):
    """No validated plan was found; this does NOT prove software is absent."""

    def __init__(self, reason: str, diagnostics: dict | None = None):
        super().__init__(reason)
        self.diagnostics = diagnostics or {}


def binding_scope_errors(contract: TaskContract, workflow) -> list[str]:
    errors = []
    for step in workflow.steps:
        method = next((m for m in step.method_options if m.id == step.selected_method_id), None)
        for binding in step.input_bindings.values():
            if binding.source == "upstream_output":
                continue
            paths = binding.absolute_paths or [binding.absolute_path or binding.relative_path]
            if binding.sample_matches:
                paths = [m.absolute_path for m in binding.sample_matches if m.status=='matched']
            if binding.history:
                paths = [a.absolute_path for a in binding.history.artifacts]
            slot = next((s for s in method.input_slots if s.id == binding.slot_id), None) if method else None
            if any(not (contract.allows_slot(slot, path) if slot else
                       contract.allows_external(binding.artifact_type, path)) for path in paths):
                errors.append(f"{step.title}/{binding.slot_id}：已有输入不符合本次允许范围")
    return errors
