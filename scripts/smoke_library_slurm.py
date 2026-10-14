# Test-specific environment and import root must be set before importing application code.
# ruff: noqa: E402
"""Isolated real two-step Pipeline test; writes only to a new shared test directory."""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

import nibabel as nib
import numpy as np

code = Path(sys.argv[1]).resolve(strict=True)
assert code.parent == Path(
    "/home/data/neuroweave/testing/methods-20260922"
) and code.name.startswith("code-library-")
root = code.parent / (
    "smoke-library-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
)
root.mkdir(exist_ok=False)
data = root / "inputs"
data.mkdir()
runs = root / "runs"
runs.mkdir()
os.environ.update(
    LOCALFLOW_SLURM_ENABLED="true",
    LOCALFLOW_SLURM_WORKER_ROOT=str(code),
    LOCALFLOW_SLURM_OUTPUT_ROOT=str(runs),
    LOCALFLOW_SLURM_PYTHON="/opt/neuroweave/envs/scientific-py311/bin/python",
    LOCALFLOW_RUN_INPUT_ROOTS=str(root),
    LOCALFLOW_DATA_ROOTS=str(data),
)
sys.path.insert(0, str(code / "bakend"))
from types import SimpleNamespace

from app.capability_registry import CapabilityRegistry
from app.execution.base import RunnerContext
from app.execution.recipe_compiler import RecipeCompiler
from app.execution.run_service import RunService
from app.execution.slurm_backend import SlurmBackend
from app.local_store import LocalStore
from app.pipeline_library import PipelineLibrary
from app.pipeline_models import ComposeRequest
from app.run_models import RunStep
from app.workflow_models import InputBinding, MethodOption, Workflow, WorkflowStep

registry = CapabilityRegistry(code / "skills")
library = PipelineLibrary(LocalStore(root / "test.db"), registry)
registry.library_provider = library.bindings
p = library.save(
    ComposeRequest(
        name="Synthetic two conversions",
        steps=[
            {"id": "a", "method_id": "mrtrix.command.mrconvert"},
            {
                "id": "b",
                "method_id": "mrtrix.command.mrconvert",
                "inputs": {"input": {"step_id": "a", "output_slot_id": "output"}},
            },
        ],
    )
)
definition = library.as_method(p)
option = MethodOption(
    id=definition.method_id, **definition.model_dump(exclude={"method_id"})
)
values = np.arange(64, dtype="float32").reshape(4, 4, 4)
image = data / "input.nii.gz"
nib.save(nib.Nifti1Image(values, np.eye(4)), image)
step = WorkflowStep(
    id="pipeline",
    order=1,
    title="Synthetic Pipeline",
    description="",
    capability=definition.capability,
    status="confirmed",
    method_options=[option],
    selected_method_id=option.id,
    selected_parameters={"a__nthreads": 1, "b__nthreads": 1},
    input_bindings={
        "a__input": InputBinding(
            slot_id="a__input",
            artifact_type="imaging_input",
            source="existing_path",
            absolute_path=str(image),
        )
    },
)
workflow = Workflow(
    workflow_id="synthetic-library",
    template_id="smoke",
    title="Synthetic",
    summary="",
    original_request="synthetic",
    status="confirmed",
    steps=[step],
    created_at="",
    updated_at="",
)
units = RecipeCompiler(registry).compile(workflow)
backend = SlurmBackend()
outputs = {}
records = []
completed = []
for unit in units:
    resolved = RunService._resolve_runtime_bindings(
        unit.step, SimpleNamespace(steps=completed)
    )
    directory = runs / unit.step.id
    directory.mkdir()
    method = unit.method
    context = RunnerContext(
        run_id="synthetic-library",
        workflow=workflow,
        step=resolved,
        method=method,
        output_directory=directory,
        cancel_event=Event(),
        log=lambda *args: None,
    )

    def save(state):
        (directory / "job-state.json").write_text(json.dumps(state, indent=2))

    result = backend.execute(
        context,
        {
            "backend": "slurm",
            "partition": "all",
            "node": "node01",
            "cpus": 1,
            "memory_gb": 2,
            "time_minutes": 3,
        },
        None,
        save,
    )
    RunService._annotate_output_slots(method, result.artifacts)
    completed.append(
        RunStep(
            step_id=unit.step.id,
            title=unit.step.title,
            runner_id=method.runner_id,
            method_id=method.id,
            status="succeeded",
            artifacts=result.artifacts,
        )
    )
    for artifact in result.artifacts:
        outputs[(unit.step.id, artifact.metadata["output_slot_id"])] = (
            artifact.absolute_path
        )
    state = json.loads((directory / "job-state.json").read_text())
    records.append(
        {
            "step": unit.step.id,
            "job_id": state["job_id"],
            "terminal": state.get("terminal"),
            "artifacts": len(result.artifacts),
        }
    )
    print(json.dumps(records[-1]), flush=True)
final = Path(
    next(a for (stepid, slot), a in outputs.items() if stepid == units[-1].step.id)
)
check = root / "verified.nii.gz"
subprocess.run(
    ["/opt/software/mrtrix3-master/bin/mrconvert", str(final), str(check), "-quiet"],
    check=True,
)
assert np.array_equal(nib.load(check).get_fdata(), values)
(root / "evidence.json").write_text(
    json.dumps(
        {"pipeline_id": p["id"], "jobs": records, "voxel_values_preserved": True},
        indent=2,
    )
)
print("Pipeline Slurm passed; evidence:", root, flush=True)
