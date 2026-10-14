from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from ..base import RunnerContext, RunnerResult
from ..external_command import ExternalCommandRunner


class BidsPrepareRunner:
    runner_id = "bids.prepare"
    label = "BIDS 安全整理与校验"

    def runtime_status(self) -> dict[str, object]:
        return {
            "available": True,
            "reason": "使用 NeuroWeave 内置只读输入 BIDS Runner",
            "executable": "python:bids-prepare",
            "environment_profile": "localflow-backend",
        }

    def preflight(self, context: RunnerContext) -> list[str]:
        problems: list[str] = []
        binding = context.step.input_bindings.get("source_dataset")
        paths = ExternalCommandRunner.binding_paths(binding) if binding else []
        if context.input_is_deferred("source_dataset"):
            pass  # Inspect the upstream directory once it has been produced.
        elif len(paths) != 1 or not paths[0].is_dir():
            problems.append("必须绑定一个可读的源数据目录")
        elif not ExternalCommandRunner._allowed_binding_input(binding, paths[0], context.output_directory.parent):
            problems.append("源数据目录超出允许运行的数据根")
        try:
            context.output_directory.mkdir(parents=True, exist_ok=True)
            probe = context.output_directory / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            problems.append(f"输出目录不可写：{exc}")
        return problems

    def run(self, context: RunnerContext) -> RunnerResult:
        binding = context.step.input_bindings.get("source_dataset")
        paths = ExternalCommandRunner.binding_paths(binding) if binding else []
        if len(paths) != 1:
            raise RuntimeError("BIDS 整理当前只接受一个源目录")
        source = paths[0].resolve(strict=True)
        destination = context.output_directory / "bids"
        destination.mkdir(parents=True, exist_ok=True)
        copied = 0
        skipped_links = 0
        context.log("正在把源数据复制到隔离的 BIDS 输出目录", 5.0, "info")
        for current, directories, files in os.walk(source, followlinks=False):
            context.raise_if_cancelled()
            current_path = Path(current)
            directories[:] = [
                name for name in directories if not (current_path / name).is_symlink()
            ]
            target_directory = destination / current_path.relative_to(source)
            target_directory.mkdir(parents=True, exist_ok=True)
            for name in files:
                source_file = current_path / name
                if source_file.is_symlink():
                    skipped_links += 1
                    continue
                target_file = target_directory / name
                shutil.copy2(source_file, target_file)
                copied += 1
                if copied % 250 == 0:
                    context.log(f"已复制 {copied} 个文件", min(75.0, 5.0 + copied / 50), "info")
        if copied == 0:
            raise RuntimeError("源目录中没有可复制的普通文件")

        description_path = destination / "dataset_description.json"
        if description_path.is_file():
            try:
                description = json.loads(description_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"dataset_description.json 无效：{exc}") from exc
        else:
            description = {"Name": source.name or "NeuroWeave dataset", "BIDSVersion": "1.9.0"}
        description.setdefault("Name", source.name or "NeuroWeave dataset")
        description.setdefault("BIDSVersion", "1.9.0")
        description_path.write_text(json.dumps(description, ensure_ascii=False, indent=2), encoding="utf-8")

        scrubbed = 0
        if bool(context.step.selected_parameters.get("anonymize_metadata", True)):
            sensitive_prefixes = ("Patient", "Institution", "ReferringPhysician")
            sensitive_keys = {"AcquisitionDateTime", "AcquisitionDate", "StudyDate", "SeriesDate"}
            for sidecar in destination.rglob("*.json"):
                try:
                    payload = json.loads(sidecar.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                removed = [
                    key for key in payload
                    if key in sensitive_keys or key.startswith(sensitive_prefixes)
                ]
                if removed:
                    for key in removed:
                        payload.pop(key, None)
                    sidecar.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                    scrubbed += len(removed)

        subjects = sorted(path.name for path in destination.glob("sub-*") if path.is_dir())
        nifti = [path for path in destination.rglob("*.nii*") if path.is_file()]
        issues: list[str] = []
        if not subjects:
            issues.append("未发现 sub-* 受试者目录；需要在运行前先定义实体映射")
        if not nifti:
            issues.append("未发现 NIfTI 影像；DICOM 输入应先使用 dcm2niix 转换")
        if str(context.step.selected_parameters.get("validation_level", "strict")) == "strict" and issues:
            raise RuntimeError("BIDS 严格校验未通过：" + "；".join(issues))
        report_path = context.output_directory / "bids-validation.json"
        report = {
            "schema_version": 1,
            "copied_files": copied,
            "skipped_symlinks": skipped_links,
            "subject_count": len(subjects),
            "nifti_count": len(nifti),
            "anonymized_fields": scrubbed,
            "issues": issues,
            "valid_basic": not issues,
        }
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        artifacts = [
            ExternalCommandRunner.directory_artifact(
                context, destination, "bids_dwi", "BIDS 数据集副本", report
            ),
            ExternalCommandRunner.artifact(
                context, report_path, "generated_file", "BIDS 校验报告", "application/json"
            ),
        ]
        context.log("BIDS 副本和校验报告已完成完整性检查", 100.0, "info")
        return RunnerResult(artifacts=artifacts, message=f"BIDS 整理完成，共复制 {copied} 个文件。")
