"""MethodCatalog: translate registered Method contracts into selectable options."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..capability_registry import CapabilityMethod
from ..workflow_models import (
    MethodOption,
)
from . import contracts

if TYPE_CHECKING:
    from ..capability_registry import CapabilityRegistry
    from ..capability_tree import CapabilityTree
    from ..execution.runner_registry import RunnerRegistry
    from ..skill_loader import SkillLoader


class MethodCatalog:
    def __init__(
        self,
        registry: CapabilityRegistry,
        capability_tree: CapabilityTree | None,
        skill_loader: SkillLoader | None,
        runner_registry: RunnerRegistry | None,
    ) -> None:
        self.registry = registry
        self.capability_tree = capability_tree
        self.skill_loader = skill_loader
        self.runner_registry = runner_registry

    def expand_and_order_capabilities(
        self,
        requested: list[str],
        initial_types: set[str],
        excluded: set[str] | None = None,
        selected: dict[str, tuple[MethodOption, dict[str, Any]]] | None = None,
    ) -> list[str]:
        """Add only required producer capabilities and return a validated linear DAG."""
        allowed = set(self.registry.capabilities()) - (excluded or set())
        selected = selected or {}
        internal = {
            stage.capability
            for method, _values in selected.values()
            if method.execution_recipe
            for stage in method.execution_recipe.steps
        }
        allowed -= internal - set(requested)
        result: list[str] = []
        available = set(initial_types)
        visiting: set[str] = set()
        explicit_prerequisites: dict[str, list[str]] = {
            "dwi.fod": ["dwi.response"],
            "dwi.tractography": ["dwi.fod", "dwi.five_tt"],
            "dwi.connectome": ["dwi.tractography"],
        }

        def accepts(method: CapabilityMethod) -> bool:
            return "*" in method.input_types or bool(
                available.intersection(method.input_types)
            )

        def ensure(capability_id: str) -> None:
            if capability_id in result or capability_id not in allowed:
                return
            if capability_id in visiting:
                raise RuntimeError(f"能力依赖包含循环：{capability_id}")
            visiting.add(capability_id)
            selection = selected.get(capability_id)
            methods = (
                [selection[0]]
                if selection
                else self.registry.methods_for(capability_id)
            )
            required = (
                {
                    slot.type
                    for slot in selection[0].input_slots
                    if contracts.slot_required(slot, selection[1])
                }
                if selection
                else None
            )
            for prerequisite in explicit_prerequisites.get(capability_id, []):
                # A requested recomputation must still run; otherwise reuse a
                # prerequisite artifact already present in the inspected inputs.
                products = {
                    item
                    for method in self.registry.methods_for(prerequisite)
                    for item in method.output_types
                    if item != "execution_log"
                }
                if required is not None and not products.intersection(required):
                    continue
                if prerequisite not in requested and products.intersection(available):
                    continue
                ensure(prerequisite)

            def ready():
                return (
                    required.issubset(available)
                    if required is not None
                    else any(accepts(method) for method in methods)
                )

            if methods and not ready():
                required_types = (
                    (required - available)
                    if required is not None
                    else {
                        input_type
                        for method in methods
                        for input_type in method.input_types
                        if input_type != "*"
                    }
                )
                producers: list[tuple[int, str]] = []
                for producer_id in self.registry.capabilities():
                    if (
                        producer_id not in allowed
                        or producer_id == capability_id
                        or producer_id in result
                    ):
                        continue
                    producer_methods = self.registry.methods_for(producer_id)
                    if not any(
                        required_types.intersection(method.output_types)
                        for method in producer_methods
                    ):
                        continue
                    can_start = any(accepts(method) for method in producer_methods)
                    producers.append((0 if can_start else 1, producer_id))
                for _rank, producer_id in sorted(producers):
                    ensure(producer_id)
                    if ready():
                        break
            visiting.remove(capability_id)
            result.append(capability_id)
            for method in methods:
                available.update(method.output_types)

        for capability_id in requested:
            ensure(capability_id)
        return result

    def candidate_context(self, capability_ids: list[str]) -> list[dict[str, Any]]:
        if self.capability_tree is None:
            raise RuntimeError("CapabilityTree 尚未配置")
        context: list[dict[str, Any]] = []
        for capability_id in capability_ids:
            node = self.capability_tree.leaf(capability_id)
            if node is None:
                continue
            skill_context: list[dict[str, str]] = []
            for skill_id in node.skill_ids:
                skill = self.skill_loader.find(skill_id) if self.skill_loader else None
                if skill is None:
                    continue
                skill_context.append(
                    {
                        "skill_id": skill.name,
                        "description": skill.description,
                        "summary": skill.summary_zh or skill.summary_en,
                        "skill_excerpt": skill.prompt_excerpt(max_chars=1_200),
                    }
                )
            methods = []
            for method in self.registry.selectable_methods_for(capability_id):
                runtime_status: dict[str, Any] = {
                    "available": False if method.execution_mode == "planning" else None,
                    "reason": (
                        method.execution_resolution.get("reason")
                        or "规划方法没有执行入口"
                    )
                    if method.execution_mode == "planning"
                    else "Runner 状态未注入",
                }
                if (
                    method.execution_mode == "runner"
                    and self.runner_registry is not None
                ):
                    recipe_methods = (
                        self.registry.recipe_methods(method.execution_recipe)
                        if method.execution_recipe is not None
                        else [method]
                    )
                    stage_statuses: list[dict[str, Any]] = []
                    for recipe_method in recipe_methods:
                        runner = self.runner_registry.get(recipe_method.runner_id)
                        stage_statuses.append(
                            runner.runtime_status()
                            if runner is not None
                            else {
                                "available": False,
                                "reason": f"尚未接入可信 Runner：{recipe_method.runner_id}",
                            }
                        )
                    unavailable = [
                        status
                        for status in stage_statuses
                        if not status.get("available")
                    ]
                    runtime_status = {
                        "available": not unavailable,
                        "reason": (
                            "执行配方中的 Runner 均可用"
                            if not unavailable and method.execution_recipe is not None
                            else "；".join(
                                str(status.get("reason") or "Runner 不可用")
                                for status in unavailable
                            )
                            if unavailable
                            else str(stage_statuses[0].get("reason") or "Runner 可用")
                        ),
                    }
                methods.append(
                    {
                        "method_id": method.method_id,
                        "skill_id": method.skill_id,
                        "label": method.label,
                        "description": method.description,
                        "skill_role": method.skill_role,
                        "definition_id": method.definition_id,
                        "entry_kind": method.entry_kind,
                        "execution_mode": method.execution_mode,
                        "input_types": method.input_types,
                        "output_types": method.output_types,
                        "requires_features": method.requires_features,
                        "default_recommended": method.default_recommended,
                        "default_reason": method.default_reason,
                        "structure_level": method.structure_level,
                        "delegate_skill_ids": method.delegate_skill_ids,
                        "runtime_status": runtime_status,
                        "execution_recipe": (
                            {
                                "recipe_id": method.execution_recipe.recipe_id,
                                "label": method.execution_recipe.label,
                                "steps": [
                                    {
                                        "id": step.id,
                                        "title": step.title,
                                        "method_id": step.method_id,
                                        "capability": step.capability,
                                    }
                                    for step in method.execution_recipe.steps
                                ],
                            }
                            if method.execution_recipe is not None
                            else None
                        ),
                        "included_capabilities": method.included_capabilities,
                        "inputs": [
                            {
                                "id": slot.id,
                                "type": slot.type,
                                "label": slot.label,
                                "description": slot.description,
                                "required": slot.required,
                                "required_when": slot.required_when,
                                "required_when_any": slot.required_when_any,
                            }
                            for slot in method.input_slots
                        ],
                        "outputs": [
                            {
                                "id": slot.id,
                                "type": slot.type,
                                "label": slot.label,
                                "description": slot.description,
                                "generated_when": slot.generated_when,
                            }
                            for slot in method.output_slots
                        ],
                        "parameter_summary": [
                            {
                                "id": parameter.id,
                                "label": parameter.label,
                                "level": parameter.level,
                            }
                            for parameter in method.parameters
                        ],
                    }
                )
            context.append(
                {
                    "capability_id": capability_id,
                    "label": node.label,
                    "description": node.description,
                    "tree_path": self.capability_tree.path_for(capability_id),
                    "skills": skill_context,
                    "methods": methods,
                }
            )
        return context

    def option(
        self,
        definition: CapabilityMethod,
        available_types: set[str],
        features: dict[str, bool],
        has_dataset: bool,
        check_runtime: bool = True,
    ) -> MethodOption:
        reasons: list[str] = []
        input_warnings: list[str] = []
        if (
            not definition.input_slots
            and "*" not in definition.input_types
            and not available_types.intersection(definition.input_types)
        ):
            input_warnings.append(
                "尚未识别到所需输入："
                + ", ".join(definition.input_types)
                + "。仍可选择；请核对输入，执行前会再次校验。"
            )
        if has_dataset:
            missing_features = [
                feature
                for feature in definition.requires_features
                if not features.get(feature, False)
            ]
            if missing_features:
                input_warnings.append(
                    "数据集扫描尚未确认条件："
                    + ", ".join(missing_features)
                    + "。扫描结果可能不完整，请核对实际输入。"
                )
        if definition.input_slots:
            missing_types = {
                slot.type
                for slot in definition.input_slots
                if slot.required and slot.type not in available_types
            }
            if missing_types:
                input_warnings.append(
                    "输入待补齐："
                    + "、".join(sorted(missing_types))
                    + "。可以先选择方法，锁定流程后手动绑定文件。"
                )
        if (
            check_runtime
            and definition.execution_mode == "runner"
            and self.runner_registry is not None
        ):
            recipe_methods = (
                self.registry.recipe_methods(definition.execution_recipe)
                if definition.execution_recipe is not None
                else [definition]
            )
            for recipe_method in recipe_methods:
                runner = self.runner_registry.get(recipe_method.runner_id)
                if runner is None:
                    reasons.append(f"尚未接入可信 Runner：{recipe_method.runner_id}")
                    continue
                runtime = runner.runtime_status()
                if not runtime.get("available"):
                    reasons.append(str(runtime.get("reason") or "运行环境暂不可用"))
        return MethodOption(
            id=definition.method_id,
            skill_role=definition.skill_role,
            definition_id=definition.definition_id,
            definition_version=definition.definition_version,
            entry_kind=definition.entry_kind,
            skill_id=definition.skill_id,
            label=definition.label,
            description=definition.description,
            runner_id=definition.runner_id,
            execution_mode=definition.execution_mode,
            input_types=definition.input_types,
            output_types=definition.output_types,
            requires_features=definition.requires_features,
            compatible=not reasons,
            blocked_reasons=reasons,
            input_warnings=input_warnings,
            parameters=definition.parameters,
            input_slots=definition.input_slots,
            output_slots=definition.output_slots,
            structure_level=definition.structure_level,
            source_references=definition.source_references,
            command_references=definition.command_references,
            delegate_skill_ids=definition.delegate_skill_ids,
            execution_recipe=definition.execution_recipe,
            included_capabilities=definition.included_capabilities,
            execution_resolution=definition.execution_resolution,
        )

    def restrict_method_scope(self, method: MethodOption, excluded: set[str]) -> None:
        conflicts = contracts.scope_conflicts(method, excluded)
        if conflicts:
            labels = []
            for capability in sorted(conflicts):
                node = (
                    self.capability_tree.leaf(capability)
                    if self.capability_tree
                    else None
                )
                labels.append(node.label if node else capability)
            method.scope_warnings.append(
                "此方法内部仍包含已移除或计划复用的操作："
                + "、".join(labels)
                + "。手动选择后将按该方法原有定义执行；如需复用已有结果，请选择对应的已有输入方法。"
            )
