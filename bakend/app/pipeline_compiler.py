"""Compile registered method graphs in memory. No database or workflow service required."""

from __future__ import annotations

import copy
import math

from .artifact_semantics import compatible, specialize, accepts_format
from .capability_registry import CapabilityMethod
from .parameter_rules import effective_parameters, matches
from .pipeline_models import ComposeRequest
from .pipeline_shared_inputs import merge_shared_inputs
from .workflow_models import ExecutionRecipeSpec


def remap_conditions(value, namespace):
    value = copy.deepcopy(value)
    for field in ("visible_when", "required_when", "enabled_when", "generated_when"):
        value[field] = {
            namespace + key: options for key, options in value.get(field, {}).items()
        }
    if "required_when_any" in value:
        value["required_when_any"] = [
            {namespace + key: options for key, options in condition.items()}
            for condition in value["required_when_any"]
        ]
    if "conflicts_with" in value:
        value["conflicts_with"] = [namespace + key for key in value["conflicts_with"]]
    return value


def check_parameter(spec, value):
    if value is None:
        return
    valid = (
        isinstance(value, str)
        if spec.type == "string"
        else type(value) is bool
        if spec.type == "boolean"
        else type(value) is int
        if spec.type == "integer"
        else type(value) in (int, float)
    )
    if type(value) in (int, float) and not math.isfinite(value):
        raise ValueError("数值必须有限：" + spec.id)
    if not valid or (
        spec.options and value not in [option.value for option in spec.options]
    ):
        raise ValueError("参数类型或枚举值不合法：" + spec.id)
    if (
        spec.minimum is not None
        and value < spec.minimum
        or spec.maximum is not None
        and value > spec.maximum
    ):
        raise ValueError("参数超出范围：" + spec.id)


class PipelineCompiler:
    def __init__(self, registry):
        self.registry = registry

    def compile(self, request: ComposeRequest, identity="preview", version=1):
        if not request.name.strip():
            raise ValueError("名称不能为空")
        registry = self.registry.definitions
        registry.load()
        seen, consumed = {}, set()
        producer_values = {}
        inputs, outputs, parameters, steps, output_bindings = [], [], [], [], {}
        control_ids = {p.id for p in request.controls}
        if len(control_ids) != len(request.controls):
            raise ValueError("Pipeline 控制参数 ID 重复")
        for control in request.controls:
            check_parameter(control, control.default)
            parameters.append(control.model_dump(mode="json"))
        for step in request.steps:
            if step.id in seen:
                raise ValueError("步骤 ID 重复：" + step.id)
            method = registry.method(step.method_id)
            if self.registry.method_for("method", method.id) is None:
                raise ValueError("未注册的方法")
            specs = {p.id: p for p in method.parameters}
            if set(step.parameters) - set(specs):
                raise ValueError("包含方法未定义的参数")
            inferred = {
                key: next(
                    (
                        s.type
                        for s in seen[link.step_id].output_slots
                        if s.id == link.output_slot_id
                    ),
                    "",
                )
                for key, link in step.inputs.items()
                if link.step_id in seen
            }
            method = specialize(
                method, inferred, effective_parameters(method, step.parameters)
            )
            prefix = step.id + "__"
            if any(prefix + p.id in control_ids for p in method.parameters):
                raise ValueError("控制参数与步骤参数 ID 冲突")
            for key, allowed in step.when.items():
                spec = next((p for p in request.controls if p.id == key), None)
                if spec is None or not allowed:
                    raise ValueError("条件步骤必须引用 Pipeline 控制参数")
                for value in allowed:
                    check_parameter(spec, value)
            for p in method.parameters:
                item = remap_conditions(p.model_dump(mode="json"), prefix)
                item.update(
                    id=prefix + p.id, label=step.id + " · " + p.label, group=step.id
                )
                if p.id in step.parameters:
                    check_parameter(p, step.parameters[p.id])
                    item["default"] = step.parameters[p.id]
                parameters.append(item)
            bindings = {}
            if set(step.inputs) - {s.id for s in method.input_slots}:
                raise ValueError("包含方法未定义的输入")
            for slot in method.input_slots:
                link = step.inputs.get(slot.id)
                if link:
                    producer = seen.get(link.step_id)
                    output = (
                        next(
                            (
                                s
                                for s in producer.output_slots
                                if s.id == link.output_slot_id
                            ),
                            None,
                        )
                        if producer
                        else None
                    )
                    if output is None:
                        raise ValueError(
                            "输入必须连接到前面步骤的真实输出；不允许环或向后引用"
                        )
                    if not compatible(output.type, slot.type):
                        raise ValueError(
                            f"输入输出类型不匹配：{output.type} → {slot.type}"
                        )
                    if not accepts_format(output.format, slot.formats):
                        raise ValueError(f'输入输出格式不匹配：{output.format or "未声明"} → {slot.formats}；需要显式格式转换')
                    if slot.requires_features and any(output.provides_features.get(k) != v for k,v in slot.requires_features.items()):
                        raise ValueError("上游契约没有保证此输入需要的条件")
                    if link.additional_sources and slot.cardinality != "many":
                        raise ValueError("此输入只接受单个文件")
                    for extra in link.additional_sources:
                        producer_extra = seen.get(extra.step_id)
                        extra_output = (
                            next(
                                (
                                    s
                                    for s in producer_extra.output_slots
                                    if s.id == extra.output_slot_id
                                ),
                                None,
                            )
                            if producer_extra
                            else None
                        )
                        if extra_output is None or not compatible(
                            extra_output.type, slot.type
                        ):
                            raise ValueError("多文件输入引用了不存在或不兼容的输出")
                        if not accepts_format(extra_output.format, slot.formats):
                            raise ValueError('多文件输入输出格式不匹配；需要显式格式转换')
                        if not matches(
                            extra_output.generated_when, producer_values[extra.step_id]
                        ):
                            raise ValueError("多文件输入引用的条件输出当前不会生成")
                        consumed.add((extra.step_id, extra.output_slot_id))
                    if len(
                        {(v.step_id, v.output_slot_id) for v in link.sources()}
                    ) != len(link.sources()):
                        raise ValueError("多文件输入不能重复引用同一输出")
                    # Conditional ports are real ports. Validate their effective producer
                    # parameters now; RecipeCompiler checks them again before execution.
                    if not matches(
                        output.generated_when, producer_values[link.step_id]
                    ):
                        expected = ", ".join(
                            f"{key}={allowed}"
                            for key, allowed in output.generated_when.items()
                        )
                        raise ValueError(
                            f"{link.step_id}（{producer.label}）的输出 {output.label} 当前不会生成；"
                            f"需要 {expected}，请调整上游参数或移除此连接"
                        )
                    bindings[slot.id] = {
                        "source": "step_output",
                        "step_id": link.step_id,
                        "output_slot_id": link.output_slot_id,
                        "additional_sources": [
                            v.model_dump() for v in link.additional_sources
                        ],
                    }
                    consumed.add((link.step_id, link.output_slot_id))
                else:
                    item = remap_conditions(slot.model_dump(mode="json"), prefix)
                    item.update(id=prefix + slot.id, label=step.id + " · " + slot.label)
                    inputs.append(item)
                    bindings[slot.id] = {
                        "source": "workflow_input",
                        "slot_id": prefix + slot.id,
                    }
            steps.append(
                {
                    "id": step.id,
                    "title": method.label,
                    "capability": "method",
                    "method_id": method.id,
                    "method_ref": method.id,
                    "method_version": method.version,
                    "workspace": step.id,
                    "inherit_parameters": False,
                    "when": step.when,
                    "fixed_parameters": {},
                    "parameter_bindings": {
                        p.id: prefix + p.id for p in method.parameters
                    },
                    "input_bindings": bindings,
                }
            )
            seen[step.id] = method
            producer_values[step.id] = effective_parameters(method, step.parameters)
        inputs = merge_shared_inputs(request.shared_inputs, inputs, steps)
        for step_id, method in seen.items():
            for slot in method.output_slots:
                if (step_id, slot.id) in consumed:
                    continue
                item = remap_conditions(slot.model_dump(mode="json"), step_id + "__")
                item["generated_when"].update(next(s.when for s in request.steps if s.id == step_id))
                item.update(
                    id=step_id + "__" + slot.id, label=step_id + " · " + slot.label
                )
                outputs.append(item)
                output_bindings[item["id"]] = {
                    "step_id": step_id,
                    "output_slot_id": slot.id,
                }
        payload = {
            "schema_version": 1,
            "id": identity,
            "version": str(version),
            "kind": "pipeline",
            "label": request.name.strip(),
            "description": request.description,
            "category": ["我的 Pipeline"],
            "parameters": parameters,
            "input_slots": inputs,
            "output_slots": outputs,
            "steps": steps,
            "output_bindings": output_bindings,
            "editor": request.model_dump(mode="json"),
        }
        self.as_method(
            payload
        )  # Validate against the same recipe schema used by Runner.
        return payload

    @staticmethod
    def as_method(p):
        identity = p["id"] + "@" + str(p["version"])
        recipe = ExecutionRecipeSpec.model_validate(
            {
                **p,
                "recipe_id": identity,
                "operation_value": "",
                "interface_capability": "method",
                "interface_method_id": p["steps"][0].get(
                    "method_ref", p["steps"][0].get("method_id")
                ),
                "steps": [
                    {**s, "method_id": s.get("method_ref", s.get("method_id"))}
                    for s in p["steps"]
                ],
            }
        )
        return CapabilityMethod(
            capability="library." + identity,
            method_id=identity,
            skill_id="pipeline-library",
            definition_id=p["id"],
            definition_version=str(p["version"]),
            entry_kind="pipeline",
            label=p["label"],
            description=p.get("description", ""),
            runner_id="pipeline:" + identity,
            execution_recipe=recipe,
            parameters=recipe.parameters,
            input_slots=recipe.input_slots,
            output_slots=recipe.output_slots,
            input_types=[s.type for s in recipe.input_slots],
            output_types=[s.type for s in recipe.output_slots],
        )
