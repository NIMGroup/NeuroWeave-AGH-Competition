"""HTTP-only confirmation endpoints are intentionally absent from model tools."""
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.routing import APIRoute

from .workspace_models import WorkspaceCreate, WorkspaceUpdate, FileOperationRequest, DirectoryCreationRequest, ConfirmationRequest, RestoreRequest


class FilesystemRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def guarded(request: Request):
            try:
                return await handler(request)
            except PermissionError as exc:
                raise HTTPException(403, str(exc)) from exc
            except (FileNotFoundError, KeyError) as exc:
                raise HTTPException(404, str(exc)) from exc
            except (ValueError, OSError) as exc:
                raise HTTPException(409, str(exc)) from exc
        return guarded


def workspace_router(service, dataset_files=None):
    router = APIRouter(route_class=FilesystemRoute)

    @router.get("/api/fs/roots")
    def roots():
        return {"roots": [str(p) for p in service.paths.roots], "default_root": str(service.paths.default),
                "page_size": service.paths.page_size, "text_max_chars": service.paths.text_max}

    @router.get("/api/workspaces")
    @router.get("/api/output-directories")
    def workspaces():
        return service.list()

    @router.post("/api/workspaces")
    @router.post("/api/output-directories")
    def create(request: WorkspaceCreate):
        return service.create(request)

    @router.patch("/api/workspaces/{workspace_id}")
    @router.patch("/api/output-directories/{workspace_id}")
    def update(workspace_id: str, request: WorkspaceUpdate):
        return service.update(workspace_id, request)

    @router.delete("/api/workspaces/{workspace_id}")
    @router.delete("/api/output-directories/{workspace_id}")
    def remove(workspace_id: str):
        return service.remove(workspace_id)

    @router.get("/api/fs/list")
    def listing(workspace_id: str | None = None, root_path: str | None = None, path: str = ".",
                cursor: str | None = None, limit: int | None = Query(None, ge=1, le=200),
                sort: str = "name", filter: str = "", show_hidden: bool = False, filter_mode: str = 'text'):
        return service.list_directory(path, workspace_id, root_path, cursor, limit, sort, filter, show_hidden, filter_mode)

    @router.get("/api/fs/inspect")
    def inspect(path: str = ".", workspace_id: str | None = None, root_path: str | None = None):
        return service.inspect(path, workspace_id, root_path)

    @router.get("/api/fs/read-text")
    def text(path: str, workspace_id: str | None = None, root_path: str | None = None,
             offset: int = Query(0, ge=0, le=2000000), max_chars: int | None = Query(None, ge=1, le=100000)):
        return service.read_text(path, workspace_id, root_path, offset, max_chars)

    @router.get("/api/fs/search")
    def search(path: str = ".", workspace_id: str | None = None, root_path: str | None = None,
               pattern: str = "*", max_depth: int = Query(2, ge=0, le=5), max_results: int = Query(100, ge=1, le=200), follow_links: bool = False):
        return service.search(path, workspace_id, root_path, pattern, max_depth, max_results, follow_links=follow_links)

    def proposal(action):
        def propose(request: FileOperationRequest):
            return service.propose(action, request)
        return propose

    @router.post("/api/fs/mkdir")
    def mkdir(request: DirectoryCreationRequest):
        return service.propose("mkdir", request)

    for action in ("copy", "move", "rename", "delete", "read-sensitive"):
        router.add_api_route(f"/api/fs/{action}", proposal(action), methods=["POST"], name=f"fs_{action}")

    @router.get("/api/fs/operations/{operation_id}")
    def operation(operation_id: str):
        return service.operation(operation_id)

    @router.post("/api/fs/operations/{operation_id}/confirm")
    def confirm(operation_id: str, request: ConfirmationRequest):
        return service.confirm(operation_id, request.conversation_id)

    @router.post("/api/fs/operations/{operation_id}/cancel")
    def cancel(operation_id: str, request: ConfirmationRequest):
        return service.cancel(operation_id, request.conversation_id)

    @router.get("/api/workspaces/{workspace_id}/trash")
    @router.get("/api/output-directories/{workspace_id}/trash")
    def trash(workspace_id: str):
        return service.trash(workspace_id)

    @router.post("/api/fs/trash/{trash_id}/restore")
    def restore(trash_id: str, request: RestoreRequest):
        return service.propose_restore(trash_id, request.conversation_id, request.destination)

    @router.get("/api/workspace-tool-events")
    def events(turn_id: str = Query(min_length=16, max_length=100)):
        return service.events(turn_id)

    if dataset_files is not None:
        @router.get('/api/datasets/{dataset_id}/files/list')
        def dataset_list(dataset_id: str, path: str = '.', cursor: str | None = None,
                         limit: int | None = Query(None, ge=1, le=200), sort: str = 'name',
                         filter: str = '', show_hidden: bool = False, filter_mode: str = 'text'):
            return dataset_files.reader(dataset_id).list_directory(path=path, cursor=cursor, limit=limit,
                sort=sort, filter=filter, show_hidden=show_hidden, filter_mode=filter_mode)

        @router.get('/api/datasets/{dataset_id}/files/inspect')
        def dataset_inspect(dataset_id: str, path: str = '.'):
            return dataset_files.reader(dataset_id).inspect(path)

        @router.get('/api/datasets/{dataset_id}/files/read-text')
        def dataset_text(dataset_id: str, path: str, offset: int = Query(0, ge=0, le=2000000),
                         max_chars: int | None = Query(None, ge=1, le=100000)):
            return dataset_files.reader(dataset_id).read_text(path, offset=offset, max_chars=max_chars)

        @router.get('/api/datasets/{dataset_id}/files/search')
        def dataset_search(dataset_id: str, path: str = '.', pattern: str = '*',
                           max_depth: int = Query(2, ge=0, le=5), max_results: int = Query(100, ge=1, le=200), follow_links: bool = False):
            return dataset_files.reader(dataset_id).search(path=path, pattern=pattern,
                                                          max_depth=max_depth, max_results=max_results, follow_links=follow_links)

    return router
