from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .workflow_models import (
    CommandReference,
    ExecutionRecipeSpec,
    InputSlotSpec,
    MethodSourceReference,
    OutputSlotSpec,
    ParameterSpec,
)
from .method_registry import MethodRegistry


class ArtifactTypeDefinition(BaseModel):
    id: str
    label: str
    description: str
    kind: Literal["file", "folder", "file_bundle", "artifact"] = "artifact"
    formats: list[str] = Field(default_factory=list)
    companions: list[str] = Field(default_factory=list)


class CapabilityMethod(BaseModel):
    skill_role: str = ""
    definition_id: str = ""
    definition_version: str = ""
    entry_kind: Literal["method", "pipeline", "capability"] = "method"
    capability: str
    method_id: str
    skill_id: str
    label: str
    description: str
    runner_id: str
    execution_mode: Literal["planning", "runner"] = "runner"
    input_types: list[str] = Field(default_factory=list)
    output_types: list[str] = Field(default_factory=list)
    requires_features: list[str] = Field(default_factory=list)
    default_recommended: bool = False
    default_reason: str = ""
    parameters: list[ParameterSpec] = Field(default_factory=list)
    input_slots: list[InputSlotSpec] = Field(default_factory=list)
    output_slots: list[OutputSlotSpec] = Field(default_factory=list)
    structure_level: Literal["profile", "evidence", "reviewed"] = "reviewed"
    source_references: list[MethodSourceReference] = Field(default_factory=list)
    command_references: list[CommandReference] = Field(default_factory=list)
    delegate_skill_ids: list[str] = Field(default_factory=list)
    execution_recipe: ExecutionRecipeSpec | None = None
    included_capabilities: list[str] = Field(default_factory=list)
    replaces_operations: list[str] = Field(default_factory=list)
    execution_resolution: dict = Field(default_factory=dict)


class CapabilityRegistry:
    """Project Skill references into the existing workflow routing interface."""

    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir
        self.definitions = MethodRegistry(skills_dir.parent)
        self._signature = None
        self._by_capability = {}
        self._artifact_types = {}

    def _atomic(self, identity, **context):
        d = self.definitions.method(identity)
        return CapabilityMethod(
            **d.contract(),
            capability=context.pop("capability", "method"),
            method_id=context.pop("method_id", identity),
            skill_id=context.pop("skill_id", ""),
            definition_id=d.id,
            definition_version=d.version,
            entry_kind="method",
            **context,
        )

    def _load(self):
        self.definitions.load()
        artifact_path = self.skills_dir / "artifact-types.json"
        library_methods = self.library_provider() if hasattr(self, 'library_provider') else []
        library_signature = tuple((m.method_id, m.definition_version) for m in library_methods)
        signature = (self.definitions._signature, artifact_path.stat().st_mtime_ns, library_signature)
        if signature == self._signature:
            return
        artifacts = {
            v["id"]: ArtifactTypeDefinition.model_validate(v)
            for v in json.loads(artifact_path.read_text(encoding="utf-8"))[
                "artifact_types"
            ]
        }
        result = {}
        for sid, skill in self.definitions.skills.items():
            for ref in skill["method_refs"]:
                method = self._atomic(
                    ref["method_ref"],
                    capability=ref["capability"],
                    method_id=ref["method_id"],
                    skill_id=sid,
                )
                # Context describes why this Skill uses the method, never its schema.
                method = method.model_copy(
                    update={
                        "skill_role": ref.get("role", ""),
                        **{
                            k: ref[k]
                            for k in (
                                "default_recommended",
                                "default_reason",
                                "replaces_operations",
                                "execution_resolution",
                            )
                            if k in ref
                        },
                    }
                )
                result.setdefault(method.capability, []).append(method)
            for ref in skill["pipeline_refs"]:
                p = self.definitions.pipelines[ref["pipeline_ref"]]
                recipe = self.definitions.pipeline_recipe(p["id"])
                method = CapabilityMethod(
                    capability=ref["capability"],
                    method_id=ref["method_id"],
                    skill_id=sid,
                    definition_id=p["id"],
                    definition_version=p["version"],
                    entry_kind="pipeline",
                    label=ref["label"],
                    description=p["description"],
                    skill_role=ref["role"],
                    default_recommended=ref.get("default_recommended", False),
                    default_reason=ref.get("default_reason", ""),
                    replaces_operations=ref.get("replaces_operations", []),
                    execution_resolution=ref.get("execution_resolution", {}),
                    runner_id="pipeline:" + p["id"],
                    included_capabilities=p.get("included_capabilities", []),
                    execution_recipe=recipe,
                    parameters=recipe.parameters,
                    input_slots=recipe.input_slots,
                    output_slots=recipe.output_slots,
                    input_types=[s.type for s in recipe.input_slots],
                    output_types=[s.type for s in recipe.output_slots],
                    source_references=[
                        MethodSourceReference(
                            path="pipelines/" + p["id"] + "/pipeline.json",
                            kind="skill",
                            label="组合流程定义",
                        )
                    ],
                )
                result.setdefault(method.capability, []).append(method)
            for raw in skill["planning_capabilities"]:
                method = CapabilityMethod.model_validate(
                    {
                        **raw,
                        "entry_kind": "capability",
                        "execution_mode": "planning",
                        "runner_id": "",
                    }
                )
                result.setdefault(method.capability, []).append(method)
        for cap, items in result.items():
            if len({m.method_id for m in items}) != len(items):
                raise ValueError("Duplicate Skill entry: " + cap)
            for m in items:
                if {
                    s.type for s in [*m.input_slots, *m.output_slots]
                } - artifacts.keys():
                    raise ValueError("Unknown artifact type: " + m.method_id)
        for method in library_methods:
            result.setdefault(method.capability, []).append(method)
        self._by_capability = result
        self._artifact_types = artifacts
        self._signature = signature

    def methods_for(self, capability):
        self._load()
        return [
            m.model_copy(deep=True) for m in self._by_capability.get(capability, [])
        ]

    def selectable_methods_for(self, capability, **_):
        seen = set()
        choices = []
        for method in self.methods_for(capability):
            identity = (method.entry_kind, method.definition_id or method.method_id)
            if identity not in seen:
                choices.append(method)
                seen.add(identity)
        return choices

    def method_for(self, capability, method_id):
        self._load()
        if capability == "method" or method_id in self.definitions.methods:
            try:
                return self._atomic(method_id)
            except KeyError:
                return None
        return next(
            (m for m in self.methods_for(capability) if m.method_id == method_id), None
        )

    def recipe_methods(self, recipe):
        return [self._atomic(s.method_id) for s in recipe.steps]

    def capabilities(self):
        self._load()
        return sorted(self._by_capability)

    def all_methods(self):
        self._load()
        return [
            m.model_copy(deep=True)
            for cap in sorted(self._by_capability)
            for m in self._by_capability[cap]
        ]

    def planner_catalog(self, capabilities):
        return {
            cap: [m.method_id for m in self.methods_for(cap)] for cap in capabilities
        }

    def runner_ids(self):
        self._load()
        return {m.runner_id for m in self.definitions.methods.values()}

    def artifact_types(self):
        self._load()
        return list(self._artifact_types.values())

    def stats(self):
        self._load()
        if getattr(self, "_stats_signature", None) == self._signature:
            return dict(self._stats_cache)
        items = self.all_methods()
        result = dict(
            capabilities=len(self._by_capability),
            methods=len(self.definitions.methods),
            pipelines=len(self.definitions.pipelines),
            planning_methods=sum(m.entry_kind == "capability" for m in items),
            runner_methods=len(self.definitions.methods),
            evidence_backed_methods=len(self.definitions.methods),
            reference_commands=sum(len(m.command_references) for m in items),
            artifact_types=len(self._artifact_types),
            selectable_methods=len(items),
            multi_method_capabilities=sum(
                len(v) > 1 for v in self._by_capability.values()
            ),
        )
        self._stats_signature = self._signature
        self._stats_cache = result
        return dict(result)
