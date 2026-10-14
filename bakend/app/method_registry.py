"""Single source of executable contracts, Skill references and explicit pipelines.

No directory discovery grants execution. Each atomic method must still bind to
the code allowlist in RunnerRegistry. Skill references cannot override its ports
or parameter schema. Definitions are immutable per execution snapshot.
"""

from __future__ import annotations
import copy
import hashlib
import json
import time
import re
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .skill_definitions import native_skills
from .artifact_semantics import compatible
from .method_validation import validate_contract, verification_status
from .workflow_models import (
    ParameterSpec,
    InputSlotSpec,
    OutputSlotSpec,
    MethodSourceReference,
    CommandReference,
    ExecutionRecipeSpec,
)


class MethodDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    id: str
    version: str
    kind: Literal["method"] = "method"
    label: str
    description: str
    runner_id: str
    parameters: list[ParameterSpec] = Field(default_factory=list)
    input_slots: list[InputSlotSpec] = Field(default_factory=list)
    output_slots: list[OutputSlotSpec] = Field(default_factory=list)
    input_types: list[str] = Field(default_factory=list)
    output_types: list[str] = Field(default_factory=list)
    requires_features: list[str] = Field(default_factory=list)
    included_capabilities: list[str] = Field(default_factory=list)
    source_references: list[MethodSourceReference] = Field(default_factory=list)
    command_references: list[CommandReference] = Field(default_factory=list)
    execution: dict

    @model_validator(mode="before")
    @classmethod
    def reject_unknown_contract_fields(cls, value):
        # Nested workflow models accept historical snapshots elsewhere. Native
        # definitions must reject typos instead of silently discarding conditions.
        for key, schema in (("parameters", ParameterSpec), ("input_slots", InputSlotSpec), ("output_slots", OutputSlotSpec)):
            for field in value.get(key, []):
                if isinstance(field, dict) and set(field) - schema.model_fields.keys():
                    raise ValueError("Unknown native contract fields in " + key)
        identity = value.get("id", "")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", identity) or not str(value.get("version", "")).strip():
            raise ValueError("Invalid native method identity/version")
        return value

    def contract(self):
        return self.model_dump(
            mode="json",
            exclude={"schema_version", "id", "version", "kind", "execution"},
        )

    def revision(self):
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class MethodRegistry:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.methods = {}
        self.pipelines = {}
        self.skills = {}
        self._signature = None

    def paths(self):
        return sorted(
            [
                *self.root.glob("methods/*/*/method.json"),
                *self.root.glob("pipelines/*/pipeline.json"),
                *self.root.glob("skills/*/skill.json"),
            ]
        )

    def load(self, *, force=False):
        now = time.monotonic()
        if (
            not force
            and self._signature is not None
            and now - getattr(self, "_checked", 0) < 0.5
        ):
            return
        self._checked = now
        paths = self.paths()
        signature = tuple(
            (str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in paths
        )
        if signature == self._signature:
            return
        methods = {}
        pipelines = {}
        skills = {}
        for path in paths:
            if not path.resolve().is_relative_to(self.root):
                raise ValueError("Catalog path leaves project root")
            value = json.loads(path.read_text(encoding="utf-8"))
            if value.get("schema_version") != 1:
                raise ValueError("Unsupported catalog schema: " + str(path))
            if path.name == "method.json":
                definition = MethodDefinition.model_validate(value)
                if definition.id != definition.runner_id or definition.execution.get(
                    "kind"
                ) not in {"builtin", "command", "reviewed"}:
                    raise ValueError("Method must identify a registered atomic adapter")
                if definition.id in methods:
                    raise ValueError("Duplicate method: " + definition.id)
                for key in ("parameters", "input_slots", "output_slots"):
                    ids = [s.id for s in getattr(definition, key)]
                    if len(ids) != len(set(ids)):
                        raise ValueError(
                            "Duplicate contract field: " + definition.id + "/" + key
                        )
                validate_contract(definition)
                methods[definition.id] = definition
            else:
                target = pipelines if path.name == "pipeline.json" else skills
                if value["id"] in target:
                    raise ValueError("Duplicate catalog identity: " + value["id"])
                target[value["id"]] = value
        skills = native_skills(skills)
        # References are checked before exposing a partially loaded catalog.
        for pid, p in pipelines.items():
            if p.get("kind") != "pipeline":
                raise ValueError("Invalid pipeline kind")
            prior = {}
            public_inputs = {s["id"]: s["type"] for s in p["input_slots"]}
            public_params = {s["id"] for s in p["parameters"]}
            for step in p["steps"]:
                if step["id"] in prior:
                    raise ValueError("Duplicate pipeline step: " + pid)
                method = methods.get(step["method_ref"])
                if method is None or method.version != step["method_version"]:
                    raise ValueError("Pipeline method/version missing: " + pid)
                if any(token in step["id"] for token in ("/", "\\", "..")) or any(
                    token in step.get("workspace", "") for token in ("/", "\\", "..")
                ):
                    raise ValueError("Unsafe pipeline step/workspace identity")
                ids = {v.id for v in method.parameters}
                if (
                    set(step.get("fixed_parameters", {})) - ids
                    or set(step.get("parameter_bindings", {})) - ids
                ):
                    raise ValueError("Unknown pipeline parameter: " + pid)
                if (
                    set(step.get("parameter_bindings", {}).values()) - public_params
                    or set(step.get("when", {})) - public_params
                ):
                    raise ValueError("Unknown public pipeline parameter: " + pid)
                slots = {s.id: s.type for s in method.input_slots}
                for key, b in step["input_bindings"].items():
                    actual = (
                        public_inputs.get(b.get("slot_id", key))
                        if b["source"] == "workflow_input"
                        else prior.get(b.get("step_id"), {}).get(
                            b.get("output_slot_id")
                        )
                    )
                    if key not in slots or not compatible(actual,slots[key]):
                        raise ValueError(
                            "Pipeline input type/link mismatch: "
                            + pid
                            + "/"
                            + step["id"]
                            + "/"
                            + key
                        )
                prior[step["id"]] = {s.id: s.type for s in method.output_slots}
            outputs = {s["id"]: s["type"] for s in p["output_slots"]}
            if set(outputs) != set(p["output_bindings"]):
                raise ValueError("Pipeline must bind every public output: " + pid)
            for key, b in p["output_bindings"].items():
                if (
                    key not in outputs
                    or prior.get(b["step_id"], {}).get(b["output_slot_id"])
                    != outputs[key]
                ):
                    raise ValueError(
                        "Pipeline output type/link mismatch: " + pid + "/" + key
                    )
        allowed = {
            "capability",
            "method_id",
            "label",
            "role",
            "default_recommended",
            "default_reason",
            "replaces_operations",
            "execution_resolution",
            "method_ref",
            "pipeline_ref",
        }
        for sid, s in skills.items():
            for ref in s["method_refs"] + s["pipeline_refs"]:
                if set(ref) - allowed:
                    raise ValueError("Skill cannot override method contracts: " + sid)
                collection, key = (
                    (methods, "method_ref")
                    if "method_ref" in ref
                    else (pipelines, "pipeline_ref")
                )
                if ref.get(key) not in collection:
                    raise ValueError("Dangling Skill reference: " + sid)
        self.methods, self.pipelines, self.skills = methods, pipelines, skills
        self._signature = signature

    def method(self, identity):
        self.load()
        if identity not in self.methods:
            raise KeyError("Method not found")
        return self.methods[identity].model_copy(deep=True)

    def verification(self, identity):
        return verification_status(self.method(identity), self.root)

    def pipeline_recipe(self, identity):
        self.load()
        p = copy.deepcopy(self.pipelines[identity])
        for step in p["steps"]:
            step["method_id"] = step.pop("method_ref")
            step.pop("method_version")
        return ExecutionRecipeSpec.model_validate(
            {
                **p,
                "recipe_id": p["id"],
                "operation_value": "",
                "interface_capability": "method",
                "interface_method_id": p["steps"][0]["method_id"],
            }
        )

    def reviewed_specs(self):
        self.load()
        result = {}
        for m in self.methods.values():
            if m.execution.get("kind") != "reviewed":
                continue
            s = copy.deepcopy(m.execution["adapter"])
            s["runner_id"] = m.runner_id
            s["label"] = m.label
            routing = {p["id"]: p for p in s["parameters"]}
            s["parameters"] = [
                {**p.model_dump(mode="json"), **routing.get(p.id, {})}
                for p in m.parameters
            ]
            routing = {p["id"]: p for p in s["inputs"]}
            s["inputs"] = [
                {**p.model_dump(mode="json"), **routing.get(p.id, {})}
                for p in m.input_slots
            ]
            s["artifact_types"] = {
                port.path_template.removeprefix("results/"): port.type
                for port in m.output_slots
                if port.id != "results" and port.path_template.startswith("results/")
            }
            result[m.runner_id] = s
        return result
