"""Shared HTTP translation; domain revision failures retain their current snapshot."""

from fastapi import HTTPException

from ..workflow_service import RevisionConflict


def revision_http_error(exc: RevisionConflict) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "revision_conflict",
            "message": str(exc),
            "current": exc.current.model_dump(mode="json"),
        },
    )


def submit_operation(operation_service, kind, key, action):
    try:
        return operation_service.submit(kind, key, action)
    except ValueError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
