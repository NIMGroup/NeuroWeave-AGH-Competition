from __future__ import annotations

import asyncio
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

import httpx


class ConversationApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_pagination_validation_and_archived_delete_http_contract(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.dict(
                os.environ,
                {
                    "LOCALFLOW_DATABASE_PATH": str(root / "api.db"),
                    "LOCALFLOW_OUTPUT_ROOT": str(root / "outputs"),
                    "LOCALFLOW_DATA_ROOTS": str(root),
                    "LOCALFLOW_EXECUTION_ENABLED": "false",
                },
            ):
                previous = sys.modules.get("app.main")
                if previous is not None:
                    previous.app.state.services.run_service.shutdown()
                main = (
                    importlib.reload(previous)
                    if previous is not None
                    else importlib.import_module("app.main")
                )
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=main.app),
                    base_url="http://test",
                ) as client:
                    for index in range(23):
                        main.app.state.services.conversation_service.create(
                            f"API 测试 {index}"
                        )
                    first = await client.get("/api/conversations")
                    self.assertEqual(first.status_code, 200)
                    page = first.json()
                    self.assertEqual(len(page["conversations"]), 20)
                    self.assertTrue(page["has_more"])
                    second = (
                        await client.get(
                            "/api/conversations",
                            params={
                                "cursor": page["next_cursor"],
                            },
                        )
                    ).json()
                    self.assertEqual(len(second["conversations"]), 3)
                    self.assertFalse(second["has_more"])
                    self.assertEqual(
                        (
                            await client.get(
                                "/api/conversations", params={"cursor": "%%%"}
                            )
                        ).status_code,
                        422,
                    )
                    self.assertEqual(
                        (
                            await client.get(
                                "/api/conversations", params={"limit": 500}
                            )
                        ).status_code,
                        422,
                    )
                    conversation_id = page["conversations"][0]["conversation_id"]
                    url = f"/api/conversations/{conversation_id}"
                    self.assertEqual((await client.delete(url)).status_code, 409)
                    self.assertEqual(
                        (await client.patch(url, json={"archived": True})).status_code,
                        200,
                    )
                    self.assertEqual((await client.delete(url)).status_code, 204)
                    self.assertEqual((await client.get(url)).status_code, 404)
                    self.assertEqual((await client.delete(url)).status_code, 404)
                    cors = await client.options(
                        url,
                        headers={
                            "Origin": "http://localhost:3000",
                            "Access-Control-Request-Method": "DELETE",
                        },
                    )
                    self.assertEqual(cors.status_code, 200)
                    with patch.object(
                        main.app.state.services.runner_registry,
                        "public_catalog",
                        side_effect=AssertionError("Light health must not probe tools"),
                    ):
                        light = await client.get(
                            "/api/health", params={"include_runtime": "false"}
                        )
                    self.assertEqual(light.status_code, 200)
                    self.assertNotIn("runners", light.json()["execution"])
                    self.assertNotIn("summary", page["conversations"][0])

                    from app.models import ChatRequest

                    with (
                        patch.object(
                            main.app.state.services.workflow_service.catalog,
                            "runner_registry",
                            None,
                        ),
                        patch.object(
                            main.app.state.services.chat_service, "base_url", ""
                        ),
                    ):
                        (
                            workflow,
                            _,
                        ) = await main.app.state.services.workflow_service.draft(
                            ChatRequest(
                                message="计算FOD并追踪", interaction_mode="task"
                            ),
                            {
                                "recommend_workflow": True,
                                "capability_ids": ["dwi.fod", "dwi.tractography"],
                            },
                        )
                        with patch.object(
                            main.app.state.services.run_service, "create"
                        ) as submit:
                            missing_output = await client.post(
                                f"/api/workflows/{workflow.workflow_id}/runs",
                                json={"expected_revision": workflow.revision},
                            )
                            self.assertEqual(missing_output.status_code, 409)
                            self.assertIn("输出目录", missing_output.json()["detail"])
                            submit.assert_not_called()
                        # Semantic routing must precede bounded inspection and drafting;
                        # the unrestricted catalog loop cannot intercept processing.
                        order = []

                        async def assess(req, **kwargs):
                            order.append("route")
                            return {
                                "recommend_workflow": True,
                                "decision_source": "test",
                                "capability_ids": ["dwi.tractography"],
                            }

                        async def inspect(req, decision, registry):
                            order.append("inspect")

                        async def draft(req, decision):
                            order.append("plan")
                            return workflow, "reviewable plan"

                        with (
                            patch.object(
                                main.app.state.services.workflow_service,
                                "assess_task",
                                side_effect=assess,
                            ),
                            patch.object(
                                main.app.state.services.workspace_tools,
                                "inspect_for_plan",
                                side_effect=inspect,
                            ),
                            patch.object(
                                main.app.state.services.workflow_service,
                                "draft",
                                side_effect=draft,
                            ),
                            patch.object(
                                main.app.state.services.workspace_tools,
                                "reply",
                                side_effect=AssertionError("Wrong pre-routing loop"),
                            ),
                        ):
                            planned = await client.post(
                                "/api/chat",
                                json={
                                    "message": "重建白质通路",
                                    "interaction_mode": "task",
                                },
                            )
                            self.assertEqual(planned.status_code, 200, planned.text)
                            self.assertEqual(planned.json()["type"], "workflow")
                            self.assertEqual(order, ["route", "inspect", "plan"])
                        with (
                            patch.object(
                                main.app.state.services.workflow_service,
                                "assess_task",
                                return_value={"recommend_workflow": False},
                            ),
                            patch.object(
                                main.app.state.services.workspace_tools,
                                "reply",
                                return_value="真实目录查询结果",
                            ) as file_reply,
                            patch.object(
                                main.app.state.services.workflow_service, "draft"
                            ) as no_plan,
                        ):
                            listing = await client.post(
                                "/api/chat",
                                json={
                                    "message": "列出数据集文件",
                                    "interaction_mode": "task",
                                },
                            )
                            self.assertEqual(listing.status_code, 200)
                            self.assertEqual(
                                listing.json()["content"], "真实目录查询结果"
                            )
                            file_reply.assert_awaited_once()
                            no_plan.assert_not_called()

                        async def handoff(req):
                            req.planning_handoff = True
                            req.workspace_context += "bounded observed method evidence"
                            return None

                        decisions = [
                            {
                                "recommend_workflow": False,
                                "decision_source": "initial_chat",
                            },
                            {
                                "recommend_workflow": True,
                                "decision_source": "followup_task",
                                "capability_ids": ["dwi.tractography"],
                            },
                        ]
                        with (
                            patch.object(
                                main.app.state.services.workflow_service,
                                "assess_task",
                                side_effect=decisions,
                            ) as reassess,
                            patch.object(
                                main.app.state.services.workspace_tools,
                                "reply",
                                side_effect=handoff,
                            ) as lookup,
                            patch.object(
                                main.app.state.services.workspace_tools,
                                "inspect_for_plan",
                            ),
                            patch.object(
                                main.app.state.services.workflow_service,
                                "draft",
                                return_value=(workflow, "revised plan"),
                            ),
                        ):
                            followup = await client.post(
                                "/api/chat",
                                json={
                                    "message": "如果缺少必要输入怎么办",
                                    "interaction_mode": "task",
                                },
                            )
                            self.assertEqual(followup.status_code, 200, followup.text)
                            self.assertEqual(followup.json()["type"], "workflow")
                            self.assertEqual(reassess.await_count, 2)
                            lookup.assert_awaited_once()
                            self.assertIn(
                                "bounded observed",
                                reassess.await_args.args[0].workspace_context,
                            )
                            metadata = (
                                main.app.state.services.conversation_service.snapshot(
                                    followup.json()["conversation_id"]
                                )
                                .messages[-1]
                                .metadata
                            )
                            self.assertEqual(
                                metadata["workflow_decision_source"], "followup_task"
                            )
                        workflow = (
                            main.app.state.services.workflow_service.select_method(
                                workflow.workflow_id,
                                "dwi_tractography",
                                "mrtrix3-existing-fod",
                                workflow.revision,
                            )
                        )
                        remove_url = f"/api/workflows/{workflow.workflow_id}/steps/dwi_fod/remove"
                        conflict = await client.post(
                            remove_url,
                            json={"expected_revision": workflow.revision - 1},
                        )
                        self.assertEqual(conflict.status_code, 409)
                        self.assertEqual(
                            conflict.json()["detail"]["current"]["revision"],
                            workflow.revision,
                        )
                        removed = await client.post(
                            remove_url, json={"expected_revision": workflow.revision}
                        )
                        self.assertEqual(removed.status_code, 200)
                        edited = removed.json()
                        self.assertEqual(edited["excluded_capabilities"], ["dwi.fod"])
                        self.assertEqual(
                            edited["steps"][0]["manual_input_types"], ["fod_image"]
                        )
                        locked = await client.post(
                            f"/api/workflows/{workflow.workflow_id}/lock",
                            json={"expected_revision": edited["revision"]},
                        )
                        self.assertEqual(locked.status_code, 200)
                        (root / "existing_fod.mif").write_bytes(b"synthetic")
                        bound = await client.patch(
                            f"/api/workflows/{workflow.workflow_id}/steps/dwi_tractography/inputs/fod",
                            json={
                                "expected_revision": locked.json()["revision"],
                                "root_id": main.app.state.services.dataset_service.roots()[
                                    0
                                ].root_id,
                                "relative_path": "existing_fod.mif",
                            },
                        )
                        self.assertEqual(bound.status_code, 200)
                        self.assertEqual(
                            bound.json()["steps"][0]["input_bindings"]["fod"]["source"],
                            "existing_path",
                        )
                        frozen = await client.post(
                            remove_url,
                            json={"expected_revision": bound.json()["revision"]},
                        )
                        self.assertEqual(frozen.status_code, 409)
                        (root / "replacement").mkdir()
                        dataset = main.app.state.services.dataset_service.register(
                            main.app.state.services.dataset_service.roots()[0].root_id,
                            "replacement",
                        )
                        dataset_url = f"/api/workflows/{workflow.workflow_id}/dataset"
                        rejected = await client.patch(
                            dataset_url,
                            json={
                                "dataset_id": dataset.dataset_id,
                                "expected_revision": bound.json()["revision"],
                            },
                        )
                        self.assertEqual(rejected.status_code, 409)
                        unlocked = await client.post(
                            f"/api/workflows/{workflow.workflow_id}/unlock",
                            json={"expected_revision": bound.json()["revision"]},
                        )
                        changed = await client.patch(
                            dataset_url,
                            json={
                                "dataset_id": dataset.dataset_id,
                                "expected_revision": unlocked.json()["revision"],
                            },
                        )
                        self.assertEqual(changed.status_code, 200)
                        self.assertEqual(
                            changed.json()["dataset_id"], dataset.dataset_id
                        )
                        self.assertNotIn(
                            "fod", changed.json()["steps"][0]["input_bindings"]
                        )
                        stale = await client.patch(
                            dataset_url,
                            json={
                                "dataset_id": dataset.dataset_id,
                                "expected_revision": unlocked.json()["revision"],
                            },
                        )
                        self.assertEqual(stale.status_code, 409)
                        self.assertEqual(
                            stale.json()["detail"]["current"]["revision"],
                            changed.json()["revision"],
                        )
                        absent = await client.post(
                            "/api/workflows/missing/steps/missing/remove",
                            json={"expected_revision": 1},
                        )
                        self.assertEqual(absent.status_code, 404)

                    entered, released, timed_out = Event(), Event(), Event()

                    def slow_skill(skill, *_args):
                        if not entered.is_set():
                            entered.set()
                            if not released.wait(3):
                                timed_out.set()
                        return skill.public_dict()

                    with patch.object(
                        main.app.state.services.skill_catalog,
                        "execution_status",
                        side_effect=slow_skill,
                    ):
                        slow_request = asyncio.create_task(client.get("/api/skills"))
                        try:
                            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                            fast = await asyncio.wait_for(
                                client.get("/api/conversations"), timeout=1
                            )
                            self.assertEqual(fast.status_code, 200)
                            self.assertFalse(
                                timed_out.is_set(),
                                "Skill probes blocked the event loop",
                            )
                        finally:
                            released.set()
                            await slow_request
                    entered, released = Event(), Event()
                    from app.operation_context import checkpoint

                    def scan(_dataset_id):
                        entered.set()
                        self.assertTrue(released.wait(3))
                        checkpoint()
                        return {"done": True}

                    with patch.object(
                        main.app.state.services.dataset_service,
                        "inspect",
                        side_effect=scan,
                    ):
                        response = await asyncio.wait_for(
                            client.post("/api/datasets/synthetic/inspect-jobs"),
                            timeout=1,
                        )
                        self.assertEqual(response.status_code, 202)
                        operation_id = response.json()["operation_id"]
                        try:
                            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                            fast = await asyncio.wait_for(
                                client.get("/api/health?include_runtime=false"),
                                timeout=1,
                            )
                            self.assertEqual(fast.status_code, 200)
                            cancelled = await client.post(
                                f"/api/operations/{operation_id}/cancel"
                            )
                            self.assertEqual(
                                cancelled.json()["status"], "cancel_requested"
                            )
                        finally:
                            released.set()
                        for _ in range(100):
                            record = (
                                await client.get(f"/api/operations/{operation_id}")
                            ).json()
                            if record["status"] == "cancelled":
                                break
                            await asyncio.sleep(0.01)
                        self.assertEqual(record["status"], "cancelled")
                    marker = root / "release-maintenance.lock"
                    marker.write_text("synthetic")
                    try:
                        self.assertEqual(
                            (await client.post("/api/conversations")).status_code, 503
                        )
                        self.assertEqual(
                            (await client.get("/api/conversations")).status_code, 200
                        )
                    finally:
                        marker.unlink()
                    cors = await client.options(
                        "/api/workflows/synthetic/execution-draft",
                        headers={
                            "Origin": "http://localhost:3000",
                            "Access-Control-Request-Method": "PUT",
                        },
                    )
                    self.assertEqual(cors.status_code, 200)
                    conversation = main.app.state.services.conversation_service.create(
                        "Paged"
                    )
                    for index in range(85):
                        main.app.state.services.local_store.append_conversation_message(
                            conversation.conversation_id,
                            role="user",
                            content=str(index),
                            estimated_tokens=1,
                            metadata={},
                            created_at="2026-09-08",
                        )
                    first = (
                        await client.get(
                            f"/api/conversations/{conversation.conversation_id}"
                        )
                    ).json()
                    self.assertEqual(len(first["messages"]), 40)
                    self.assertTrue(first["has_more"])
            finally:
                main.app.state.services.operation_service.shutdown(wait=True)
                main.app.state.services.run_service.shutdown()
                # This test imports app.main with a temporary database path.
                # Do not leave that process-wide module bound to a database that
                # TemporaryDirectory removes before later tests import app.main.
                sys.modules.pop("app.main", None)
                package = sys.modules.get("app")
                if package is not None and getattr(package, "main", None) is main:
                    delattr(package, "main")
