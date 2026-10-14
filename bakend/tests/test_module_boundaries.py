"""Refactor contracts: preserve external behavior while enforcing one-way dependencies."""

import ast
import hashlib
import json
from pathlib import Path

import pytest
from app.application import create_app
from app.bootstrap import build_services
from app.settings import Settings
from app.workflow_models import Workflow
from app.workspace_models import WorkspaceCreate
from fastapi import APIRouter, FastAPI, routing
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "bakend/app"
FIXTURES = Path(__file__).parent / "fixtures"


def imported_modules(path):
    return {
        n.module or ""
        for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(n, ast.ImportFrom)
    } | {
        alias.name
        for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(n, ast.Import)
        for alias in n.names
    }


@pytest.mark.parametrize(
    "relative, forbidden",
    [
        (
            "llm",
            {
                "chat_service",
                "local_store",
                "workflow_service",
                "pipeline_library",
                "main",
                "fastapi",
            },
        ),
        (
            "workflows",
            {"workflow_service", "main", "application", "bootstrap", "fastapi"},
        ),
        (
            "pipeline_compiler.py",
            {
                "local_store",
                "pipeline_library",
                "pipeline_repository",
                "chat_service",
                "main",
                "fastapi",
            },
        ),
        (
            "pipeline_models.py",
            {"local_store", "pipeline_library", "pipeline_compiler", "main", "fastapi"},
        ),
    ],
)
def test_inner_components_do_not_import_their_controllers(relative, forbidden):
    path = APP / relative
    files = path.glob("*.py") if path.is_dir() else [path]
    for file in files:
        imports = {module.split(".")[-1] for module in imported_modules(file)}
        assert not imports & forbidden, (file.name, imports & forbidden)


def test_pipeline_composition_and_pure_compiler_have_no_fake_initialization():
    tree = ast.parse((APP / "pipeline_library.py").read_text(encoding="utf-8"))
    library = next(
        n
        for n in tree.body
        if isinstance(n, ast.ClassDef) and n.name == "PipelineLibrary"
    )
    assert not library.bases, (
        "Authoring and persistence are composed, not mixins with implicit attributes"
    )
    source = (APP / "method_planning.py").read_text(encoding="utf-8")
    assert "object.__new__" not in source
    assert "PipelineCompiler(registry)" in source


def test_model_policy_prompts_match_reviewed_contract():
    # Scope changes explicitly review/update the affected prompt fingerprint;
    # moving modules alone must not silently alter the model contract.
    expected = json.loads((FIXTURES / "refactor_prompt_contract.json").read_text())
    seen = {}
    for path in (APP / "llm").glob("*.py"):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in expected
            ):
                docstring = (
                    node.body[0].value if isinstance(node.body[0], ast.Expr) else None
                )
                strings = [
                    value.value
                    for value in ast.walk(node)
                    if isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                    and len(value.value) > 80
                    and value is not docstring
                ]
                seen[node.name] = hashlib.sha256(
                    json.dumps(strings, ensure_ascii=False).encode()
                ).hexdigest()
    assert seen == expected, "Review prompt changes separately from module moves"


@pytest.fixture
def application(tmp_path, monkeypatch):
    (tmp_path / "datasets").mkdir()
    monkeypatch.setenv("WORKSPACE_ALLOWED_ROOTS", str(tmp_path))
    monkeypatch.setenv("WORKSPACE_DEFAULT_ROOT", str(tmp_path))
    monkeypatch.setenv("LOCALFLOW_DATA_ROOTS", str(tmp_path / "datasets"))
    monkeypatch.setenv("LOCALFLOW_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("LOCALFLOW_LLM_BASE_URL", "")
    settings = Settings(
        ROOT,
        ROOT / "bakend",
        ROOT / "skills",
        tmp_path / "runtime/api.db",
        tmp_path / "outputs",
        (),
    )
    services = build_services(settings)
    app = create_app(services)
    with TestClient(app) as client:
        yield app, client, services


def http_route_contexts(app):
    """Inspect effective paths/defaults, including lazy included routers in FastAPI.

    The locked FastAPI version exposes iter_route_contexts for this traversal.
    Older development installs store the effective APIRoutes directly instead.
    Neither branch may silently omit included endpoints or hide duplicate routes.
    """
    contexts = getattr(routing, "iter_route_contexts", iter)(app.routes)
    return [
        route
        for route in contexts
        if isinstance(getattr(route, "original_route", route), APIRoute)
    ]


def test_split_routes_preserve_paths_status_codes_and_response_models(application):
    app, client, _ = application
    effective = http_route_contexts(app)
    routes = {
        (method, route.path): route
        for route in effective
        for method in route.methods
    }
    expected = json.loads((FIXTURES / "refactor_api_contract.json").read_text())
    schema = client.get("/openapi.json").json()
    for item in expected:
        key = item["method"], item["path"]
        assert key in routes, f"Missing effective route: {key}"
        route = routes[key]
        assert (route.status_code or 200) == item["status"], item
        if item["response"]:
            assert route.response_model.__name__ == item["response"], item
        operation = schema["paths"][item["path"]][item["method"].lower()]
        assert str(item["status"]) in operation["responses"], item
    keys = [(method, route.path) for route in effective for method in route.methods]
    assert len(keys) == len(set(keys)), "An endpoint must not be registered twice"
    root = client.get("/")
    assert root.status_code == 200
    assert root.json() == {"name": "NeuroWeave API", "docs": "/docs"}
    assert client.get("/api/health?include_runtime=false").json()["status"] == "ok"
    assert client.get("/api/chat/turns").json() == {"turns": []}


def test_route_contract_traversal_keeps_nested_prefixes_metadata_and_duplicates():
    class Payload(BaseModel):
        value: str

    leaf = APIRouter()

    @leaf.post("/item", response_model=Payload, status_code=201)
    def item():
        return {"value": "ok"}

    parent = APIRouter(prefix="/parent")
    parent.include_router(leaf, prefix="/child")
    app = FastAPI()
    app.include_router(parent, prefix="/api")
    effective = http_route_contexts(app)
    assert [(r.path, r.status_code, r.response_model) for r in effective] == [
        ("/api/parent/child/item", 201, Payload)
    ]
    with TestClient(app) as client:
        response = client.post("/api/parent/child/item")
        assert response.status_code == 201
        assert response.json() == {"value": "ok"}
    # Do not collapse duplicates into a dictionary before checking uniqueness.
    app.include_router(parent, prefix="/api")
    assert len(http_route_contexts(app)) == 2


def test_retired_skill_compatibility_endpoints_are_unavailable(application):
    _, client, _ = application
    assert client.get("/api/skills/graph").status_code == 404
    assert client.get("/api/skills/software-fsl/methods").status_code == 404
    assert client.get("/api/skills/fsl-tool/methods").status_code == 200


def test_output_adapter_keeps_revision_and_conversation_binding_consistent(
    application, tmp_path
):
    _, client, services = application
    target = tmp_path / "managed"
    target.mkdir()
    workspace = services.workspace_service.create(
        WorkspaceCreate(name="Managed", root_path=str(target))
    )
    conversation = services.conversation_service.create("Output change")
    workflow = Workflow(
        workflow_id="wf_refactor",
        template_id="refactor",
        title="Refactor",
        summary="",
        original_request="test",
        status="awaiting_method_choice",
        steps=[],
        created_at="2026-09-27",
        updated_at="2026-09-27",
    )
    services.local_store.save_workflow(workflow, event_type="workflow_created")
    response = client.patch(
        "/api/workflows/wf_refactor/output",
        json={
            "workspace_id": workspace.id,
            "conversation_id": conversation.conversation_id,
            "expected_revision": 1,
            "relative_path": ".",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 2
    assert response.json()["workspace_id"] == workspace.id
    assert (
        services.local_store.get_conversation(conversation.conversation_id).workspace_id
        == workspace.id
    )
    assert services.local_store.get_workflow("wf_refactor").workspace_root_path == str(
        target
    )
    conflict = client.patch(
        "/api/workflows/wf_refactor/output",
        json={"expected_revision": 1, "relative_path": "."},
    )
    assert conflict.status_code == 409
    assert services.local_store.get_workflow("wf_refactor").revision == 2
