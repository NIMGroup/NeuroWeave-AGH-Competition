"""Explicit application dependencies; no import-time I/O and no router imports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .capability_registry import CapabilityRegistry
    from .capability_tree import CapabilityTree
    from .chat_coordinator import ChatCoordinator
    from .chat_service import ChatService
    from .chat_turns import ChatTurns
    from .conversation_service import ConversationService
    from .dataset_files import DatasetFiles
    from .dataset_service import DatasetService
    from .data_operations import DataOperations
    from .data_auth import DataAuth
    from .execution import RunnerRegistry, RunService
    from .local_store import LocalStore
    from .operation_service import OperationService
    from .pipeline_library import PipelineLibrary
    from .settings import Settings
    from .skill_catalog import SkillCatalog
    from .skill_loader import SkillLoader
    from .workflow_service import WorkflowService
    from .workspace_service import WorkspaceService
    from .workspace_tools import WorkspaceTools


@dataclass
class ApplicationServices:
    settings: Settings
    skill_loader: SkillLoader
    chat_service: ChatService
    capability_registry: CapabilityRegistry
    capability_tree: CapabilityTree
    local_store: LocalStore
    operation_service: OperationService
    dataset_service: DatasetService
    conversation_service: ConversationService
    workspace_service: WorkspaceService
    dataset_files: DatasetFiles
    workspace_tools: WorkspaceTools
    runner_registry: RunnerRegistry
    skill_catalog: SkillCatalog
    workflow_service: WorkflowService
    run_service: RunService
    pipeline_library: PipelineLibrary
    chat_turns: ChatTurns
    chat_coordinator: ChatCoordinator
    data_operations: DataOperations | None = None
    data_auth: DataAuth | None = None
