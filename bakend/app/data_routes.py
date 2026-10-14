from fastapi import APIRouter, Query

from .data_models import DataConfirmation, DataScriptConfirmation, DataPlanRequest, OrganizeRequest, PythonRequest
from .workspace_routes import FilesystemRoute


def data_router(service):
    router = APIRouter(route_class=FilesystemRoute)

    @router.get('/api/data-environments')
    def environments():
        return service.environments()

    @router.get("/api/data-operations")
    def listing(conversation_id: str):
        return service.list(conversation_id)

    @router.post("/api/data-operations/preview")
    def preview(request: DataPlanRequest):
        return service.propose(request)

    @router.post("/api/data-operations/organize")
    def organize(request: OrganizeRequest):
        return service.organize(request)

    @router.post("/api/data-operations/python")
    def python(request: PythonRequest):
        return service.python(request)

    @router.get("/api/data-operations/{identity}")
    def get(identity: str, conversation_id: str):
        return service.get(identity, conversation_id)

    @router.get('/api/data-operations/{identity}/progress')
    def progress(identity: str, conversation_id: str):
        return service.progress(identity, conversation_id)

    @router.post("/api/data-operations/{identity}/confirm")
    def confirm(identity: str, request: DataConfirmation):
        return service.confirm(identity, request)

    @router.post("/api/data-operations/{identity}/run-script")
    def run_script(identity: str, request: DataScriptConfirmation):
        return service.start_python(identity, request)

    @router.post("/api/data-operations/{identity}/undo")
    def undo(identity: str, request: DataConfirmation):
        return service.undo(identity, request)

    @router.post("/api/data-operations/{identity}/cancel")
    def cancel(identity: str, conversation_id: str):
        return service.cancel(identity, conversation_id)

    @router.get("/api/data-operations/{identity}/manifest")
    def manifest(identity: str, conversation_id: str):
        # Export the full reviewable mapping, without content, backup paths or grants.
        service.get(identity, conversation_id)
        task = service._raw(identity, 'state')
        return {"manifest_hash": task["manifest_hash"],
                "items": [{k: m[k] for k in ("action", "path", "destination", "size")} for m in service.repository.items(identity)]}

    @router.get("/api/data-operations/{identity}/files")
    def files(identity: str, conversation_id: str, kind: str = "unmatched",
              offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200), download: bool = False):
        return service.operation_files(identity, conversation_id, kind, offset, limit, full=download)

    @router.get("/api/data-operations/{identity}/log")
    def execution_log(identity: str, conversation_id: str):
        import os
        from fastapi.responses import StreamingResponse
        from . import workspace_io
        service.get(identity, conversation_id)
        path = service.directory / identity / 'execution.log'
        service._artifact_path(path)
        stream = os.fdopen(workspace_io.open_read(path), 'rb')
        def chunks():
            try:
                yield from iter(lambda: stream.read(1024 * 1024), b'')
            finally:
                stream.close()
        return StreamingResponse(chunks(), media_type='text/plain; charset=utf-8',
            headers={'Content-Disposition': 'attachment; filename="analysis-result.log"'})

    @router.get("/api/data-operations/{identity}/artifacts/{index}")
    def artifact(identity: str, index: int, conversation_id: str):
        from pathlib import Path
        from fastapi.responses import StreamingResponse
        from .data_operations import digest
        from . import workspace_io
        service.get(identity, conversation_id)
        task = service._raw(identity, 'state')
        if index < 0 or index >= task['file_count']:
            raise ValueError("输出文件不存在")
        item = service.repository.items(identity, index, 1)[0]
        if item.get("content") is not None:
            import hashlib
            from fastapi.responses import Response
            from urllib.parse import quote
            content = item["content"].encode("utf-8")
            if hashlib.sha256(content).hexdigest() != item["content_hash"]:
                raise ValueError("文本新版本发生变化")
            return Response(content, media_type="application/octet-stream", headers={
                "Content-Disposition": "attachment; filename*=UTF-8''" + quote(Path(item["destination"] or item["path"]).name)})
        if not item.get("artifact"):
            raise ValueError("此操作没有脚本输出文件")
        path = Path(item["artifact"])
        service._artifact_path(path)
        if digest(path) != item["content_hash"]:
            raise ValueError("输出文件发生变化")
        import os
        from urllib.parse import quote
        stream = os.fdopen(workspace_io.open_read(path), "rb")
        def chunks():
            try:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    yield block
            finally:
                stream.close()
        return StreamingResponse(chunks(), media_type="application/octet-stream", headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(path.name)})

    return router
