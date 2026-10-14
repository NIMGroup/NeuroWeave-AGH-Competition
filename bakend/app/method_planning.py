"""Bounded contract retrieval and validation for goal-driven task planning.

The capability tree is a ranking hint, not an exclusion boundary. The index covers
every native Method, including methods without Skill references. It reads metadata
only; it never scans datasets or runs software. Exhausting a search budget reports
incomplete coverage, never 'method does not exist'.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .artifact_semantics import specialize
from .parameter_rules import effective_parameters, input_required, matches
from .pipeline_compiler import PipelineCompiler
from .pipeline_models import ComposeRequest, ComposeStep
from .task_contract import PlanNotMatched, TaskContract, binding_scope_errors


class MethodPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["matched", "candidate_mismatch"]
    reason: str = Field(default="", max_length=1000)
    steps: list[ComposeStep] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def topological_order(self):
        remaining = {step.id: step for step in self.steps}
        if len(remaining) != len(self.steps):
            raise ValueError("步骤 ID 重复")
        ordered, done = [], set()
        while remaining:
            ready = [
                step
                for step in remaining.values()
                if {
                    source.step_id
                    for link in step.inputs.values()
                    for source in link.sources()
                }
                <= done
            ]
            if not ready:
                raise ValueError("流程包含环或引用了不存在的步骤")
            for step in ready:
                ordered.append(step)
                done.add(step.id)
                del remaining[step.id]
        self.steps = ordered
        return self


class MethodContractIndex:
    def __init__(self, registry):
        self.registry = registry
        self.signature = None

    def refresh(self):
        self.registry._load()
        signature = self.registry._signature
        if signature == self.signature:
            return
        self.methods = dict(self.registry.definitions.methods)
        self.producers = defaultdict(set)
        self.references = defaultdict(list)
        for method in self.methods.values():
            for slot in method.output_slots:
                self.producers[slot.type].add(method.id)
        for method in self.registry.all_methods():
            if method.entry_kind == "method":
                self.references[method.definition_id].append(method)
        self.signature = signature

    def retrieve(self, contract, capability_ids, *, limit=32, max_depth=4):
        self.refresh()
        ranked = {}
        queue = deque((kind, 0) for kind in contract.goal_artifact_types)
        visited = set()
        depth_limited = False
        generic_frontiers = set()
        while queue:
            kind, depth = queue.popleft()
            if kind in visited:
                continue
            visited.add(kind)
            # Generic containers are not semantic compatibility evidence. Joining
            # every generated_file/image producer would flood the shortlist with
            # unrelated commands and could suggest scientifically invalid edges.
            if depth and kind in {
                "generated_file",
                "imaging_input",
                "execution_log",
                "dataset_folder",
                "*",
            }:
                generic_frontiers.add(kind)
                continue
            # Existing inputs end a dependency branch, except the requested output.
            if depth and any(
                c.artifact_type == kind for c in contract.allowed_existing_inputs
            ):
                continue
            for identity in sorted(self.producers.get(kind, ())):
                method = self.methods[identity]
                refs = self.references.get(identity, [])
                if any(
                    ref.capability in contract.forbidden_capability_ids for ref in refs
                ):
                    continue
                ranked.setdefault(identity, depth)
                # Include conditional requirements. The planner must choose their
                # real controlling parameters; it may never invent a bypass flag.
                for slot in method.input_slots:
                    if slot.required or slot.required_when or slot.required_when_any:
                        if depth < max_depth:
                            queue.append((slot.type, depth + 1))
                        elif not contract.allows_external(slot.type):
                            depth_limited = True
        # With unknown output semantics use the existing route as a bounded hint.
        if not contract.goal_artifact_types:
            for identity, refs in self.references.items():
                if any(ref.capability in capability_ids for ref in refs):
                    ranked.setdefault(identity, 0)
        ordered = sorted(
            ranked,
            key=lambda mid: (
                ranked[mid],
                not any(
                    ref.capability in capability_ids
                    for ref in self.references.get(mid, [])
                ),
                mid,
            ),
        )
        selected = ordered[:limit]
        return selected, {
            "strategy": "method_output_index",
            "indexed_methods": len(self.methods),
            "reachable_candidates": len(ordered),
            "returned_candidates": len(selected),
            "max_depth": max_depth,
            "truncated": len(ordered) > limit or depth_limited,
            "generic_frontiers": sorted(generic_frontiers),
            "missing_goal_types": [
                t for t in contract.goal_artifact_types if not self.producers.get(t)
            ],
        }

    def context(self, identities, runner_registry=None):
        result = []
        for identity in identities:
            m = self.methods[identity]
            runner = (
                runner_registry.get(m.runner_id)
                if runner_registry is not None
                else None
            )
            status = (
                runner_registry.runtime_status(m.runner_id)
                if getattr(runner_registry, 'planning_only', False) is True
                else runner.runtime_status() if runner is not None else {"available": None}
            )
            result.append(
                {
                    "method_id": identity,
                    "label": m.label,
                    "description": m.description[:700],
                    "runtime": status,
                    "inputs": [
                        s.model_dump(exclude_defaults=True) for s in m.input_slots
                    ],
                    "outputs": [
                        s.model_dump(exclude_defaults=True) for s in m.output_slots
                    ],
                    "parameters": [
                        p.model_dump(
                            exclude_defaults=True,
                            exclude={"usage", "documentation_url", "software_default"},
                        )
                        for p in m.parameters
                    ],
                }
            )
        return result

    def capability(self, identity):
        refs = self.references.get(identity, [])
        precise = [
            ref
            for ref in refs
            if not ref.capability.startswith(("skill.", "software."))
        ]
        return (precise or refs)[0].capability if refs else "method"


def validate_plan(
    plan: MethodPlan,
    contract: TaskContract,
    registry,
    *,
    allowed_ids=None,
    requested_capabilities=(),
):
    """Validate real ports/conditions, external scope, goals and unnecessary nodes."""
    if plan.status != "matched" or not plan.steps:
        raise PlanNotMatched(
            "当前候选尚未形成符合需求的可执行流程；不能据此判断平台缺少方法。"
        )
    if allowed_ids is not None and any(
        s.method_id not in allowed_ids for s in plan.steps
    ):
        raise ValueError("流程引用了当前未检索到的方法，需先读取其定义")
    index = MethodContractIndex(registry)
    index.refresh()
    seen = {}
    goal_nodes = set()
    dependencies = {}
    for step in plan.steps:
        method = registry.definitions.method(step.method_id)
        refs = index.references.get(step.method_id, [])
        if any(r.capability in contract.forbidden_capability_ids for r in refs):
            raise ValueError("流程包含用户排除的操作：" + method.label)
        values = effective_parameters(method, step.parameters)
        inferred = {
            key: next(
                (
                    s.type
                    for s in seen[link.step_id][0].output_slots
                    if s.id == link.output_slot_id
                ),
                "",
            )
            for key, link in step.inputs.items()
            if link.step_id in seen
        }
        method = specialize(method, inferred, values)
        produces_goal = any(
            s.type in contract.goal_artifact_types and matches(s.generated_when, values)
            and (not contract.goal_output_refs or any(
                r.method_id == step.method_id and r.output_slot_id == s.id for r in contract.goal_output_refs))
            for s in method.output_slots
        )
        if (
            not contract.allow_generated_intermediates
            or contract.plan_scope == "requested_steps"
        ) and not (
            produces_goal or step.method_id in contract.required_method_ids or
            any(s.type in contract.required_output_types for s in method.output_slots) or
            any(r.capability in requested_capabilities for r in refs)
        ):
            raise ValueError("流程增加了未请求的操作：" + method.label)
        dependencies[step.id] = {
            source.step_id for link in step.inputs.values() for source in link.sources()
        }
        for slot in method.input_slots:
            if slot.id in step.inputs:
                if not contract.allow_generated_intermediates:
                    raise ValueError("用户未允许使用本次生成的中间结果")
            elif input_required(slot, values) and not contract.allows_slot(slot):
                raise ValueError(
                    f"{method.label}/{slot.id} 需要未获允许的已有输入：{slot.type}"
                )
        if produces_goal:
            goal_nodes.add(step.id)
        seen[step.id] = (method, values)
    generated = {
        s.type
        for m, values in seen.values()
        for s in m.output_slots
        if matches(s.generated_when, values)
    }
    missing = set(contract.goal_artifact_types) - generated
    if missing:
        raise ValueError("流程没有生成要求的输出：" + ", ".join(sorted(missing)))
    missing_intermediates = set(contract.required_output_types) - generated
    for reference in [*contract.goal_output_refs, *contract.required_output_refs]:
        if not any(m.id == reference.method_id and any(
            p.id == reference.output_slot_id and matches(p.generated_when,values)
            for p in m.output_slots) for m,values in seen.values()):
            raise ValueError('流程没有生成选定的真实输出：'+reference.method_id+'/'+reference.output_slot_id)
    missing_methods = set(contract.required_method_ids) - {s.method_id for s in plan.steps}
    if missing_intermediates or missing_methods:
        raise ValueError('流程没有覆盖完整目标中的必需阶段：'+', '.join(sorted(missing_intermediates | missing_methods)))
    if goal_nodes:
        needed = set(goal_nodes)
        queue = list(goal_nodes)
        while queue:
            for dependency in dependencies.get(queue.pop(), set()):
                if dependency not in needed:
                    needed.add(dependency)
                    queue.append(dependency)
        if set(seen) - needed:
            raise ValueError(
                "流程包含不通向目标的额外步骤：" + ", ".join(sorted(set(seen) - needed))
            )
    # Reuse the visual Pipeline editor's compiler: exact ports/types, topological
    # ordering, no cycles, parameter enums/ranges, conditional output availability.
    library = PipelineCompiler(registry)
    return library.compile(
        ComposeRequest(name="经过输入输出校验的流程", steps=plan.steps)
    )


def workflow_plan(workflow):
    steps = []
    for step in workflow.steps:
        if not step.selected_method_id:
            raise ValueError("请先选择所有步骤的方法")
        method = next(m for m in step.method_options if m.id == step.selected_method_id)
        steps.append(
            ComposeStep(
                id=step.id,
                method_id=method.definition_id or method.id,
                parameters=step.selected_parameters,
                inputs={
                    key: {
                        "step_id": b.upstream_step_id,
                        "output_slot_id": b.output_slot_id,
                        "additional_sources": [
                            v.model_dump() for v in b.upstream_sources
                        ],
                    }
                    for key, b in step.input_bindings.items()
                    if b.source == "upstream_output"
                },
            )
        )
    return MethodPlan(status="matched", steps=steps)


def validate_workflow_scope(workflow, registry):
    errors = binding_scope_errors(workflow.task_contract, workflow)
    if workflow.planner_source.startswith("method-contract:"):
        try:
            validate_plan(
                workflow_plan(workflow),
                workflow.task_contract,
                registry,
                requested_capabilities=workflow.requested_capabilities,
            )
        except (ValueError, KeyError) as exc:
            errors.append(str(exc))
    return errors
