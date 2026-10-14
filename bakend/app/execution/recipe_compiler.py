from __future__ import annotations

from dataclasses import dataclass
from ..artifact_semantics import compatible, specialize, accepts_format
from ..parameter_rules import effective_parameters, matches

from ..capability_registry import CapabilityMethod, CapabilityRegistry
from ..run_models import RunExecutionOptions
from ..workflow_models import InputBinding, MethodOption, Workflow, WorkflowStep


class RecipeCompilationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExecutionUnit:
    step: WorkflowStep
    method: MethodOption
    workspace_key: str
    parent_step_id: str
    recipe_id: str | None = None
    sample_id: str | None = None
    sample_label: str = ""
    sample_root: str | None = None
    execution: RunExecutionOptions | None = None


class RecipeCompiler:
    """Compile reviewed recipes into immutable, runner-only execution units."""

    def __init__(self, registry: CapabilityRegistry) -> None:
        self.registry = registry

    def compile(self, workflow: Workflow) -> list[ExecutionUnit]:
        units: list[ExecutionUnit] = []
        # Public workflow outputs may be implemented by a nested recipe step.
        output_aliases: dict[tuple[str, str], tuple[str, str]] = {}

        for parent in sorted(workflow.steps, key=lambda item: item.order):
            method = self._selected_method(parent)
            if method.entry_kind == 'method' and method.definition_id:
                current = self.registry.method_for('method', method.definition_id)
                if current is None or current.definition_version != method.definition_version:
                    raise RecipeCompilationError(f'步骤 {parent.title} 的方法契约已更新；请根据当前事实重新准备方案，不要执行旧契约')
            parent = parent.model_copy(deep=True)
            self._rewrite_upstream_bindings(parent, output_aliases)
            recipe = method.execution_recipe
            if recipe is None:
                sources={(u.step.id,o.id):o.type for u in units for o in u.method.output_slots}
                types={key:sources.get((b.upstream_step_id,b.output_slot_id),b.artifact_type) for key,b in parent.input_bindings.items()}
                unit_method = specialize(method,types,effective_parameters(method,parent.selected_parameters))
                units.append(
                    ExecutionUnit(
                        step=parent,
                        method=unit_method,
                        workspace_key=parent.id,
                        parent_step_id=parent.id,
                    )
                )
                for output in unit_method.output_slots:
                    output_aliases[(parent.id, output.id)] = (parent.id, output.id)
                continue

            stage_ids: dict[str, str] = {}
            stage_outputs: dict[tuple[str, str], tuple[str, str, str]] = {}
            public_values=self._default_parameters(method)
            public_values.update(parent.selected_parameters)
            for stage_index, stage in enumerate(recipe.steps, start=1):
                if not matches(stage.when, public_values):
                    continue
                if stage.id in stage_ids:
                    raise RecipeCompilationError(
                        f"执行配方 {recipe.recipe_id} 含有重复步骤：{stage.id}"
                    )
                definition = self.registry.method_for(stage.capability, stage.method_id)
                if definition is None or definition.execution_mode != "runner":
                    raise RecipeCompilationError(
                        f"执行配方 {recipe.recipe_id} 引用了不可执行方法："
                        f"{stage.capability}/{stage.method_id}"
                    )
                child_id = f"{parent.id}__{stage.id}"
                stage_ids[stage.id] = child_id
                child_method = self._method_option(definition)
                parameters = self._default_parameters(child_method)
                if stage.inherit_parameters:
                    parameters.update(
                        {
                            key: value
                            for key, value in parent.selected_parameters.items()
                            if key in {spec.id for spec in child_method.parameters}
                        }
                    )
                child_parameter_ids = {spec.id for spec in child_method.parameters}
                for target_id, public_id in stage.parameter_bindings.items():
                    if target_id not in child_parameter_ids or public_id not in {
                        spec.id for spec in method.parameters
                    }:
                        raise RecipeCompilationError(
                            f"执行配方 {recipe.recipe_id}/{stage.id} 的参数映射无效："
                            f"{public_id} -> {target_id}"
                        )
                    public_values = self._default_parameters(method)
                    public_values.update(parent.selected_parameters)
                    if public_id in public_values:
                        parameters[target_id] = public_values[public_id]
                if set(stage.fixed_parameters) - child_parameter_ids:
                    raise RecipeCompilationError(f"执行配方 {recipe.recipe_id} 含有未声明参数")
                parameters.update(stage.fixed_parameters)

                bindings: dict[str, InputBinding] = {}
                input_specs = {slot.id: slot for slot in child_method.input_slots}
                for target_slot_id, binding_spec in stage.input_bindings.items():
                    target_slot = input_specs.get(target_slot_id)
                    if target_slot is None:
                        raise RecipeCompilationError(
                            f"执行配方 {recipe.recipe_id}/{stage.id} 引用了不存在的输入槽位："
                            f"{target_slot_id}"
                        )
                    if binding_spec.source == "workflow_input":
                        source_id = binding_spec.slot_id or target_slot_id
                        source = parent.input_bindings.get(source_id)
                        if source is not None:
                            if not compatible(source.artifact_type,target_slot.type):
                                raise RecipeCompilationError(
                                    f"执行配方 {recipe.recipe_id}/{stage.id} 的输入类型不匹配："
                                    f"{source.artifact_type} -> {target_slot.type}"
                                )
                            bindings[target_slot_id] = source.model_copy(
                                deep=True,
                                update={
                                    "slot_id": target_slot_id,
                                    "artifact_type": source.artifact_type,
                                },
                            )
                        continue

                    source_step = binding_spec.step_id or ""
                    source_output = binding_spec.output_slot_id or ""
                    upstream = stage_outputs.get((source_step, source_output))
                    if upstream is None:
                        raise RecipeCompilationError(
                            f"执行配方 {recipe.recipe_id}/{stage.id} 的上游输出无效："
                            f"{source_step}.{source_output}"
                        )
                    upstream_step_id, upstream_output_id, artifact_type = upstream
                    if not compatible(artifact_type,target_slot.type):
                        raise RecipeCompilationError(
                            f"执行配方 {recipe.recipe_id}/{stage.id} 的输入类型不匹配："
                            f"{artifact_type} -> {target_slot.type}"
                        )
                    bindings[target_slot_id] = InputBinding(
                        slot_id=target_slot_id,
                        artifact_type=target_slot.type if target_slot.cardinality=="many" else artifact_type,
                        source="upstream_output",
                        upstream_sources=[v.model_copy(update={"step_id":stage_ids.get(v.step_id,v.step_id)}) for v in binding_spec.additional_sources],
                        label=target_slot.label,
                        upstream_step_id=upstream_step_id,
                        output_slot_id=upstream_output_id,
                        status="planned",
                        messages=["运行时将绑定到配方上一步产生的受审核制品。"],
                    )

                child_method=specialize(child_method,{k:b.artifact_type for k,b in bindings.items()},parameters)
                child = WorkflowStep(
                    id=child_id,
                    order=len(units) + 1,
                    title=stage.title,
                    description=f"由执行配方 {recipe.label} 展开。",
                    capability=stage.capability,
                    origin=parent.origin,
                    selection="auto",
                    depends_on=(
                        [units[-1].step.id]
                        if stage_index > 1 and units
                        else list(parent.depends_on)
                    ),
                    status="confirmed",
                    method_options=[child_method],
                    selected_method_id=child_method.id,
                    selected_parameters=parameters,
                    input_bindings=bindings,
                )
                units.append(
                    ExecutionUnit(
                        step=child,
                        method=child_method,
                        workspace_key=f"{parent.id}__{stage.workspace}",
                        parent_step_id=parent.id,
                        recipe_id=recipe.recipe_id,
                    )
                )
                for output in child_method.output_slots:
                    stage_outputs[(stage.id, output.id)] = (
                        child_id,
                        output.id,
                        output.type,
                    )

            for public_output_id, source in recipe.output_bindings.items():
                resolved = stage_outputs.get((source.step_id, source.output_slot_id))
                public_slot=next((s for s in method.output_slots if s.id==public_output_id),None)
                if public_slot and not matches(public_slot.generated_when,public_values):
                    continue
                if resolved is None:
                    raise RecipeCompilationError(
                        f"执行配方 {recipe.recipe_id} 的公开输出无效："
                        f"{source.step_id}.{source.output_slot_id}"
                    )
                if public_slot and not compatible(resolved[2],public_slot.type):
                    raise RecipeCompilationError('参数改变了输出语义，请在 Pipeline 编辑器重新校验并发布：'+public_output_id)
                output_aliases[(parent.id, public_output_id)] = (
                    resolved[0],
                    resolved[1],
                )

        for unit in units:
            unit.step.selected_parameters = effective_parameters(unit.method, unit.step.selected_parameters)
        self._validate_upstream_inputs(units)
        return units

    @staticmethod
    def _validate_upstream_inputs(units: list[ExecutionUnit]) -> None:
        # Validate expanded recipes as well as ordinary workflow links before
        # dry-run defers file checks, and again before a real run is created.
        available: dict[tuple[str, str], str] = {}
        formats: dict[tuple[str, str], str] = {}
        for unit in units:
            input_types = {slot.id: slot.type for slot in unit.method.input_slots}
            for slot_id, binding in unit.step.input_bindings.items():
                if binding.source != "upstream_output":
                    continue
                target=next(s for s in unit.method.input_slots if s.id==slot_id)
                if binding.upstream_sources and target.cardinality!='many':
                    raise RecipeCompilationError('此输入只接受单个文件：'+slot_id)
                refs=[(binding.upstream_step_id,binding.output_slot_id),*[(v.step_id,v.output_slot_id) for v in binding.upstream_sources]]
                if len(refs)!=len(set(refs)):
                    raise RecipeCompilationError('重复的多文件输入：'+slot_id)
                for extra in binding.upstream_sources:
                    kind=available.get((extra.step_id,extra.output_slot_id))
                    if not compatible(kind,target.type):
                        raise RecipeCompilationError('多文件输入引用了不可用或不兼容的上游输出：'+slot_id)
                    if not accepts_format(formats.get((extra.step_id,extra.output_slot_id), ''), target.formats):
                        raise RecipeCompilationError('多文件输入输出格式不匹配：'+slot_id)
                source = (binding.upstream_step_id or "", binding.output_slot_id or "")
                artifact_type = available.get(source)
                if artifact_type is None:
                    raise RecipeCompilationError(
                        f"步骤 {unit.step.title} 的输入 {slot_id} 引用了不可用的上游输出："
                        f"{source[0]}.{source[1]}"
                    )
                if not compatible(artifact_type,input_types.get(slot_id)) or not compatible(artifact_type,binding.artifact_type):
                    raise RecipeCompilationError(
                        f"步骤 {unit.step.title} 的输入 {slot_id} 与上游输出类型不匹配"
                    )
                if not accepts_format(formats.get(source, ''), target.formats):
                    raise RecipeCompilationError(f'步骤 {unit.step.title} 的输入 {slot_id} 与上游输出格式不匹配；需要显式格式转换')
            parameters = RecipeCompiler._default_parameters(unit.method)
            parameters.update(unit.step.selected_parameters)
            for output in unit.method.output_slots:
                formats[(unit.step.id, output.id)] = output.format
                if all(parameters.get(key) in values for key, values in output.generated_when.items()):
                    available[(unit.step.id, output.id)] = output.type

    @staticmethod
    def _rewrite_upstream_bindings(
        step: WorkflowStep,
        aliases: dict[tuple[str, str], tuple[str, str]],
    ) -> None:
        for slot_id, binding in list(step.input_bindings.items()):
            if binding.source != "upstream_output":
                continue
            for extra in binding.upstream_sources:
                if (extra.step_id,extra.output_slot_id) in aliases:
                    extra.step_id,extra.output_slot_id=aliases[(extra.step_id,extra.output_slot_id)]
            alias = aliases.get(
                (binding.upstream_step_id or "", binding.output_slot_id or "")
            )
            if alias is None:
                continue
            step.input_bindings[slot_id] = binding.model_copy(
                deep=True,
                update={"upstream_step_id": alias[0], "output_slot_id": alias[1]},
            )

    @staticmethod
    def _selected_method(step: WorkflowStep) -> MethodOption:
        if not step.selected_method_id:
            raise RecipeCompilationError(f"步骤 {step.title} 尚未选择方法")
        method = next(
            (item for item in step.method_options if item.id == step.selected_method_id),
            None,
        )
        if method is None:
            raise RecipeCompilationError(f"步骤 {step.title} 的方法快照已经失效")
        return method.model_copy(deep=True)

    @staticmethod
    def _method_option(definition: CapabilityMethod) -> MethodOption:
        return MethodOption(
            id=definition.method_id,
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
            parameters=definition.parameters,
            input_slots=definition.input_slots,
            output_slots=definition.output_slots,
            structure_level=definition.structure_level,
            source_references=definition.source_references,
            command_references=definition.command_references,
            delegate_skill_ids=definition.delegate_skill_ids,
            execution_resolution=definition.execution_resolution,
            execution_recipe=definition.execution_recipe,
            included_capabilities=definition.included_capabilities,
        )

    @staticmethod
    def _default_parameters(method: MethodOption) -> dict[str, object]:
        return {
            spec.id: spec.default
            for spec in method.parameters
            if spec.default is not None
        }
