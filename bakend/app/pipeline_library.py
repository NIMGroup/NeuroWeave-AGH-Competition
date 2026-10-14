"""Library facade: catalogue navigation and workflow integration.

Compilation, immutable versions, and editable drafts have separate owners.
Saving or describing a pipeline never submits a job.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from .pipeline_compiler import PipelineCompiler
from .pipeline_management import PipelineAuthoring
from .pipeline_models import ComposeRequest
from .pipeline_repository import PipelineRepository
from .workflow_models import Workflow, WorkflowStep


class PipelineLibrary:
    def __init__(self, store, registry):
        self.store, self.registry = store, registry
        self.compiler = PipelineCompiler(registry)
        self.repository = PipelineRepository(store, registry, self.compiler)
        self.authoring = PipelineAuthoring(self.repository, self.compiler)

    def compile(self, request: ComposeRequest, identity="preview", version=1):
        return self.compiler.compile(request, identity, version)

    as_method = staticmethod(PipelineCompiler.as_method)

    def records(self):
        return self.repository.records()

    def get(self, identity, version=None):
        return self.repository.get(identity, version)

    def save(self, request, identity=None):
        return self.repository.save(request, identity)

    def trash(self):
        return self.repository.trash()

    def delete(self, identity, expected_version, expected_deletion_revision):
        return self.repository.delete(identity, expected_version, expected_deletion_revision)

    def restore(self, identity, expected_deletion_revision):
        return self.repository.restore(identity, expected_deletion_revision)

    def source(self, identity, version=None):
        from .pipeline_source import pipeline_source
        pipeline = self.get(identity, version)
        return {"editor": pipeline_source(pipeline, self.registry.definitions)}

    def drafts(self):
        return self.authoring.drafts()

    def save_draft(self, identity, request):
        return self.authoring.save_draft(identity, request)

    def draft_trash(self):
        return self.authoring.draft_trash()

    def delete_draft(self, identity, request):
        return self.authoring.delete_draft(identity, request)

    def restore_draft(self, identity, expected_revision):
        return self.authoring.restore_draft(identity, expected_revision)

    def lifecycle(self, identity, version, status):
        return self.authoring.lifecycle(identity, version, status)

    def migration(self, identity, version=None):
        return self.authoring.migration(identity, version)

    def bindings(self):
        self.registry.definitions.load()
        latest = {p["id"]: p for p in self.records()}
        current = []
        for p in latest.values():
            if p.get("status", "published") == "archived":
                continue
            if all(
                self.registry.definitions.methods.get(
                    s.get("method_ref", s.get("method_id"))
                )
                and self.registry.definitions.methods[
                    s.get("method_ref", s.get("method_id"))
                ].version
                == s.get("method_version")
                for s in p["steps"]
            ):
                current.append(p)
        return [
            self.as_method(p)
            for p in [*self.registry.definitions.pipelines.values(), *current]
        ]

    def catalog(
        self, kind, q="", offset=0, limit=50, category="", software="", language="zh"
    ):
        from .catalog_taxonomy import catalog_entries, category_labels, facets

        items = [
            item
            for item in catalog_entries(self.registry.definitions, self.records())
            if item["kind"] == kind
        ]
        terms = q.casefold().split()
        # Index both locale projections, so switching language never hides a
        # canonical command, Chinese task phrase or English scientific term.
        from .catalog_locale import localize

        if terms:
            items = [
                item
                for item in items
                if all(
                    term
                    in (
                        json.dumps(item, ensure_ascii=False)
                        + json.dumps(localize(item, "en"), ensure_ascii=False)
                        + json.dumps(localize(item, "zh"), ensure_ascii=False)
                    ).casefold()
                    for term in terms
                )
            ]
        software_facets = facets(items, language)["software"]
        if software:
            items = [item for item in items if software in item["software"]]
        navigation = facets(items, language)
        navigation["software"] = software_facets
        if category:
            prefix = category.split("/")
            items = [
                item
                for item in items
                if any(
                    path[: len(prefix)] == prefix
                    for path in item.get("categories", [item["category_ids"]])
                )
            ]
        items.sort(
            key=lambda item: (
                item["category_ids"],
                item["label"].casefold(),
                item["id"],
            )
        )
        page = []
        for original in items[offset : offset + limit]:
            item = localize(original, language)
            item["category"] = category_labels(item["category_ids"], language)
            item["description"] = item["description"][:350]
            page.append(item)
        return {
            "items": page,
            "total": len(items),
            "has_more": offset + limit < len(items),
            "next_cursor": offset + limit if offset + limit < len(items) else None,
            "facets": navigation,
        }

    def from_workflow(self, workflow, name):
        # Reuse the real compiler to flatten nested recipes and retain the exact
        # selected methods/parameters; existing file paths become external ports.
        from .execution.recipe_compiler import RecipeCompiler

        units = RecipeCompiler(self.registry).compile(workflow)
        ids = {unit.step.id: "s" + str(i + 1) for i, unit in enumerate(units)}
        steps = []
        for unit in units:
            method = self.registry.definitions.method(
                unit.method.definition_id or unit.method.runner_id
            )
            links = {}
            for slot, b in unit.step.input_bindings.items():
                if b.source == "upstream_output" and b.upstream_step_id in ids:
                    links[slot] = {
                        "step_id": ids[b.upstream_step_id],
                        "output_slot_id": b.output_slot_id,
                        "additional_sources": [
                            {
                                "step_id": ids[v.step_id],
                                "output_slot_id": v.output_slot_id,
                            }
                            for v in b.upstream_sources
                        ],
                    }
            steps.append(
                {
                    "id": ids[unit.step.id],
                    "method_id": method.id,
                    "parameters": {
                        k: v
                        for k, v in unit.step.selected_parameters.items()
                        if k in {p.id for p in method.parameters}
                    },
                    "inputs": links,
                }
            )
        # Preserve explicit shared public ports when exporting a used Pipeline.
        # Read recipe identities rather than guessing equality from path strings.
        shared = {}
        exported = {step["id"]: step for step in steps}
        for parent in workflow.steps:
            selected = next(
                (m for m in parent.method_options if m.id == parent.selected_method_id),
                None,
            )
            if not selected or not selected.execution_recipe:
                continue
            for stage in selected.execution_recipe.steps:
                child_id = ids.get(parent.id + "__" + stage.id)
                if child_id is None:
                    continue
                for slot, binding in stage.input_bindings.items():
                    if (
                        binding.source == "workflow_input"
                        and (binding.slot_id or "").startswith("shared_input_")
                        and slot not in exported[child_id]["inputs"]
                    ):
                        shared.setdefault((parent.id, binding.slot_id), []).append(
                            {"step_id": child_id, "input_slot_id": slot}
                        )
        return self.save(
            ComposeRequest(
                name=name or workflow.title,
                description=workflow.summary,
                steps=steps,
                shared_inputs=[group for group in shared.values() if len(group) > 1],
            )
        )

    def workflow(self, identity, request, service, workspace_service):
        p = self.get(identity, request.version)
        for step in p["steps"]:
            current = self.registry.definitions.method(
                step.get("method_ref", step.get("method_id"))
            )
            if step.get("method_version", current.version) != current.version:
                raise ValueError("方法版本已变化，请复制 Pipeline 并重新校验")
        # The repository already attaches state to this exact version; scanning
        # the full library again is both redundant and a separate read snapshot.
        if p.get("status") == "archived":
            raise ValueError("Pipeline 已归档，请先恢复发布状态")
        if request.conversation_id and not self.store.get_conversation(
            request.conversation_id
        ):
            raise ValueError("Conversation not found")
        method = self.as_method(p)
        dataset = (
            service.dataset_service.get(request.dataset_id)
            if request.dataset_id
            else None
        )
        workspace = (
            workspace_service.get(request.workspace_id)
            if request.workspace_id
            else None
        )
        option = service.catalog.option(
            method,
            set(dataset.inspection.input_types if dataset else []),
            dataset.inspection.features if dataset else {},
            dataset is not None,
        )
        # Immutable recipe snapshot is locked immediately; user can bind files and review
        # parameters through the normal workflow UI before dry-run or confirmation.
        now = datetime.now(timezone.utc).isoformat()
        wf = Workflow(
            conversation_id=request.conversation_id,
            output_layout='sample_steps',
            workflow_id="wf_" + uuid4().hex[:12],
            template_id="pipeline:" + identity,
            title=p["label"],
            summary=p.get("description", ""),
            original_request=p["label"],
            status="awaiting_parameter_review",
            dataset_id=request.dataset_id,
            dataset=dataset,
            workspace_id=request.workspace_id,
            workspace_root_path=workspace.root_path if workspace else None,
            output_relative_path=".",
            planner_source="pipeline-library",
            locked_at=now,
            created_at=now,
            updated_at=now,
            requested_capabilities=[method.capability],
            steps=[
                WorkflowStep(
                    id="pipeline",
                    order=1,
                    title=p["label"],
                    description=p.get("description", ""),
                    capability=method.capability,
                    selection="auto",
                    status="method_selected",
                    method_options=[option],
                    selected_method_id=method.method_id,
                    selected_parameters=service.state.default_parameters(option),
                )
            ],
        )
        service.state.revalidate_chain(wf)
        service.state.resolve_workflow_inputs(wf)
        service.state.refresh(wf)
        self.store.save_workflow(wf, event_type="pipeline_instantiated")
        if request.conversation_id:
            self.store.append_conversation_message(
                request.conversation_id,
                role="assistant",
                content="Pipeline：" + p["label"],
                estimated_tokens=50,
                created_at=now,
                metadata={
                    "workflow_id": wf.workflow_id,
                    "dataset_id": request.dataset_id,
                    "workspace_id": request.workspace_id,
                    "response_type": "workflow",
                },
            )
        return wf
