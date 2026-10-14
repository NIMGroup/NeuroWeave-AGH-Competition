from __future__ import annotations

import gzip
import hashlib
import json
import os
import struct
from collections import Counter
from pathlib import Path
from typing import Any
from uuid import uuid4

from ...run_models import RunArtifact
from ...filesystem_scan import ScopedScan
from ...workspace_paths import WorkspacePaths
from ... import workspace_io as secure_io
from ..base import RunnerContext, RunnerResult


class DatasetInspectRunner:
    runner_id = "dataset.inspect"
    label = "数据集真实巡检"

    def runtime_status(self) -> dict[str, object]:
        return {
            "available": True,
            "reason": "使用 NeuroWeave 内置 Python Runner",
            "executable": "python:dataset-inspect",
        }

    def preflight(self, context: RunnerContext) -> list[str]:
        dataset = context.workflow.dataset
        if dataset is None:
            return ["流程没有关联数据集"]
        root = Path(dataset.absolute_path)
        if not root.is_dir():
            return ["数据集目录不存在或当前 Worker 无权读取"]
        try:
            context.output_directory.mkdir(parents=True, exist_ok=True)
            probe = context.output_directory / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError:
            return ["任务输出目录不可写"]
        return []

    def run(self, context: RunnerContext) -> RunnerResult:
        dataset = context.workflow.dataset
        if dataset is None:
            raise RuntimeError("流程没有关联数据集")
        root = Path(dataset.absolute_path).resolve(strict=True)
        parameters = context.step.selected_parameters
        max_files = max(0, int(parameters.get("max_files", 10_000)))
        follow_links = bool(parameters.get("follow_links", False))
        compute_checksums = bool(parameters.get("compute_checksums", False))
        scope = str(parameters.get("inspection_scope", "metadata"))
        sample_subjects = max(1, int(parameters.get("sample_subjects", 5)))
        bids_validation = bool(parameters.get("bids_validation", True))

        context.log("开始扫描数据集目录", 2.0, "info")
        files: list[Path] = []
        policy = WorkspacePaths(root, allowed_roots=(root,))
        scan = ScopedScan(root, policy, follow_links=follow_links, strict=False, check=context.raise_if_cancelled)
        for _, target, info, _ in scan.walk():
            if not target.is_file():
                continue
            files.append(target)
            if max_files and len(files) >= max_files:
                scan.stop_reason = '文件数量上限'
                break
        scan_report = scan.report()

        context.raise_if_cancelled()
        extension_counts: Counter[str] = Counter()
        total_bytes = 0
        checksums: dict[str, str] = {}
        file_records: list[dict[str, Any]] = []
        for index, path in enumerate(files, start=1):
            context.raise_if_cancelled()
            relative = path.relative_to(root).as_posix()
            suffix = self._suffix(path)
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            extension_counts[suffix or "(no extension)"] += 1
            total_bytes += size
            record: dict[str, Any] = {"path": relative, "size_bytes": size, "type": suffix}
            if compute_checksums:
                digest = hashlib.sha256()
                checked = policy.resolve(root, str(path))
                with os.fdopen(secure_io.open_read(checked), 'rb') as handle:
                    while chunk := handle.read(1024 * 1024):
                        context.raise_if_cancelled()
                        digest.update(chunk)
                digest = digest.hexdigest()
                checksums[relative] = digest
                record["sha256"] = digest
            file_records.append(record)
            if index == 1 or index % 250 == 0 or index == len(files):
                progress = 10.0 + (65.0 * index / max(len(files), 1))
                context.log(f"已检查 {index}/{len(files)} 个文件", progress, "info")

        nifti_headers: list[dict[str, Any]] = []
        header_warnings: list[str] = []
        if scope == "headers":
            subject_seen: set[str] = set()
            for path in files:
                if self._suffix(path) not in {".nii", ".nii.gz"}:
                    continue
                subject = next(
                    (part for part in path.relative_to(root).parts if part.startswith("sub-")),
                    path.parent.name,
                )
                if subject not in subject_seen and len(subject_seen) >= sample_subjects:
                    continue
                subject_seen.add(subject)
                try:
                    nifti_headers.append(self._nifti_header(path, root))
                except (OSError, ValueError, struct.error) as exc:
                    header_warnings.append(
                        f"{path.relative_to(root).as_posix()}：无法读取 NIfTI 头（{exc}）"
                    )

        bids_issues: list[str] = []
        if bids_validation:
            if not (root / "dataset_description.json").is_file():
                bids_issues.append("缺少 dataset_description.json")
            if not any(path.is_dir() for path in root.glob("sub-*")):
                bids_issues.append("未发现 sub-* 受试者目录")
            for path in files:
                if self._suffix(path) != ".nii.gz" or "dwi" not in path.name.lower():
                    continue
                stem = path.name[:-7]
                for suffix in (".bval", ".bvec"):
                    try:
                        companion = policy.resolve(root, str(path.with_name(stem + suffix)))
                        available = companion.is_file()
                    except (OSError, RuntimeError, ValueError):
                        available = False
                    if not available:
                        bids_issues.append(
                            f"{path.relative_to(root).as_posix()} 缺少 {stem + suffix}"
                        )

        context.log("正在生成结构化巡检报告", 88.0, "info")
        report = {
            "schema_version": 1,
            "runner_id": self.runner_id,
            "dataset_id": dataset.dataset_id,
            "dataset_name": dataset.display_name,
            "dataset_root": str(root),
            "inspection_scope": scope,
            "truncated": scan.stop_reason is not None,
            **{k: value for k, value in scan_report.items() if k != "warnings"},
            "file_count": len(files),
            "total_bytes": total_bytes,
            "extension_counts": dict(sorted(extension_counts.items())),
            "files": file_records,
            "nifti_headers": nifti_headers,
            "checksums": checksums,
            "bids": {
                "checked": bids_validation,
                "appears_valid": bids_validation and not bids_issues,
                "issues": bids_issues,
            },
            "warnings": [
                *header_warnings,
                *scan_report["warnings"],
            ],
        }
        context.output_directory.mkdir(parents=True, exist_ok=True)
        output = context.output_directory / "dataset_profile.json"
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        digest = context.file_sha256(output)
        artifact = RunArtifact(
            artifact_id=f"artifact_{uuid4().hex[:12]}",
            step_id=context.step.id,
            artifact_type="dataset_profile",
            label="数据集巡检报告",
            absolute_path=str(output),
            relative_path=output.name,
            media_type="application/json",
            size_bytes=output.stat().st_size,
            sha256=digest,
            metadata={
                "file_count": len(files),
                "total_bytes": total_bytes,
                "bids_issue_count": len(bids_issues),
            },
        )
        context.log("巡检报告已生成并通过完整性校验", 100.0, "info")
        return RunnerResult(
            artifacts=[artifact],
            message=f"已检查 {len(files)} 个文件，生成 1 个结构化报告。",
        )

    @staticmethod
    def _within_root(path: Path, root: Path) -> bool:
        try:
            path.resolve(strict=False).relative_to(root)
            return True
        except (OSError, ValueError):
            return False

    @staticmethod
    def _suffix(path: Path) -> str:
        lowered = path.name.lower()
        return ".nii.gz" if lowered.endswith(".nii.gz") else path.suffix.lower()

    @staticmethod
    def _nifti_header(path: Path, root: Path) -> dict[str, Any]:
        path = WorkspacePaths(root, allowed_roots=(root,)).resolve(root, str(path))
        with os.fdopen(secure_io.open_read(path), 'rb') as raw:
            if path.name.lower().endswith('.gz'):
                with gzip.GzipFile(fileobj=raw, mode='rb') as handle:
                    header = handle.read(348)
            else:
                header = raw.read(348)
        if len(header) < 348:
            raise ValueError("文件短于标准 NIfTI-1 头")
        little = struct.unpack("<i", header[:4])[0]
        endian = "<" if little == 348 else ">" if struct.unpack(">i", header[:4])[0] == 348 else ""
        if not endian:
            raise ValueError("sizeof_hdr 不是 348")
        dims = struct.unpack(f"{endian}8h", header[40:56])
        pixdim = struct.unpack(f"{endian}8f", header[76:108])
        rank = max(0, min(int(dims[0]), 7))
        return {
            "path": path.relative_to(root).as_posix(),
            "shape": [int(value) for value in dims[1 : rank + 1]],
            "voxel_sizes": [round(float(value), 6) for value in pixdim[1 : min(rank, 3) + 1]],
            "datatype_code": int(struct.unpack(f"{endian}h", header[70:72])[0]),
        }
