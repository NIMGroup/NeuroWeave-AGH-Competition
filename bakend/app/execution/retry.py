"""Conservative reuse of verified file outputs; no implicit algorithm or input changes."""
from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path


def implementation_fingerprint(runner, method) -> str:
    paths = {Path(inspect.getfile(type(runner))), Path(__file__), Path(__file__).with_name("base.py"),
             Path(__file__).with_name("external_command.py")}
    spec = getattr(runner, "spec", {})
    root = Path(__file__).resolve().parents[3]
    paths.update(root / item["path"] for item in spec.get("implementation_files", []))
    fingerprint = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(paths) if path.is_file()}
    executable = getattr(runner, "executable", None)
    if executable and Path(executable).is_file():
        stat = Path(executable).stat()
        fingerprint["executable"] = [str(executable),stat.st_size,stat.st_mtime_ns]
    fingerprint["method"] = method.model_dump(mode="json")
    return hashlib.sha256(json.dumps(fingerprint,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def input_fingerprint(context) -> dict:
    from .external_command import ExternalCommandRunner
    paths = [path for binding in context.step.input_bindings.values() for path in ExternalCommandRunner.binding_paths(binding)]
    # Folder inputs and legacy records cannot prove unchanged content cheaply;
    # rerun their upstream steps instead of claiming their outputs are reusable.
    if not paths or any(not path.is_file() for path in paths):
        return {}
    return {str(path.resolve()): context.file_sha256(path) for path in paths}


def reusable_step(step, context, runner, original_output_root: Path) -> bool:
    if step.status != "succeeded" or not step.input_fingerprint or not step.implementation_fingerprint:
        return False
    if step.implementation_fingerprint != implementation_fingerprint(runner, context.method):
        return False
    if step.input_fingerprint != input_fingerprint(context):
        return False
    if not step.artifacts:
        return False
    for artifact in step.artifacts:
        path = Path(artifact.absolute_path).resolve()
        if not path.is_relative_to(original_output_root) or not path.is_file() or not artifact.sha256:
            return False
        if path.stat().st_size != artifact.size_bytes or context.file_sha256(path) != artifact.sha256:
            return False
    return True
