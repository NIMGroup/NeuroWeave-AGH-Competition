"""User-selected input rules; previews describe paths, dry-run checks files."""
from pathlib import Path
from string import Formatter

from ..data_selection import DataSelection
from ..dataset_models import DatasetPathPreview, SamplePathCoverage, SamplePathMatch
from ..operation_context import checkpoint
from ..workflow_models import InputBinding
from ..workspace_paths import WorkspacePaths
from . import contracts


def sample_variables(sample):
    # One portable variable for directory and BIDS layouts; retain registered
    # subject/session entities for more specific filename rules.
    return {**sample.entities, 'sample':sample.relative_root or sample.sample_id}


def rule_preview(workflow, slot, datasets, root_id, relative_path, mode="auto"):
    if not workflow.dataset_id:
        raise ValueError("请先选择数据集，再选择输入文件或批量规则")
    dataset = datasets.get(workflow.dataset_id)
    if root_id != dataset.root_id:
        raise ValueError("输入规则必须属于当前所选数据集的数据根目录")
    root_relative = DataSelection(path=relative_path or ".").path
    prefix = Path(dataset.relative_path).as_posix().strip("/")
    if prefix == ".":
        prefix = ""
    if prefix:
        if root_relative == prefix:
            relative = "."
        elif root_relative.startswith(prefix + "/"):
            relative = root_relative[len(prefix) + 1:]
        else:
            raise ValueError("输入规则超出当前数据集目录")
    else:
        relative = root_relative
    relative = DataSelection(path=relative).path
    fields = []
    for _, field, spec, conversion in Formatter().parse(relative):
        if field is not None:
            if not field.isidentifier() or spec or conversion:
                raise ValueError("路径规则只支持样本变量，例如 {sample}；不支持表达式或格式转换")
            fields.append(field)
    samples = dataset.inspection.samples
    selected = next((s for s in sorted(samples, key=lambda s:len(s.relative_root), reverse=True)
                     if s.relative_root and (relative == s.relative_root or relative.startswith(s.relative_root + "/"))), None)
    template = relative
    if not fields and mode in {"auto", "per_sample"} and selected and relative != ".":
        template = datasets._infer_sample_template(relative, selected)
        fields = [field for _, field, _, _ in Formatter().parse(template) if field]
    if fields and mode == "single":
        raise ValueError("共用一个文件时不能包含样本变量")
    if mode in {"template", "per_sample"} and not fields and relative != ".":
        raise ValueError("无法从所选路径推断样本规则，请输入包含 {sample} 等变量的规则，或选择共用一个文件")
    available = set.intersection(*(set(sample_variables(s)) for s in samples)) if samples else set()
    if set(fields) - available:
        raise ValueError("样本规则包含未知变量：" + ",".join(sorted(set(fields) - available)))
    per_sample = bool(fields)
    matches = []
    if per_sample:
        for sample in samples:
            checkpoint("预览样本输入规则")
            target = DataSelection(path=template.format_map(sample_variables(sample))).path
            matches.append(SamplePathMatch(sample_id=sample.sample_id, label=sample.label,
                relative_path=(Path(dataset.relative_path)/target).as_posix(),
                absolute_path=str(Path(dataset.absolute_path)/target), status="pending",
                message="仅预览路径规则；文件存在性、格式及内容由 dry-run 检查"))
    else:
        matches.append(SamplePathMatch(sample_id="dataset" if relative == "." else "single",
            label=dataset.display_name, relative_path=root_relative,
            absolute_path=str(Path(dataset.absolute_path)/relative), status="pending",
            message="由用户选择；实际文件待 dry-run 检查"))
    return DatasetPathPreview(dataset_id=dataset.dataset_id, validation="pending_dry_run",
        binding_mode="per_sample" if per_sample else "dataset" if relative == "." and slot.binding == "folder" else "single",
        selected_sample_id=selected.sample_id if selected else None,
        selected_relative_path=root_relative, path_template=template, variables=list(dict.fromkeys(fields)),
        coverage=SamplePathCoverage(total=len(matches)), matches=matches, can_apply=True,
        messages=["此预览只展示规则展开，不表示样本输入已合格。",
                  "采用规则后由用户点击 dry-run 统一检查；未匹配样本按范围策略处理。"])


def selected_rule(workflow, slot, datasets, root_id, relative_path, mode="auto"):
    preview = rule_preview(workflow, slot, datasets, root_id, relative_path, mode)
    return InputBinding(slot_id=slot.id, artifact_type=slot.type, source="existing_path",
        confirmed_by_user=True, root_id=root_id, relative_path=preview.selected_relative_path,
        dataset_id=workflow.dataset_id, path_template=preview.path_template,
        binding_mode=preview.binding_mode, variables=preview.variables,
        per_sample_requested=preview.binding_mode == "per_sample", deferred_resolution=True,
        allow_partial=preview.binding_mode == 'per_sample' and workflow.task_contract.sample_policy == "eligible",
        label=preview.path_template, status="selected", messages=preview.messages)


def resolve_user_inputs(workflow, datasets):
    dataset = datasets.get(workflow.dataset_id) if workflow.dataset_id else None
    reports = []
    for step in workflow.steps:
        method = contracts.selected_method(step)
        for slot_id, binding in list(step.input_bindings.items()):
            if binding.source == "upstream_output":
                continue
            if not binding.confirmed_by_user:
                raise ValueError(f"{step.title}/{slot_id}：请先在任务卡选择输入，模型建议不能自动采用")
            if binding.history:
                continue
            if dataset is None:
                raise ValueError("请先选择数据集并配置输入规则")
            if binding.dataset_id != dataset.dataset_id or binding.root_id != dataset.root_id:
                raise ValueError("输入规则与当前数据集不一致，请重新选择")
            slot = next(s for s in method.input_slots if s.id == slot_id)
            root = Path(dataset.absolute_path)
            policy = WorkspacePaths(root, allowed_roots=(root,))
            template = binding.path_template
            if not template:
                raise ValueError(f"{step.title}/{slot.label}：请选择明确路径或样本规则")
            per_sample = binding.binding_mode == "per_sample"
            if per_sample:
                if dataset.inspection.sample_count != len(dataset.inspection.samples):
                    raise ValueError("登记样本范围尚不完整，请更新数据集样本清单后再预检")
                matches = []
                data_root = next(r for r in datasets.roots() if r.root_id == dataset.root_id)
                for sample in dataset.inspection.samples:
                    checkpoint("预检：核对用户选择的样本输入")
                    target = DataSelection(path=template.format_map(sample_variables(sample))).path
                    expanded_sample = sample.model_copy(update={'entities':sample_variables(sample)})
                    match = datasets._expand_sample_match(data_root, root, dataset.relative_path,
                                                          expanded_sample, template, slot.binding, slot.formats)
                    try:
                        policy.resolve(root, target, write=True)
                    except (ValueError, PermissionError, OSError) as exc:
                        match.status, match.message = "invalid", str(exc)
                    matches.append(match)
                coverage = datasets._coverage(matches)
                representative = next((m for m in matches if m.status == "matched"), None)
                absolute = representative.absolute_path if representative else str(root/dataset.inspection.samples[0].relative_root) if dataset.inspection.samples else None
            else:
                target = DataSelection(path=template).path
                candidate = root/target
                try:
                    policy.resolve(root, target, write=True)
                    status, message = datasets._path_match_status(candidate, slot.binding, slot.formats)
                except (ValueError, PermissionError, OSError) as exc:
                    status, message = "invalid", str(exc)
                matches = [SamplePathMatch(sample_id="single", label=dataset.display_name,
                    relative_path=(Path(dataset.relative_path)/target).as_posix(), absolute_path=str(candidate),
                    status=status, message=message)]
                coverage = datasets._coverage(matches)
                absolute = str(candidate)
            binding.sample_matches = matches if per_sample else []
            binding.coverage = coverage
            binding.absolute_path = absolute
            binding.deferred_resolution = False
            binding.status = "validated" if coverage.matched else "selected"
            binding.messages = [f"dry-run 路径核对：{coverage.matched}/{coverage.total} 匹配；内容与兼容性继续预检。"]
            reports.append({"step_id":step.id,"slot_id":slot.id,"path_template":template,
                            "binding_mode":binding.binding_mode,"coverage":coverage.model_dump(),
                            "validation":"dry_run_path_checked"})
    workflow.dataset = dataset or workflow.dataset
    return reports
