# Test-specific environment and import root must be set before importing application code.
# ruff: noqa: E402
"""Isolated tiny synthetic inputs; no user dataset, deployment or scheduler mutations."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bakend"))
from app.artifact_semantics import specialize
from app.capability_registry import CapabilityRegistry
from app.execution.base import RunnerContext
from app.execution.recipe_compiler import RecipeCompiler
from app.execution.run_service import RunService
from app.execution.runner_registry import RunnerRegistry
from app.local_store import LocalStore
from app.method_validation import execution_fingerprint
from app.pipeline_library import PipelineLibrary
from app.pipeline_models import ComposeRequest
from app.run_models import RunStep, WorkflowRun
from app.workflow_models import InputBinding, Workflow, WorkflowStep


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--record-evidence", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    python = os.getenv(
        "LOCALFLOW_SKILL_PYTHON", "/opt/neuroweave/envs/scientific-py311/bin/python"
    )
    subprocess.run(
        [
            python,
            "-B",
            "-s",
            "-c",
            "import nibabel as n,numpy as a,sys;from pathlib import Path;p=Path(sys.argv[1]);[(n.save(n.Nifti1Image(a.full((6,6,6),v,dtype=a.float32),a.eye(4)),p/(str(i)+'.nii.gz'))) for i,v in enumerate([1,3,5])];a.savetxt(p/'identity.txt',a.eye(4))",
            str(output),
        ],
        check=True,
    )
    os.environ["LOCALFLOW_RUN_INPUT_ROOTS"] = str(output)
    registry = CapabilityRegistry(ROOT / "skills")
    runners = RunnerRegistry()
    reports = []
    now = datetime.now(timezone.utc).isoformat()
    workflow = Workflow(
        workflow_id="synthetic",
        template_id="synthetic",
        title="Synthetic",
        summary="",
        original_request="",
        status="confirmed",
        steps=[],
        created_at=now,
        updated_at=now,
    )

    def execute(
        method_id, bindings, parameters=None, folder=None, method_override=None
    ):
        option = method_override or RecipeCompiler._method_option(
            registry.method_for("method", method_id)
        )
        values = RecipeCompiler._default_parameters(option)
        values.update(parameters or {})
        step = WorkflowStep(
            id=folder or method_id.replace(".", "_"),
            order=1,
            title=method_id,
            description="",
            capability="method",
            status="confirmed",
            selected_method_id=option.id,
            method_options=[option],
            selected_parameters=values,
            input_bindings=bindings,
        )
        context = RunnerContext(
            run_id="synthetic",
            workflow=workflow,
            step=step,
            method=option,
            output_directory=output / step.id,
            cancel_event=Event(),
            log=lambda *_: None,
        )
        runner = runners.get(method_id)
        problems = runner.preflight(context)
        if problems:
            raise RuntimeError(method_id + ": " + str(problems))
        result = runner.run(context)
        RunService._annotate_output_slots(option, result.artifacts)
        assert result.artifacts and all(
            Path(a.absolute_path).exists() for a in result.artifacts
        )
        reports.append(
            dict(
                method_id=method_id,
                result="passed",
                artifact_types=[a.artifact_type for a in result.artifacts],
                quality_checks=context.quality_checks,
            )
        )
        return step, result

    for method_id in ["ants.command.AverageImages", "fsl.command.fslmerge"]:
        method = registry.definitions.method(method_id)
        slot = method.input_slots[0]
        binding = InputBinding(
            slot_id=slot.id,
            artifact_type=slot.type,
            source="existing_path",
            absolute_paths=[str(output / (str(i) + ".nii.gz")) for i in range(3)],
        )
        step, result = execute(method_id, {slot.id: binding})
        code = "import nibabel as n,numpy as a,sys;im=n.load(sys.argv[1]);d=im.get_fdata();assert (a.allclose(d,3) and d.shape==(6,6,6)) if sys.argv[2]=='average' else (d.shape==(6,6,6,3) and a.allclose(d[0,0,0],[1,3,5]))"
        subprocess.run(
            [
                python,
                "-B",
                "-s",
                "-c",
                code,
                result.artifacts[0].absolute_path,
                "average" if "AverageImages" in method_id else "merge",
            ],
            check=True,
        )
    # A real compiled DAG binds all three upstream file outputs to one list port.
    store = LocalStore(output / "state.db")
    library = PipelineLibrary(store, registry)
    concat = registry.definitions.method("mrtrix3.command.mrcat")
    port = concat.input_slots[0].id
    request = ComposeRequest(
        name="Synthetic list DAG",
        steps=[
            *[
                dict(id="s" + str(i), method_id="mrtrix.command.mrconvert")
                for i in range(3)
            ],
            dict(
                id="concat",
                method_id=concat.id,
                parameters={"axis": 3},
                inputs={
                    port: dict(
                        step_id="s0",
                        output_slot_id="output",
                        additional_sources=[
                            dict(step_id="s1", output_slot_id="output"),
                            dict(step_id="s2", output_slot_id="output"),
                        ],
                    )
                },
            ),
        ],
    )
    option = RecipeCompiler._method_option(library.as_method(library.compile(request)))
    bindings = {
        f"s{i}__input": InputBinding(
            slot_id=f"s{i}__input",
            artifact_type="imaging_input",
            source="existing_path",
            absolute_path=str(output / (str(i) + ".nii.gz")),
        )
        for i in range(3)
    }
    workflow.steps = [
        WorkflowStep(
            id="p",
            order=1,
            title="DAG",
            description="",
            capability="method",
            status="confirmed",
            selected_method_id=option.id,
            method_options=[option],
            input_bindings=bindings,
            selected_parameters=RecipeCompiler._default_parameters(option),
        )
    ]
    run = WorkflowRun(
        run_id="synthetic",
        workflow_id="synthetic",
        workflow_revision=1,
        output_directory=str(output),
        created_at=now,
        updated_at=now,
    )
    for unit in RecipeCompiler(registry).compile(workflow):
        resolved = RunService._resolve_runtime_bindings(unit.step, run)
        step, result = execute(
            unit.method.id,
            resolved.input_bindings,
            unit.step.selected_parameters,
            unit.step.id,
            unit.method,
        )
        run.steps.append(
            RunStep(
                step_id=unit.step.id,
                title=unit.step.title,
                runner_id=unit.method.runner_id,
                method_id=unit.method.id,
                status="succeeded",
                artifacts=result.artifacts,
            )
        )
    mrinfo = str(runners.get("mrtrix.command.mrconvert").executable.with_name("mrinfo"))
    shape = subprocess.run(
        [mrinfo, run.steps[-1].artifacts[0].absolute_path, "-size"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    assert list(map(int, shape)) == [6, 6, 6, 3]
    transform = registry.definitions.method("mrtrix.command.mrtransform")
    for modulation in [None, "jac"]:
        option = RecipeCompiler._method_option(
            registry.method_for("method", transform.id)
        )
        values = {"interp": "nearest", "modulate": modulation}
        option = specialize(option, {"input": "parcellation_image"}, values)
        b = {
            "input": InputBinding(
                slot_id="input",
                artifact_type="parcellation_image",
                source="existing_path",
                absolute_path=str(output / "0.nii.gz"),
            ),
            "linear": InputBinding(
                slot_id="linear",
                artifact_type="generated_file",
                source="existing_path",
                absolute_path=str(output / "identity.txt"),
            ),
        }
        step, result = execute(
            transform.id, b, values, "transform_" + str(modulation), option
        )
        assert result.artifacts[0].artifact_type == (
            "imaging_input" if modulation else "parcellation_image"
        )
    report = dict(
        test_type="synthetic",
        tested_at=now,
        environment="mgr installed MRtrix3/FSL/ANTs; six-voxel axes",
        checks=reports,
        submitted_jobs=False,
        user_datasets_read=False,
    )
    report_path = output / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if args.record_evidence:
        dest = ROOT / "deploy/method-verification/reports/reliability-synthetic.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(report_path.read_bytes())
        for mid in {r["method_id"] for r in reports}:
            method = registry.definitions.method(mid)
            evidence = dict(
                method_id=mid,
                method_version=method.version,
                execution_fingerprint=execution_fingerprint(method, ROOT),
                test_type="synthetic",
                tested_at=now,
                environment=report["environment"],
                result="passed",
                report_path=dest.relative_to(ROOT).as_posix(),
                report_sha256=hashlib.sha256(dest.read_bytes()).hexdigest(),
            )
            (dest.parent.parent / (mid + ".json")).write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    print(
        json.dumps(
            dict(
                passed=len(reports),
                methods=len({r["method_id"] for r in reports}),
                report=str(report_path),
            )
        )
    )


if __name__ == "__main__":
    main()
