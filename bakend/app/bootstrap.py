"""Application composition and lifetime wiring.

Construct one service graph per process. The shared run lock prevents filesystem
changes from racing a submission; shutting down web monitoring must not cancel Slurm.
"""

from __future__ import annotations

from pathlib import Path

from .application_services import ApplicationServices
from .capability_registry import CapabilityRegistry
from .capability_tree import CapabilityTree
from .chat_coordinator import ChatCoordinator
from .chat_service import ChatService
from .chat_turns import ChatTurns
from .conversation_service import ConversationService
from .dataset_files import DatasetFiles
from .dataset_service import DatasetService
from .execution import RunnerRegistry, RunService
from .local_store import LocalStore
from .operation_service import OperationService
from .pipeline_library import PipelineLibrary
from .settings import Settings
from .skill_catalog import SkillCatalog
from .skill_loader import SkillLoader
from .workflow_models import (
    Workflow,
)
from .workflow_service import WorkflowService
from .workspace_service import WorkspaceService
from .workspace_tools import WorkspaceTools
from .data_operations import DataOperations
from .data_auth import DataAuth


def build_services(settings: Settings) -> ApplicationServices:
    skill_loader = SkillLoader(settings.skills_dir)
    chat_service = ChatService()
    capability_registry = CapabilityRegistry(settings.skills_dir)
    capability_tree = CapabilityTree(settings.skills_dir / "capability.tree.json")
    local_store = LocalStore(settings.database_path)
    operation_service = OperationService(local_store)
    dataset_service = DatasetService(settings.project_root, local_store)
    conversation_service = ConversationService(local_store, chat_service)
    workspace_service = WorkspaceService(
        local_store, settings.project_root / "workspaces"
    )
    dataset_files = DatasetFiles(dataset_service)
    workspace_tools = WorkspaceTools(workspace_service, chat_service, dataset_files)
    runner_registry = RunnerRegistry()
    skill_catalog = SkillCatalog(skill_loader, capability_registry, runner_registry)
    workspace_tools.skill_catalog = skill_catalog
    workflow_service = WorkflowService(
        chat_service,
        capability_registry,
        local_store,
        dataset_service,
        capability_tree,
        skill_loader,
        runner_registry.available_ids(),
        runner_registry,
    )
    run_service = RunService(
        local_store,
        capability_registry,
        runner_registry,
        settings.output_root,
    )

    def _protected_workspace_paths():
        # Reads remain available; mutations cannot invalidate queued/running jobs.
        protected = [
            settings.database_path.parent,
            settings.project_root / "bakend",
            settings.project_root / "skills",
            settings.project_root / "deploy",
            settings.project_root / ".conda",
        ]
        for run in local_store.incomplete_runs():
            protected.append(Path(run.output_directory))
            workflow = Workflow.model_validate(run.plan_snapshot)
            if workflow.dataset:
                protected.append(Path(workflow.dataset.absolute_path))
            for step in workflow.steps:
                for binding in step.input_bindings.values():
                    protected.extend(
                        Path(p)
                        for p in binding.absolute_paths
                        or ([binding.absolute_path] if binding.absolute_path else [])
                    )
                    protected.extend(
                        Path(match.absolute_path)
                        for match in binding.sample_matches
                        if match.absolute_path
                    )
        return protected

    workspace_service.protected_paths = _protected_workspace_paths
    workspace_service.run_lock = run_service.mutation_lock
    dataset_service.registration_lock = run_service.mutation_lock
    dataset_service.registration_guard = workspace_service.guard_dataset_registration
    data_operations = DataOperations(workspace_service, dataset_files)
    data_auth = DataAuth(local_store)
    workspace_tools.data_operations = data_operations
    base_protected = workspace_service.protected_paths

    def data_reserved_paths():
        with local_store._connect() as db:
            import json
            rows = db.execute("SELECT payload FROM data_operations WHERE status IN ('validating','queued','analyzing','cancel_requested','running','rolling_back','interrupted')").fetchall()
        paths = []
        for row in rows:
            task = json.loads(row[0])
            paths.append(Path(task["target_root"]))
            paths.append(Path(task["source_root"]))
        return paths

    workspace_service.protected_paths = lambda: [*base_protected(), *data_reserved_paths()]
    pipeline_library = PipelineLibrary(local_store, capability_registry)
    capability_registry.library_provider = pipeline_library.bindings
    chat_coordinator = ChatCoordinator(
        conversation_service,
        workspace_service,
        dataset_files,
        workspace_tools,
        skill_loader,
        workflow_service,
        chat_service,
    )
    workspace_tools.workflow_service = workflow_service
    chat_turns = ChatTurns(local_store, conversation_service, chat_coordinator.reply)
    return ApplicationServices(
        settings=settings,
        skill_loader=skill_loader,
        chat_service=chat_service,
        capability_registry=capability_registry,
        capability_tree=capability_tree,
        local_store=local_store,
        operation_service=operation_service,
        dataset_service=dataset_service,
        conversation_service=conversation_service,
        workspace_service=workspace_service,
        dataset_files=dataset_files,
        workspace_tools=workspace_tools,
        runner_registry=runner_registry,
        skill_catalog=skill_catalog,
        workflow_service=workflow_service,
        run_service=run_service,
        pipeline_library=pipeline_library,
        chat_turns=chat_turns,
        chat_coordinator=chat_coordinator,
        data_operations=data_operations,
        data_auth=data_auth,
    )
