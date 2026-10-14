"""Health HTTP endpoints; dependencies are supplied by the application factory."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from ..application_services import ApplicationServices
from ..maintenance import inventory_outputs, storage_status


def router_for(services: ApplicationServices) -> APIRouter:
    router = APIRouter()

    @router.get("/")
    def root() -> dict[str, str]:
        return {"name": "NeuroWeave API", "docs": "/docs"}

    @router.get("/api/health")
    def health(include_runtime: bool = True) -> dict[str, Any]:
        skill_stats = services.skill_loader.stats()
        skill_stats["execution_enabled"] = services.run_service.enabled
        result = {
            "status": "ok",
            "release": {
                "api_contract": "2",
                "frontend_revision": os.getenv(
                    "NEUROWEAVE_RELEASE_REVISION", "development"
                ),
                "backend_revision": os.getenv(
                    "NEUROWEAVE_RELEASE_REVISION", "development"
                ),
                "worker_revision": Path(
                    os.getenv("LOCALFLOW_SLURM_WORKER_ROOT", "unversioned")
                ).name,
                "maintenance": (
                    services.settings.database_path.parent / "release-maintenance.lock"
                ).exists(),
            },
            "mode": services.chat_service.mode,
            "model": services.chat_service.model or "conversation-demo",
            "prompt_version": services.chat_service.prompt_version,
            "assistant_prompt_version": "agent-v23-port-grounded-goals",
            "assistant_features": {"on_demand_specialists": True, "shared_conversation_facts": True,
                "fact_owner_isolation": True, "lossless_method_contracts": True,
                "model_process_compaction": True, "resumable_file_scan": True,
                "explicit_unknown_coverage": True, "direct_registered_graph": True,
                "manual_execution_required": True, "typed_fact_merge": True,
                "stable_fact_summaries": True, "compact_candidates": True,
                "sequence_verification": True, "final_report_truncation_recovery": True,
                "plan_scoped_configuration": True, "runtime_dependency_validation": True,
                "versioned_configuration_cache": True, "workflow_output_preview": True,
                "resumable_workflow_checks": True, "user_declared_planning_facts": True,
                "conditional_gradient_inputs": True, "dry_run_content_validation": True,
                "portable_preflight_cache": True, "user_selected_workflow_inputs":True,
                "draft_delivery_without_bindings":True, "reusable_input_rules":True},
            "thinking": services.chat_service.thinking
            if services.chat_service.mode == "model"
            else "disabled",
            "generation_budget": {
                "assistant_initial_tokens": services.chat_service._generation_options(services.chat_service.assistant_max_tokens)["max_tokens"],
                "maximum_tokens": services.chat_service.generation_max_tokens,
                "tool_result_chars": services.chat_service.assistant_tool_result_chars,
                "tool_transcript_chars": services.chat_service.assistant_context_chars,
                "request_timeout_seconds": services.chat_service.request_timeout_seconds,
                "turn_timeout_seconds": services.chat_service.assistant_turn_timeout_seconds,
            },
            "services.skill_catalog": skill_stats,
            "capability_catalog": services.capability_registry.stats(),
            "services.capability_tree": services.capability_tree.stats(),
            "conversation_memory": {
                "compact_trigger_tokens": services.conversation_service.compact_trigger_tokens,
                "keep_recent_messages": services.conversation_service.keep_recent_messages,
                "storage": "sqlite",
            },
            "persistence": "sqlite",
            "dataset_roots": len(services.dataset_service.roots()),
            "execution": {
                "enabled": services.run_service.enabled,
                "runner_count": len(services.runner_registry.ids()),
                "output_root": str(services.settings.output_root),
            },
        }
        if include_runtime:
            runners = services.runner_registry.public_catalog()
            result["execution"].update(
                ready_runner_count=sum(bool(item.get("available")) for item in runners),
                runners=runners,
            )
        if os.getenv('NEUROWEAVE_AGH_SERVER') == 'true':
            import json
            result.update(mode='agh-server', model='managed-by-AGH', thinking='managed-by-AGH',
                prompt_version='agh-neuroweave-domain-v3-3003', assistant_prompt_version='agh-neuroweave-domain-v3-3003',
                generation_budget={'owner': 'AGH', 'source': 'installed neuroweave-roi preset'},
                conversation_memory={'owner': 'AGH', 'storage': 'official session store'},
                business_baseline=json.loads((Path(__file__).resolve().parents[3] / 'integrations/agh/baseline.json').read_text()))
            # The copied business version has its own coordinator/specialist
            # loop. AGH owns model orchestration here; these services alone do
            # not claim that the old specialist loop is also running.
            result['assistant_features'].update(on_demand_specialists=False, model_process_compaction=False,
                agh_model_loop=True, agh_context_compaction=True, shared_conversation_facts=True)
        return result

    def _storage_paths():
        backups = Path(
            os.getenv(
                "LOCALFLOW_BACKUP_ROOT",
                str(services.settings.database_path.parent / "backups"),
            )
        )
        roots = [
            services.settings.output_root,
            Path(
                os.getenv(
                    "LOCALFLOW_SLURM_OUTPUT_ROOT", str(services.settings.output_root)
                )
            ),
        ]
        return backups, roots

    @router.get("/api/maintenance/storage")
    def maintenance_storage() -> dict[str, Any]:
        backups, roots = _storage_paths()
        return {
            **storage_status(services.settings.database_path, backups, roots),
            "backend_revision": os.getenv("NEUROWEAVE_RELEASE_REVISION", "development"),
        }

    @router.post("/api/maintenance/inventory-jobs", status_code=202)
    def maintenance_inventory() -> dict[str, Any]:
        _, roots = _storage_paths()
        try:
            return services.operation_service.submit(
                "storage_inventory",
                "outputs",
                lambda: inventory_outputs(services.settings.database_path, roots),
            )
        except ValueError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc

    return router
