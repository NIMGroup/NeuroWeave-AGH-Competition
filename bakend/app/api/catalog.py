"""Catalog HTTP endpoints; dependencies are supplied by the application factory."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ..application_services import ApplicationServices


def router_for(services: ApplicationServices) -> APIRouter:
    router = APIRouter()

    @router.get("/api/capabilities/tree")
    def capabilities_tree() -> dict[str, Any]:
        return {
            "tree": services.capability_tree.compact(),
            "stats": services.capability_tree.stats(),
            "registered_capabilities": services.capability_registry.capabilities(),
        }

    @router.get("/api/artifacts/types")
    def artifact_types() -> dict[str, Any]:
        return {
            "artifact_types": [
                item.model_dump(mode="json")
                for item in services.capability_registry.artifact_types()
            ]
        }

    @router.get("/api/skills")
    def list_skills(q: str = "", source: str = "") -> dict[str, Any]:
        skills = services.skill_loader.load_all()
        normalized_query = q.strip().lower()
        normalized_source = source.strip().lower()
        if normalized_query:
            skills = [
                skill
                for skill in skills
                if normalized_query
                in " ".join(
                    (
                        skill.name,
                        skill.display_name,
                        skill.description,
                        skill.summary_en,
                        skill.summary_zh,
                        skill.skill_type,
                    )
                ).lower()
            ]
        if normalized_source:
            skills = [
                skill for skill in skills if skill.source.lower() == normalized_source
            ]
        skill_stats = services.skill_loader.stats()
        skill_stats["execution_enabled"] = services.run_service.enabled
        methods_by_skill: dict[str, list[Any]] = {}
        for method in services.capability_registry.all_methods():
            methods_by_skill.setdefault(method.skill_id, []).append(method)
        runtime_statuses: dict[str, dict] = {}
        return {
            "skills": [
                services.skill_catalog.execution_status(
                    skill, methods_by_skill.get(skill.name, []), runtime_statuses
                )
                for skill in skills
            ],
            "stats": {
                **skill_stats,
                "capability_catalog": services.capability_registry.stats(),
            },
        }

    @router.get("/api/skills/{skill_name}")
    def get_skill(skill_name: str) -> dict[str, Any]:
        skill = services.skill_loader.find(skill_name)
        if skill is None:
            raise HTTPException(status_code=404, detail="Skill not found")
        files = [
            str(path.relative_to(services.settings.project_root)).replace("\\", "/")
            for path in sorted(skill.path.rglob("*"))
            if path.is_file()
        ]
        return {
            **services.skill_catalog.execution_status(skill),
            "instructions": skill.overview or skill.prompt_excerpt(),
            "references": [reference.public_dict() for reference in skill.references],
            "orchestration": services.skill_catalog.skill_structure(skill.name),
            "layered": bool(skill.overview),
            "files": files,
        }

    @router.get("/api/skills/{skill_name}/methods")
    def skill_methods(
        skill_name: str,
        q: str = Query("", max_length=500),
        cursor: str = "",
        limit: int = Query(20, ge=1, le=50),
        executable_only: bool = False,
        kind: str = "method",
    ):
        try:
            return services.skill_catalog.list_methods(
                skill_name, q, cursor, limit, executable_only, kind
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/api/methods/{method_id}")
    def atomic_method(method_id: str):
        try:
            return services.skill_catalog.method("method", method_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.get("/api/pipelines/{pipeline_id}")
    def pipeline_detail(pipeline_id: str):
        try:
            return services.skill_catalog.pipeline(pipeline_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.get("/api/skills/{skill_name}/reference")
    def skill_reference(
        skill_name: str,
        offset: int = Query(0, ge=0, le=2000000),
        max_chars: int = Query(6000, ge=1, le=12000),
        reference_id: str = Query("", max_length=80),
    ):
        try:
            return services.skill_catalog.read_reference(skill_name, offset, max_chars, reference_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    return router
