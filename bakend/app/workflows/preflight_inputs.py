"""Resolve planned input rules once, during the explicitly requested dry-run."""

from pathlib import Path
import fnmatch
import stat

from ..filesystem_scan import ScopedScan
from ..operation_context import checkpoint
from ..workspace_paths import WorkspacePaths
from .assistant_bindings import AssistantInput, bind_assistant_inputs
from .contracts import selected_method


def resolve_planned_inputs(workflow, datasets):
    if workflow.input_selection == 'user':
        from .user_inputs import resolve_user_inputs
        reports = resolve_user_inputs(workflow, datasets)
        workflow.planning_diagnostics['input_bindings'] = reports
        return reports
    pending = [
        (s, b)
        for s in workflow.steps
        for b in s.input_bindings.values()
        if b.deferred_resolution
    ]
    if not workflow.dataset:
        return []
    dataset = datasets.get(workflow.dataset_id)
    root = Path(dataset.absolute_path)
    policy = WorkspacePaths(root, allowed_roots=(root,))
    paths = None
    reports = []
    for step, binding in pending:
        checkpoint("预检：解析输入规则")
        if workflow.input_selection == 'user' and not binding.confirmed_by_user:
            raise ValueError('请在任务卡中选择或采用输入规则，模型建议不能自动绑定')
        method = selected_method(step)
        slot = next(s for s in method.input_slots if s.id == binding.slot_id)
        relative = binding.path_template
        if not relative and slot.binding == "folder" and slot.type == "dataset_folder":
            relative = "."
        if not relative:
            if workflow.input_selection == 'user':
                raise ValueError(f'{step.title}/{slot.label}：请在任务卡选择输入路径，不会自动猜测文件')
            if paths is None:
                scan = ScopedScan(root, policy, seconds=300, strict=False)
                paths = [
                    logical.relative_to(root).as_posix()
                    for logical, _, info, _ in scan.walk(root)
                    if stat.S_ISREG(info.st_mode)
                ]
                if not scan.report()["complete_scan"]:
                    raise ValueError("预检输入定位尚未扫描完成；不能据部分结果判定缺失")
            patterns = slot.discovery_patterns or [
                "**/*" + suffix for suffix in slot.formats
            ]
            candidates = [
                p
                for p in paths
                if any(
                    fnmatch.fnmatchcase(p, pattern)
                    or fnmatch.fnmatchcase(Path(p).name, pattern)
                    for pattern in patterns
                )
            ]
            if binding.per_sample_requested:
                # Pick a representative only after proving it is unique inside
                # that sample. Full sample expansion retains missing/ambiguous.
                chosen = []
                for sample in dataset.inspection.samples:
                    own = [
                        p
                        for p in candidates
                        if p.startswith(sample.relative_root.rstrip("/") + "/")
                    ]
                    if len(own) == 1:
                        chosen.append(own[0])
                relative = chosen[0] if chosen else ""
            elif len(candidates) == 1:
                relative = candidates[0]
            if not relative:
                raise ValueError(
                    f"{step.title}/{slot.label}：预检无法唯一定位声明的输入，请指定路径；不会重新调查其是否存在"
                )
        spec = AssistantInput(
            method_id=method.id,
            step_id=step.id,
            slot_id=slot.id,
            path=relative,
            per_sample=binding.per_sample_requested and slot.type != "dataset_folder",
        )
        reports.extend(
            bind_assistant_inputs(workflow, None, datasets, [spec], preflight=True)
        )
    # Auto external gradients are companions of the selected image, never an
    # arbitrary bval/bvec elsewhere in the sample. No contents are read in planning.
    from ..execution.scientific_checks import image_header

    for step in workflow.steps:
        if workflow.input_selection == 'user':
            continue  # External gradient files, like other inputs, require selection.
        method = selected_method(step)
        if not any(p.id == "gradient_source" for p in method.parameters):
            continue
        if step.selected_parameters.get("gradient_source", "auto") != "auto":
            continue
        image_id = next(
            (s.id for s in method.input_slots if s.id in {"dwi", "input"}), None
        )
        image = step.input_bindings.get(image_id)
        if not image or image.source == "upstream_output":
            continue
        representatives = (
            [m.absolute_path for m in image.sample_matches if m.status == "matched"]
            if image.binding_mode == "per_sample"
            else [image.absolute_path]
        )
        if not representatives or not representatives[0]:
            continue
        image_path = Path(representatives[0])
        if image_header(image_path).get("embedded_gradients"):
            continue
        stem = (
            image_path.name[:-7]
            if image_path.name.endswith(".nii.gz")
            else image_path.stem
        )
        for suffix, slots in (
            ("bvec", ("bvecs", "fslgrad_bvecs")),
            ("bval", ("bvals", "fslgrad_bvals")),
        ):
            slot_id = next(
                (s for s in slots if any(v.id == s for v in method.input_slots)), None
            )
            if not slot_id or slot_id in step.input_bindings:
                continue
            companion = image_path.with_name(stem + "." + suffix)
            if companion.is_file():
                spec = AssistantInput(
                    method_id=method.id,
                    step_id=step.id,
                    slot_id=slot_id,
                    path=companion.relative_to(root).as_posix(),
                    per_sample=image.binding_mode == "per_sample",
                )
                reports.extend(
                    bind_assistant_inputs(
                        workflow, None, datasets, [spec], preflight=True
                    )
                )
    if reports:
        merged = {
            (r["step_id"], r["slot_id"]): r
            for r in workflow.planning_diagnostics.get("input_bindings", [])
        }
        merged.update({(r["step_id"], r["slot_id"]): r for r in reports})
        workflow.planning_diagnostics["input_bindings"] = list(merged.values())
    return reports
