from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

from app.execution.base import ExecutionCancelled, ExecutionDetached, RunnerContext
from app.execution.slurm_backend import SlurmBackend
from app.execution.slurm_worker import atomic_json
from app.run_models import RunArtifact
from app.workflow_models import MethodOption, Workflow, WorkflowStep


ROOT = Path(__file__).resolve().parents[2]


class SlurmBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.patch_env = patch.dict(os.environ, {
            "LOCALFLOW_SLURM_ENABLED": "true", "LOCALFLOW_SLURM_OUTPUT_ROOT": str(self.root),
            "LOCALFLOW_SLURM_WORKER_ROOT": str(ROOT), "LOCALFLOW_SLURM_PYTHON": sys.executable,
            "LOCALFLOW_RUN_INPUT_ROOTS": str(self.root), "LOCALFLOW_API_KEY": "must-never-be-exported",
            "OPENAI_API_KEY": "another-sensitive-value",
        })
        self.patch_env.start()
        self.addCleanup(self.patch_env.stop)
        self.addCleanup(self.temporary.cleanup)
        self.backend = SlurmBackend()
        self.backend.poll_seconds = 0
        self.method = MethodOption(id="report", skill_id="report", label="Report", description="test",
                                   runner_id="report.build")
        self.step = WorkflowStep(id="step_report", order=1, title="Report", description="test", capability="report",
                                 status="confirmed", selected_method_id="report", method_options=[self.method],
                                 selected_parameters={"output_format": "json"})
        self.workflow = Workflow(workflow_id="wf_test", template_id="test", title="test", summary="test",
                                 original_request="report", status="confirmed", steps=[self.step],
                                 created_at="2026-09-07", updated_at="2026-09-07")
        self.context = RunnerContext(run_id="run_test", workflow=self.workflow, step=self.step,
                                     method=self.method, output_directory=self.root / "run" / "report",
                                     cancel_event=Event(), log=lambda *args: None, detach_event=Event())
        self.saved: list[dict] = []
        self.options = {"backend": "slurm", "partition": "all", "cpus": 2, "memory_gb": 2,
                        "gpus": 0, "time_minutes": 5}

    def persist(self, state: dict) -> None:
        self.saved.append(json.loads(json.dumps(state)))

    def old_state(self) -> dict:
        self.context.output_directory.mkdir(parents=True, exist_ok=True)
        directory = self.context.output_directory / ".slurm" / ("a" * 32)
        directory.mkdir(parents=True, exist_ok=True)
        return {"submission_token": "a" * 32, "job_directory": str(directory), "job_id": "123",
                "state": "RUNNING", "options": self.options}

    def worker_result(self, state: dict, *, artifact: bool = True) -> None:
        path = self.context.output_directory / "report.json"
        path.write_text('{"ok":true}', encoding="utf-8")
        artifacts = [RunArtifact(artifact_id="artifact_test", step_id=self.step.id, artifact_type="processing_report",
                                 label="report", absolute_path=str(path), relative_path=path.name,
                                 size_bytes=path.stat().st_size).model_dump()] if artifact else []
        atomic_json(Path(state["job_directory"]) / "result.json", {
            "submission_token": state["submission_token"], "job_id": state["job_id"],
            "status": "succeeded", "exit_code": 0, "message": "ok", "artifacts": artifacts})

    def test_submission_intent_precedes_sbatch_and_excludes_secrets(self) -> None:
        calls = []

        def command(argv):
            calls.append(argv)
            self.assertTrue(self.saved)
            self.assertNotIn("job_id", self.saved[0])
            directory = Path(self.saved[0]["job_directory"])
            payload = json.loads((directory / "payload.json").read_text(encoding="utf-8"))
            self.assertNotIn("LOCALFLOW_API_KEY", payload["runtime_environment"])
            self.assertNotIn("OPENAI_API_KEY", payload["runtime_environment"])
            self.assertNotIn("must-never-be-exported", (directory / "job.sh").read_text())
            return subprocess.CompletedProcess(argv, 0, "123;cluster\n", "")

        with patch.object(self.backend, "_command", side_effect=command), patch.object(self.backend, "_monitor", return_value="ok"):
            self.assertEqual(self.backend.execute(self.context, self.options, None, self.persist), "ok")
        self.assertEqual(self.saved[-1]["job_id"], "123")
        self.assertIn("--export=NONE", calls[0])
        self.assertIn("--no-requeue", calls[0])
        self.assertNotIn("--gres=gpu:1", calls[0])

    def test_fixed_node_submission_uses_reviewed_nodelist_argument(self) -> None:
        self.options.update(node="node02", cpus=32)
        with patch.object(self.backend, "_command", return_value=subprocess.CompletedProcess([], 0, "123", "")) as command, \
                patch.object(self.backend, "_monitor", return_value="ok"):
            self.backend.execute(self.context, self.options, None, self.persist)
        self.assertIn("--nodelist=node02", command.call_args.args[0])
        self.assertIn("--cpus-per-task=32", command.call_args.args[0])
        self.assertIn("--nodes=1", command.call_args.args[0])

    def test_submission_timeout_keeps_durable_ambiguous_state(self) -> None:
        with patch.object(self.backend, "_command", side_effect=subprocess.TimeoutExpired("sbatch", 20)):
            with self.assertRaises(ExecutionDetached):
                self.backend.execute(self.context, self.options, None, self.persist)
        self.assertEqual(self.saved[-1]["reason"], "submission_uncertain")
        with patch.object(self.backend, "_command") as command:
            with self.assertRaises(ExecutionDetached):
                self.backend.execute(self.context, self.options, self.saved[-1], self.persist)
            command.assert_not_called()

    def test_existing_job_is_monitored_without_resubmitting(self) -> None:
        state = self.old_state()
        self.worker_result(state)
        with patch.object(self.backend, "_query", return_value={"state": "COMPLETED", "exit_code": "0:0", "source": "scontrol"}), \
                patch.object(self.backend, "_command") as command:
            result = self.backend.execute(self.context, self.options, state, self.persist)
        command.assert_not_called()
        self.assertEqual(result.message, "ok")
        self.assertTrue((Path(state["job_directory"]) / "terminal.json").is_file())
        self.assertEqual(self.saved[-1]["terminal"]["state"], "COMPLETED")

    def test_persisted_terminal_survives_slurm_record_purge(self) -> None:
        state = self.old_state()
        self.worker_result(state)
        state["terminal"] = {"job_id": "123", "submission_token": state["submission_token"],
                             "state": "COMPLETED", "exit_code": "0:0"}
        with patch.object(self.backend, "_query") as query:
            result = self.backend.execute(self.context, self.options, state, self.persist)
        query.assert_not_called()
        self.assertEqual(len(result.artifacts), 1)

    def test_worker_success_without_scheduler_terminal_is_not_success(self) -> None:
        state = self.old_state()
        self.worker_result(state)
        self.backend.missing_timeout = -1
        with patch.object(self.backend, "_query", return_value=None):
            with self.assertRaises(ExecutionDetached):
                self.backend.execute(self.context, self.options, state, self.persist)
        self.assertEqual(self.saved[-1]["reason"], "monitoring_unavailable")

    def test_nonzero_exit_and_empty_artifacts_fail(self) -> None:
        for terminal, code, artifact in [("COMPLETED", "1:0", True), ("FAILED", "0:0", True), ("COMPLETED", "0:0", False)]:
            with self.subTest(terminal=terminal, code=code, artifact=artifact):
                state = self.old_state()
                terminal_path = Path(state["job_directory"]) / "terminal.json"
                terminal_path.unlink(missing_ok=True)
                self.worker_result(state, artifact=artifact)
                with patch.object(self.backend, "_query", return_value={"state": terminal, "exit_code": code}):
                    with self.assertRaises(RuntimeError):
                        self.backend.execute(self.context, self.options, state, self.persist)

    def test_cancel_calls_scancel_then_confirms_terminal(self) -> None:
        state = self.old_state()
        self.context.cancel_event.set()
        with patch.object(self.backend, "_query", side_effect=[{"state": "RUNNING", "exit_code": "0:0"},
                                                               {"state": "CANCELLED", "exit_code": "0:15"}]), \
                patch.object(self.backend, "_command", return_value=subprocess.CompletedProcess([], 0, "", "")) as command:
            with self.assertRaises(ExecutionCancelled):
                self.backend.execute(self.context, self.options, state, self.persist)
        command.assert_called_once_with(["scancel", "123"])
        self.assertEqual(self.saved[-1]["terminal"]["state"], "CANCELLED")

    def test_failed_cancellation_keeps_job_active(self) -> None:
        state = self.old_state()
        self.context.cancel_event.set()
        with patch.object(self.backend, "_query", return_value={"state": "RUNNING", "exit_code": "0:0"}), \
                patch.object(self.backend, "_command", return_value=subprocess.CompletedProcess([], 1, "", "denied")):
            with self.assertRaises(ExecutionDetached):
                self.backend.execute(self.context, self.options, state, self.persist)
        self.assertEqual(self.saved[-1]["reason"], "cancellation_unconfirmed")

    def test_detach_does_not_cancel_or_submit(self) -> None:
        state = self.old_state()
        self.context.detach_event.set()
        with patch.object(self.backend, "_command") as command:
            with self.assertRaises(ExecutionDetached):
                self.backend.execute(self.context, self.options, state, self.persist)
        command.assert_not_called()

    def test_malformed_job_id_rejected_before_command(self) -> None:
        state = self.old_state()
        state["job_id"] = "123; touch /tmp/not-allowed"
        with patch.object(self.backend, "_command") as command:
            with self.assertRaises(RuntimeError):
                self.backend.execute(self.context, self.options, state, self.persist)
        command.assert_not_called()

    def test_scontrol_identity_and_terminal_parsing(self) -> None:
        token = "a" * 32
        output = f"JobId=123 JobName=nw-{token} JobState=COMPLETED Reason=None ExitCode=0:0 NodeList=node01"
        with patch.object(self.backend, "_command", return_value=subprocess.CompletedProcess([], 0, output, "")):
            observed = self.backend._query("123", token)
        self.assertEqual(observed["state"], "COMPLETED")
        self.assertEqual(observed["exit_code"], "0:0")
        with patch.object(self.backend, "_command", return_value=subprocess.CompletedProcess([], 0, output.replace(token, "b" * 32), "")):
            with self.assertRaises(RuntimeError):
                self.backend._query("123", token)

    def test_status_ignores_down_gpus(self) -> None:
        output = "all*|up|idle|(null)|mgr\nall*|up|down|gpu:rtx5060:1|node01\nall*|up|idle*|gpu:1|node02\n"
        with patch("app.execution.slurm_backend.shutil.which", return_value="/usr/bin/slurm"), \
                patch.object(self.backend, "_command", return_value=subprocess.CompletedProcess([], 0, output, "")):
            status = self.backend.status()
        self.assertTrue(status["available"])
        self.assertEqual(status["partitions"], ["all"])
        self.assertFalse(status["gpu_available"])

    def test_status_reports_node_capacity_without_expanding_host_expressions(self) -> None:
        output = ("all*|up|mix|gpu:1|node01|128|245000|100000|16/112/0/128\n"
                  "all*|up|idle|gpu:1|node[02-03]|128|245000|100000|0/128/0/128\n")
        with patch("app.execution.slurm_backend.shutil.which", return_value="/usr/bin/slurm"), \
                patch.object(self.backend, "_command", return_value=subprocess.CompletedProcess([], 0, output, "")):
            status = self.backend.status()
        self.assertEqual(len(status["nodes"]), 1)
        self.assertEqual(status["nodes"][0]["cpus"], 128)
        self.assertEqual(status["nodes"][0]["memory_mb"], 245000)
        self.assertEqual(status["nodes"][0]["allocated_cpus"], 16)
        self.assertEqual(status["nodes"][0]["gpus"], 1)

    def test_script_quotes_paths_and_resets_inherited_environment(self) -> None:
        payload = self.root / "space ' $` injection" / "payload.json"
        script = self.backend._script(payload)
        import shlex
        self.assertIn(shlex.quote(str(payload)), script)
        self.assertIn("exec /usr/bin/env -i", script)
        self.assertIn("CUDA_VISIBLE_DEVICES", script)
        self.assertNotIn("OPENAI_API_KEY", script)

    def test_real_worker_executes_registered_report_and_writes_atomic_result(self) -> None:
        with patch.object(self.backend, "_command", return_value=subprocess.CompletedProcess([], 0, "123\n", "")), \
                patch.object(self.backend, "_monitor", return_value=None):
            self.backend.execute(self.context, self.options, None, self.persist)
        state = self.saved[-1]
        payload = Path(state["job_directory"]) / "payload.json"
        completed = subprocess.run([sys.executable, str(self.backend.worker), "--payload", str(payload)],
                                   env={**os.environ, "SLURM_JOB_ID": "123"}, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        result = json.loads((payload.parent / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["job_id"], "123")
        self.assertEqual(len(result["artifacts"]), 1)
        self.assertTrue((payload.parent / "events.jsonl").is_file())

    def test_modified_payload_does_not_resume_or_resubmit(self) -> None:
        with patch.object(self.backend, "_command", return_value=subprocess.CompletedProcess([], 0, "123\n", "")), \
                patch.object(self.backend, "_monitor", return_value=None):
            self.backend.execute(self.context, self.options, None, self.persist)
        state = self.saved[-1]
        payload = Path(state["job_directory"]) / "payload.json"
        payload.write_text("{}", encoding="utf-8")
        with patch.object(self.backend, "_command") as command:
            with self.assertRaises(ExecutionDetached):
                self.backend.execute(self.context, self.options, state, self.persist)
        command.assert_not_called()

    def test_worker_rejects_unregistered_runner(self) -> None:
        with patch.object(self.backend, "_command", return_value=subprocess.CompletedProcess([], 0, "123\n", "")), \
                patch.object(self.backend, "_monitor", return_value=None):
            self.backend.execute(self.context, self.options, None, self.persist)
        payload = Path(self.saved[-1]["job_directory"]) / "payload.json"
        value = json.loads(payload.read_text(encoding="utf-8"))
        value["method"]["runner_id"] = "arbitrary.shell"
        atomic_json(payload, value)
        completed = subprocess.run([sys.executable, str(self.backend.worker), "--payload", str(payload)],
                                   env={**os.environ, "SLURM_JOB_ID": "123"}, capture_output=True,
                                   timeout=30)
        self.assertEqual(completed.returncode, 1)
        result = json.loads((payload.parent / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "failed")
        self.assertIn("arbitrary.shell", result["message"])

    def test_sacct_fallback_uses_only_parent_job(self) -> None:
        token = "a" * 32
        with patch("app.execution.slurm_backend.shutil.which", return_value="/usr/bin/sacct"), \
                patch.object(self.backend, "_command", side_effect=[
                    subprocess.CompletedProcess([], 1, "", "Invalid job id specified"),
                    subprocess.CompletedProcess([], 0, f"123.batch|batch|COMPLETED|0:0\n123|nw-{token}|FAILED|1:0\n", "")]):
            result = self.backend._query("123", token)
        self.assertEqual(result["state"], "FAILED")
        self.assertEqual(result["exit_code"], "1:0")

    def test_nfs_event_read_failure_does_not_fail_live_job(self) -> None:
        state = self.old_state()
        with patch.object(self.backend, "_read_events", side_effect=OSError("NFS unavailable")):
            with self.assertRaises(ExecutionDetached):
                self.backend.execute(self.context, self.options, state, self.persist)
        self.assertEqual(self.saved[-1]["job_id"], "123")
        self.assertEqual(self.saved[-1]["reason"], "monitoring_unavailable")

    def test_persistence_failure_after_submission_keeps_run_active(self) -> None:
        def persist(state):
            if state.get("job_id"):
                raise OSError("database unavailable")
            self.persist(state)

        with patch.object(self.backend, "_command", return_value=subprocess.CompletedProcess([], 0, "123\n", "")):
            with self.assertRaises(ExecutionDetached):
                self.backend.execute(self.context, self.options, None, persist)
        self.assertTrue(self.saved[0]["submission_token"])

    def test_missing_payload_while_live_is_recoverable(self) -> None:
        state = self.old_state()
        state["payload_sha256"] = "a" * 64
        with self.assertRaises(ExecutionDetached):
            self.backend.execute(self.context, self.options, state, self.persist)
        self.assertEqual(self.saved[-1]["job_id"], "123")


if __name__ == "__main__":
    unittest.main()
