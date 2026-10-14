from __future__ import annotations

import os
import queue
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from ..run_models import RunArtifact
from ..workflow_models import InputBinding
from .base import ExecutionCancelled, RunnerContext


class ExternalCommandRunner:
    """Shared safety boundary for reviewed argv-only command runners.

    Subclasses build argument arrays from manifest-validated values. No shell is
    started and no free-form command fragment is accepted from either the model
    or the browser.
    """

    runner_id = "external.unconfigured"
    label = "外部命令"
    executable: Path

    def runtime_status(self) -> dict[str, Any]:
        executable = self.executable
        available = executable.is_file() and os.access(executable, os.X_OK)
        return {
            "available": available,
            "reason": "可执行文件可用" if available else f"未找到可执行文件：{executable}",
            "executable": str(executable),
        }

    def common_preflight(self, context: RunnerContext) -> list[str]:
        problems: list[str] = []
        status = self.runtime_status()
        if not status.get("available"):
            problems.append(str(status.get("reason") or "运行环境不可用"))
        for slot_id, binding in context.step.input_bindings.items():
            from ..parameter_rules import effective_parameters
            values=effective_parameters(context.method,context.step.selected_parameters)
            if values.get('gradient_source') in {'embedded','fsl','mrtrix'}:
                from .gradient_policy import gradient_slots
                if slot_id in gradient_slots(context,except_source=values['gradient_source']):
                    continue
            if binding.source == "upstream_output":
                if not context.input_is_deferred(slot_id):
                    problems.append(f"输入 {slot_id} 的上游输出尚未解析为实际文件")
                continue
            from ..historical_inputs import historical_binding_problems
            history_problems = historical_binding_problems(binding)
            problems.extend(f"输入 {slot_id}：{problem}" for problem in history_problems)
            for path in self.binding_paths(binding):
                from ..artifact_semantics import accepts_format
                definition = next((s for s in context.method.input_slots if s.id == slot_id), None)
                if definition and definition.binding != 'folder' and not accepts_format(path, definition.formats):
                    problems.append(f'输入 {slot_id} 格式不兼容；需要 {definition.formats}')
                if not path.exists():
                    problems.append(f"输入 {slot_id} 不存在：{path}")
                elif not (binding.history and not history_problems) and not self._allowed_input(path, context.output_directory.parent):
                    problems.append(f"输入 {slot_id} 超出允许运行的数据根：{path}")
        try:
            context.output_directory.mkdir(parents=True, exist_ok=True)
            probe = context.output_directory / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            problems.append(f"输出目录不可写：{exc}")
        if not problems:
            from .scientific_checks import scientific_checks
            from .check_cache import content_cache
            with content_cache(context):
                checks=scientific_checks(context)
            context.quality_checks.extend(checks)
            problems.extend('Scientific preflight: '+row['code']+' ('+row['slot_id']+')' for row in checks if row['status']=='error')
        return problems

    @staticmethod
    def binding_paths(binding: InputBinding) -> list[Path]:
        if binding.binding_mode == "per_sample":
            return [
                Path(match.absolute_path)
                for match in binding.sample_matches
                if match.status == "matched" and match.absolute_path
            ]
        return [Path(p) for p in binding.absolute_paths] if binding.absolute_paths else [Path(binding.absolute_path)] if binding.absolute_path else []

    @classmethod
    def required_binding(cls, context: RunnerContext, slot_id: str) -> Path:
        binding = context.step.input_bindings.get(slot_id)
        paths = cls.binding_paths(binding) if binding is not None else []
        if len(paths) != 1:
            raise RuntimeError(f"输入 {slot_id} 当前需要绑定且只能绑定一个文件")
        return paths[0].resolve(strict=True)

    @classmethod
    def optional_binding(cls, context: RunnerContext, slot_id: str) -> Path | None:
        binding = context.step.input_bindings.get(slot_id)
        if binding is None:
            return None
        paths = cls.binding_paths(binding)
        if len(paths) != 1:
            raise RuntimeError(f"输入 {slot_id} 当前只能绑定一个文件")
        return paths[0].resolve(strict=True)

    @staticmethod
    def parameter(context: RunnerContext, name: str, default: Any = None) -> Any:
        return context.step.selected_parameters.get(name, default)

    def execute(
        self,
        context: RunnerContext,
        argv: list[str],
        *,
        environment: dict[str, str] | None = None,
        cwd: Path | None = None,
        log_path: Path | None = None,
        progress_start: float = 5.0,
        progress_end: float = 90.0,
    ) -> None:
        if not argv or Path(argv[0]) not in self.allowed_executables():
            raise RuntimeError("Runner 拒绝了未经登记的可执行文件")
        context.raise_if_cancelled()
        rendered = subprocess.list2cmdline(argv)
        context.log(f"启动受信任命令：{rendered}", progress_start, "info")
        process_env = os.environ.copy()
        if environment:
            process_env.update(environment)
        log_handle = None
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_handle = log_path.open("w", encoding="utf-8")
            log_handle.write(f"Command: {rendered}\n")
            log_handle.flush()
        try:
            process = subprocess.Popen(  # noqa: S603 - argv comes from reviewed runner code
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                bufsize=1,
                shell=False,
                env=process_env,
                cwd=str(cwd) if cwd is not None else None,
                start_new_session=os.name != "nt",
            )
        except Exception:
            if log_handle is not None:
                log_handle.close()
            raise
        lines: queue.Queue[str | None] = queue.Queue()

        def read_output() -> None:
            assert process.stdout is not None
            try:
                for line in process.stdout:
                    lines.put(line.rstrip())
            except (OSError, ValueError):
                # Cancellation can close the pipe while the reader is waiting.
                if not context.cancel_event.is_set():
                    raise
            finally:
                lines.put(None)

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        started = time.monotonic()
        last_emit = 0.0
        last_line = ""
        stream_closed = False
        try:
            while process.poll() is None or not stream_closed:
                if context.cancel_event.is_set():
                    self._terminate(process)
                    raise ExecutionCancelled("外部命令已按用户请求终止")
                try:
                    line = lines.get(timeout=0.2)
                except queue.Empty:
                    line = ""
                if line is None:
                    stream_closed = True
                elif line:
                    last_line = line[:2_000]
                    if log_handle is not None:
                        log_handle.write(line + "\n")
                        log_handle.flush()
                    now = time.monotonic()
                    if now - last_emit >= 0.5:
                        elapsed = now - started
                        progress = min(progress_end, progress_start + elapsed / 30.0)
                        context.log(last_line, progress, "info")
                        last_emit = now
            return_code = process.wait()
        finally:
            if process.poll() is None:
                self._terminate(process)
            reader.join(timeout=2)
            if process.stdout is not None:
                process.stdout.close()
            if log_handle is not None:
                log_handle.close()
        if last_line and time.monotonic() - last_emit >= 0.1:
            context.log(last_line, progress_end, "info")
        if return_code != 0:
            raise RuntimeError(
                f"{self.label} 退出码为 {return_code}"
                + (f"；最后输出：{last_line}" if last_line else "")
            )

    def allowed_executables(self) -> set[Path]:
        return {self.executable}

    @staticmethod
    def _terminate(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            process.wait(timeout=8)
        except (OSError, subprocess.TimeoutExpired):
            try:
                if os.name != "nt":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except OSError:
                pass

    @staticmethod
    def artifact(
        context: RunnerContext,
        path: Path,
        artifact_type: str,
        label: str,
        media_type: str = "application/octet-stream",
        metadata: dict[str, Any] | None = None,
    ) -> RunArtifact:
        path = path.resolve(strict=True)
        path.relative_to(context.output_directory.resolve())
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"输出文件不存在或为空：{path}")
        return RunArtifact(
            artifact_id=f"artifact_{uuid4().hex[:12]}",
            step_id=context.step.id,
            artifact_type=artifact_type,
            label=label,
            absolute_path=str(path),
            relative_path=path.relative_to(context.output_directory).as_posix(),
            media_type=media_type,
            size_bytes=path.stat().st_size,
            sha256=context.file_sha256(path),
            metadata={**(metadata or {}),'quality':{'status':'not_reviewed','scope':'execution artifact; scientific validity requires review'}},
        )

    @staticmethod
    def directory_artifact(
        context: RunnerContext,
        path: Path,
        artifact_type: str,
        label: str,
        metadata: dict[str, Any] | None = None,
    ) -> RunArtifact:
        path = path.resolve(strict=True)
        path.relative_to(context.output_directory.resolve())
        if not path.is_dir():
            raise RuntimeError(f"输出目录不存在：{path}")
        return RunArtifact(
            artifact_id=f"artifact_{uuid4().hex[:12]}",
            step_id=context.step.id,
            artifact_type=artifact_type,
            label=label,
            absolute_path=str(path),
            relative_path=path.relative_to(context.output_directory).as_posix(),
            media_type="inode/directory",
            size_bytes=sum(item.stat().st_size for item in path.rglob("*") if item.is_file()),
            metadata={**(metadata or {}),'quality':{'status':'not_reviewed','scope':'execution artifact; scientific validity requires review'}},
        )

    @staticmethod
    def _allowed_input(path: Path, run_root: Path | None = None) -> bool:
        configured = os.getenv("LOCALFLOW_RUN_INPUT_ROOTS", "/server")
        roots: Iterable[str] = (item.strip() for item in configured.split(";") if item.strip())
        resolved = path.resolve(strict=False)
        allowed_roots = [Path(item) for item in roots]
        if run_root is not None:
            # A reviewed runner may consume artifacts produced earlier in the
            # same immutable run, even when the run output root is not a data root.
            allowed_roots.append(run_root)
        for root in allowed_roots:
            try:
                resolved.relative_to(root.resolve(strict=False))
                return True
            except ValueError:
                continue
        return False

    @classmethod
    def _allowed_binding_input(cls, binding: InputBinding | None, path: Path, run_root: Path | None = None) -> bool:
        if binding is not None and binding.history is not None:
            from ..historical_inputs import historical_binding_problems
            return not historical_binding_problems(binding) and any(
                Path(reference.absolute_path) == path.resolve(strict=False)
                for reference in binding.history.artifacts)
        return cls._allowed_input(path, run_root)
