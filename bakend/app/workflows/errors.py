"""Revision conflict carries the current snapshot for optimistic concurrency recovery."""

from ..workflow_models import Workflow


class RevisionConflict(ValueError):
    def __init__(self, current: Workflow) -> None:
        super().__init__(f"流程版本已更新，当前版本为 R{current.revision}")
        self.current = current
