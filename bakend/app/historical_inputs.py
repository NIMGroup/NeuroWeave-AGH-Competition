"""Bind recorded outputs to original sample identities without copying scientific data."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from pydantic import Field

from .dataset_models import SamplePathCoverage, SamplePathMatch
from .workflow_models import (
    HistoricalArtifactReference, HistoricalInputReference, InputBinding,
    InputSlotSpec, Workflow, WorkflowMutationRequest,
)


class HistoricalSelectionRequest(WorkflowMutationRequest):
    run_id: str = Field(min_length=1, max_length=100)
    group_id: str = Field(pattern=r"^[0-9a-f]{24}$")
    allow_partial: bool = False


def artifact_problem(reference: HistoricalArtifactReference, *, verify_hash=False) -> str:
    """Check the exact recorded output, never extend a general filesystem allowlist."""
    try:
        path, root = Path(reference.absolute_path), Path(reference.output_directory)
        if (not path.is_absolute() or not root.is_absolute()
                or path.resolve(strict=True) != path or root.resolve(strict=True) != root
                or not path.is_relative_to(root)):
            return "历史输出路径已重定向或超出原运行目录"
        stat = path.stat()
        if reference.is_directory:
            if not path.is_dir():
                return "历史目录已不存在或类型改变"
        elif not path.is_file() or stat.st_size != reference.size_bytes:
            return "历史文件已不存在或大小改变"
        if stat.st_mtime_ns != reference.mtime_ns:
            return "历史输出自绑定后已修改，请重新选择并核对"
        if verify_hash and reference.sha256 and not reference.is_directory:
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest() != reference.sha256:
                return "历史输出校验和与原运行记录不符"
    except (OSError, ValueError):
        return "历史输出不存在或当前节点无法读取"
    return ""


def historical_binding_problems(binding: InputBinding, *, verify_hash=False) -> list[str]:
    if binding.history is None:
        return []
    history = binding.history
    selected_matches = [m for m in binding.sample_matches if m.status == "matched"]
    if binding.binding_mode == "per_sample" and not history.allow_partial and len(selected_matches) != len(binding.sample_matches):
        return ["历史输出尚未覆盖全部样本，需明确选择仅使用匹配样本"]
    paths = ([match.absolute_path for match in selected_matches]
             if binding.binding_mode == "per_sample" else binding.absolute_paths or [binding.absolute_path])
    refs = history.artifacts
    if (binding.source != "existing_path" or history.dataset_id != binding.dataset_id
            or not refs or len(refs) != len(paths)
            or {ref.absolute_path for ref in refs} != set(paths)
            or len(set(paths)) != len(paths)):
        return ["历史输出绑定的来源、路径或样本覆盖不一致"]
    if binding.binding_mode == "per_sample":
        expected = {match.sample_id: match.absolute_path for match in selected_matches}
        if (len(expected) != len(paths) or len({ref.sample_id for ref in refs}) != len(refs)
                or any(expected.get(ref.sample_id) != ref.absolute_path for ref in refs)):
            return ["历史输出的样本标识与路径不一致"]
    return [problem for ref in refs if (problem := artifact_problem(ref, verify_hash=verify_hash))]


def select_history_sample(binding: InputBinding, sample_id: str) -> None:
    if binding.history is not None:
        binding.history.artifacts = [ref for ref in binding.history.artifacts if ref.sample_id == sample_id]
        if len(binding.history.artifacts) != 1:
            raise ValueError("历史输出必须与当前样本唯一对应")


class HistoricalInputs:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def _dataset(workflow: Workflow):
        dataset = workflow.dataset
        if not dataset or dataset.dataset_id != workflow.dataset_id:
            raise ValueError("请先选择原始数据集")
        samples = dataset.inspection.samples
        if (not samples or len(samples) != dataset.inspection.sample_count
                or len({sample.sample_id for sample in samples}) != len(samples)):
            raise ValueError("数据集样本清单不完整或有重复，请解锁流程后重新扫描")
        return dataset

    def _source(self, workflow, run_id, run=None):
        dataset = self._dataset(workflow)
        run = run if run is not None else self.store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.status not in {"succeeded", "failed", "cancelled"}:
            raise ValueError("历史运行尚未结束，不能绑定仍可能变化的输出")
        source = run.plan_snapshot.get("dataset") or {}
        if (run.plan_snapshot.get("dataset_id") != dataset.dataset_id
                or source.get("dataset_id") != dataset.dataset_id
                or not source.get("absolute_path")
                or Path(source["absolute_path"]).resolve() != Path(dataset.absolute_path).resolve()):
            raise ValueError("历史运行不属于当前原始数据集")
        return run

    @staticmethod
    def _groups(run, artifact_type):
        groups = {}
        original_samples = (run.plan_snapshot.get("dataset") or {}).get("inspection", {}).get("samples", [])
        for step in run.steps:
            if step.status != "succeeded":
                continue
            sample_id = step.sample_id
            # Single-sample legacy runs can be identified from their immutable
            # dataset snapshot. Never infer identities from ordinal directories.
            if sample_id is None and len(original_samples) == 1:
                sample_id = original_samples[0]["sample_id"]
            producer = re.sub(r"^sample_[0-9]+__", "", step.step_id) if step.sample_id else step.step_id
            for artifact in step.artifacts:
                if artifact.artifact_type != artifact_type:
                    continue
                output_slot = str(artifact.metadata.get("output_slot_id") or "")
                key = json.dumps([producer, step.method_id, artifact_type, output_slot], ensure_ascii=False)
                group_id = hashlib.sha256(key.encode()).hexdigest()[:24]
                group = groups.setdefault(group_id, {"group_id": group_id, "step_title": step.title,
                    "label": artifact.label, "output_slot_id": output_slot, "items": []})
                group["items"].append((sample_id, step, artifact))
        return groups

    def candidates(self, workflow, slot, *, offset=0, limit=10):
        dataset = self._dataset(workflow)
        runs, more = self.store.historical_runs(dataset.dataset_id, offset=offset, limit=limit)
        choices = []
        for run in runs:
            try:
                self._source(workflow, run.run_id, run)
            except ValueError:
                continue
            for group in self._groups(run, slot.type).values():
                ids = {sample_id for sample_id, _, _ in group["items"] if sample_id is not None}
                choices.append({"run_id": run.run_id, "run_title": run.plan_snapshot.get("title", run.run_id),
                    "run_status": run.status, "created_at": run.created_at,
                    **{key: group[key] for key in ("group_id", "step_title", "label", "output_slot_id")},
                    "sample_count": len(ids), "artifact_count": len(group["items"])})
        return {"candidates": choices, "has_more": more, "next_offset": offset + len(runs)}

    def _recorded_output_directory(self, run, step, artifact):
        """Retries may reuse an ancestor's artifact; follow recorded lineage only."""
        seen = set()
        while run.run_id not in seen:
            seen.add(run.run_id)
            root = Path(run.output_directory)
            if root.is_absolute() and Path(artifact.absolute_path).is_relative_to(root):
                return run.output_directory
            if step.step_id not in run.reused_step_ids or not run.retry_of:
                break
            ancestor = self.store.get_run(run.retry_of)
            if (ancestor is None or ancestor.status not in {"succeeded", "failed", "cancelled"}
                    or ancestor.plan_snapshot.get("dataset_id") != run.plan_snapshot.get("dataset_id")):
                break
            candidates = [(item, value) for item in ancestor.steps if item.status == "succeeded"
                for value in item.artifacts if value.artifact_id == artifact.artifact_id
                and value.absolute_path == artifact.absolute_path and value.sha256 == artifact.sha256
                and value.size_bytes == artifact.size_bytes and value.artifact_type == artifact.artifact_type]
            if len(candidates) != 1:
                break
            step, artifact = candidates[0]
            run = ancestor
        raise ValueError("输出不在原运行目录内，且没有可验证的重试复用记录")

    def preview(self, workflow: Workflow, slot: InputSlotSpec, run_id: str, group_id: str, *, allow_partial=False):
        dataset = self._dataset(workflow)
        run = self._source(workflow, run_id)
        group = self._groups(run, slot.type).get(group_id)
        if group is None:
            raise ValueError("该历史运行没有与当前输入类型匹配的成功输出")
        original_samples = (run.plan_snapshot["dataset"].get("inspection") or {}).get("samples", [])
        original = {sample["sample_id"]: sample.get("relative_root", "") for sample in original_samples}
        matches, refs = [], []
        for sample in dataset.inspection.samples:
            items = [item for item in group["items"] if item[0] == sample.sample_id]
            path, status, message, reference = "", "missing", "此样本没有成功的历史输出", None
            if len(items) > 1:
                status, message = "invalid", "此样本存在多个同类型输出，无法唯一匹配"
            elif len(items) == 1:
                _, step, artifact = items[0]
                path = artifact.absolute_path
                status, message = "matched", "按原始样本标识匹配"
                try:
                    candidate = Path(path)
                    output_directory = self._recorded_output_directory(run, step, artifact)
                    root = Path(output_directory)
                    if (not candidate.is_absolute() or candidate.resolve(strict=True) != candidate
                            or root.resolve(strict=True) != root or not candidate.is_relative_to(root)):
                        raise ValueError("输出路径重定向或不在原运行目录内")
                    if original.get(sample.sample_id) != sample.relative_root:
                        raise ValueError("原始样本目录标识与当前数据集不一致")
                    if slot.binding == "folder":
                        if not candidate.is_dir():
                            raise ValueError("此输入需要目录")
                    elif not candidate.is_file():
                        raise ValueError("此输入需要文件")
                    if candidate.is_file() and slot.formats and not any(
                            candidate.name.lower().endswith(ext.lower()) for ext in slot.formats):
                        raise ValueError("文件格式不符合当前输入要求")
                    stat = candidate.stat()
                    if candidate.is_file() and stat.st_size != artifact.size_bytes:
                        raise ValueError("文件大小与原输出记录不符")
                    if not candidate.is_dir() and stat.st_size == 0:
                        raise ValueError("历史输出文件为空")
                    reference = HistoricalArtifactReference(sample_id=sample.sample_id,
                        step_id=step.step_id, artifact_id=artifact.artifact_id, absolute_path=path,
                        output_directory=output_directory, size_bytes=artifact.size_bytes,
                        mtime_ns=stat.st_mtime_ns, sha256=artifact.sha256, is_directory=candidate.is_dir())
                except (ValueError, OSError) as error:
                    status, message = "invalid", str(error) if isinstance(error, ValueError) else "文件不存在或无法读取"
            matches.append(SamplePathMatch(sample_id=sample.sample_id, label=sample.label,
                relative_path="", absolute_path=path, status=status, message=message))
            if reference and status == "matched":
                refs.append(reference)
        # Even an incorrectly recorded artifact must not be shared silently
        # between different subjects.
        duplicates = {path for path, count in Counter(ref.absolute_path for ref in refs).items() if count > 1}
        for match in matches:
            if match.absolute_path in duplicates:
                match.status, match.message = "invalid", "不同样本指向了同一个输出文件"
        coverage = SamplePathCoverage(total=len(matches),
            matched=sum(match.status == "matched" for match in matches),
            missing=sum(match.status == "missing" for match in matches),
            invalid=sum(match.status == "invalid" for match in matches))
        can_apply = coverage.matched > 0 and (allow_partial or coverage.matched == coverage.total)
        # Ambiguous/shared/invalid paths remain visible as skipped, never as
        # executable provenance, including duplicate artifacts from failed runs.
        matched_ids = {match.sample_id for match in matches if match.status == "matched"}
        refs = [ref for ref in refs if ref.sample_id in matched_ids]
        history = HistoricalInputReference(run_id=run_id, group_id=group_id,
            dataset_id=dataset.dataset_id, artifacts=refs, allow_partial=allow_partial)
        binding = InputBinding(slot_id=slot.id, artifact_type=slot.type, source="existing_path",
            label=f"历史运行 {run_id} · {group['label']} · {coverage.matched} 个样本",
            absolute_path=refs[0].absolute_path if refs else None, dataset_id=dataset.dataset_id,
            binding_mode="per_sample" if len(matches) > 1 else "single",
            coverage=coverage, sample_matches=matches if len(matches) > 1 else [], history=history,
            status="validated", messages=["按原数据集和样本标识绑定历史输出；文件保留在原位置。"])
        return {"run_id": run_id, "group_id": group_id, "coverage": coverage.model_dump(),
                "matches": [match.model_dump() for match in matches], "can_apply": can_apply,
                "partial": coverage.matched < coverage.total,
                "messages": ["仅采用已结束运行中成功步骤登记的输出。",
                    "多个输入取共同匹配的样本，其余样本不提交执行。",
                    "路径匹配不代表影像空间已对齐；执行时会复核文件及已有校验和。"]}, binding

    def validate_workflow(self, workflow):
        problems = []
        for step in workflow.steps:
            for slot_id, binding in step.input_bindings.items():
                if binding.history is None:
                    continue
                method = next((item for item in step.method_options if item.id == step.selected_method_id), None)
                slot = next((item for item in method.input_slots if item.id == slot_id), None) if method else None
                try:
                    if slot is None or "existing_path" not in slot.source_options:
                        raise ValueError("当前方法不接受此历史输入")
                    preview, expected = self.preview(workflow, slot, binding.history.run_id, binding.history.group_id,
                                                     allow_partial=binding.history.allow_partial)
                    if not preview["can_apply"] or expected.history != binding.history:
                        raise ValueError("历史输出或样本覆盖已改变，请重新选择")
                    problems.extend(historical_binding_problems(binding))
                except (KeyError, ValueError, OSError) as error:
                    problems.append(f"{step.title} / {slot_id}：{error}")
        return problems
