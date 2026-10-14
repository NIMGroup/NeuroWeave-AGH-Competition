"""WorkflowSession: one lock and repository boundary shared by all mutations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..workflow_models import (
    Workflow,
)

if TYPE_CHECKING:
    from _thread import RLock

    from ..local_store import LocalStore
    from .state import WorkflowState


class WorkflowSession:
    def __init__(self, store: LocalStore, state: WorkflowState, lock: RLock) -> None:
        self.store = store
        self.state = state
        self.lock = lock

    def require(self, workflow_id: str) -> Workflow:
        workflow = self.store.get_workflow(workflow_id)
        if workflow is None:
            raise KeyError(workflow_id)
        if workflow.locked_at is None and workflow.status != "confirmed":
            # Compatibility and display order are derived from current declarations.
            # Reading old drafts does not rewrite their persisted snapshots.
            self.state.revalidate_chain(workflow, check_runtime=False)
            self.state.refresh(workflow)
        return workflow

    def save(
        self,
        workflow: Workflow,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.store.save_workflow(workflow, event_type=event_type, event_payload=payload)
