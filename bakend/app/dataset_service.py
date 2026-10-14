from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from threading import RLock

from .dataset_models import (
    DataRoot,
    DatasetPathPreview,
    DatasetInspection,
    DatasetRecord,
    DatasetSample,
    DirectoryEntry,
    DirectoryListing,
    FileEntry,
    PathBreadcrumb,
    SamplePathCoverage,
    SamplePathMatch,
)
from .local_store import LocalStore
from .operation_context import checkpoint


class DatasetService:
    MAX_FILES = 5_000
    MAX_SAMPLES = 1_000
    MAX_DIRECTORY_ENTRIES = 300
    MAX_FILE_ENTRIES = 300
    DWI_NOT_FOUND_WARNING = "没有识别到 DWI NIfTI 或 DICOM 文件。"
    DWI_GRADIENT_WARNING = "检测到 DWI，但 bval/bvec 不完整。"
    REVERSE_PHASE_WARNING = "未识别到成对反向相位编码数据，畸变校正能力可能受限。"

    def __init__(self, project_root: Path, store: LocalStore) -> None:
        self.project_root = project_root
        self.store = store
        self.registration_lock = RLock()
        self.registration_guard = lambda _path: None
        self.allow_hidden_paths = os.getenv("LOCALFLOW_ALLOW_HIDDEN_PATHS", "false").lower() in {
            "1",
            "true",
            "yes",
        }
        self._roots = self._load_roots()

    def roots(self) -> list[DataRoot]:
        return list(self._roots.values())

    def list_directories(self, root_id: str, relative_path: str = "") -> DirectoryListing:
        root, directory, normalized = self._resolve(root_id, relative_path)
        entries: list[DirectoryEntry] = []
        files: list[FileEntry] = []
        directory_count = 0
        file_count = 0
        root_path = Path(root.path).resolve()
        try:
            children = sorted(directory.iterdir(), key=lambda item: item.name.lower())
        except OSError as exc:
            raise ValueError(f"无法读取目录：{exc}") from exc
        for child in children:
            if child.name.startswith("."):
                continue
            try:
                resolved = child.resolve()
            except OSError:
                continue
            if not self._is_within(resolved, root_path):
                continue
            child_relative = resolved.relative_to(root_path).as_posix()
            try:
                is_directory = child.is_dir()
                is_file = child.is_file()
                readable = os.access(resolved, os.R_OK)
                is_symlink = child.is_symlink()
            except OSError:
                continue
            if is_directory:
                directory_count += 1
                if len(entries) < self.MAX_DIRECTORY_ENTRIES:
                    entries.append(
                        DirectoryEntry(
                            name=child.name,
                            relative_path=child_relative,
                            readable=readable,
                            is_symlink=is_symlink,
                        )
                    )
            elif is_file:
                file_count += 1
                if len(files) >= self.MAX_FILE_ENTRIES:
                    continue
                try:
                    size_bytes = child.stat().st_size
                except OSError:
                    size_bytes = 0
                suffixes = "".join(child.suffixes).lower()
                files.append(
                    FileEntry(
                        name=child.name,
                        relative_path=child_relative,
                        suffix=suffixes,
                        size_bytes=size_bytes,
                        readable=readable,
                        is_symlink=is_symlink,
                    )
                )
        parent = None if not normalized else Path(normalized).parent.as_posix()
        if parent == ".":
            parent = ""
        return DirectoryListing(
            root=root,
            relative_path=normalized,
            parent_relative_path=parent,
            breadcrumbs=self._breadcrumbs(root, normalized),
            directories=entries,
            files=files,
            directory_count=directory_count,
            file_count=file_count,
            directories_truncated=directory_count > len(entries),
            files_truncated=file_count > len(files),
        )

    def resolve_artifact_path(
        self,
        root_id: str,
        relative_path: str,
    ) -> tuple[DataRoot, Path, str]:
        root = self._roots.get(root_id)
        if root is None:
            raise ValueError("未知或未授权的数据根目录")
        root_path = Path(root.path).resolve()
        normalized_input = self._normalize_relative_path(relative_path)
        candidate = (root_path / normalized_input).resolve()
        if not self._is_within(candidate, root_path):
            raise ValueError("路径超出授权的数据根目录")
        if not candidate.exists():
            raise ValueError("选择的输入路径不存在")
        self._authorized_path(candidate)
        normalized = candidate.relative_to(root_path).as_posix()
        return root, candidate, "" if normalized == "." else normalized

    def register(
        self,
        root_id: str,
        relative_path: str,
        display_name: str | None = None,
    ) -> DatasetRecord:
        root, directory, normalized = self._resolve(root_id, relative_path)
        inspection = self._inspect(directory)
        now = self._now()
        existing = self.store.find_dataset_by_path(root_id, normalized)
        dataset = DatasetRecord(
            workspace_id=existing.workspace_id if existing else None,
            dataset_id=existing.dataset_id if existing else f"ds_{uuid4().hex[:12]}",
            root_id=root_id,
            relative_path=normalized,
            display_name=(display_name or directory.name or root.label).strip(),
            absolute_path=str(directory),
            inspection=inspection,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        checkpoint()
        with self.registration_lock:
            self.registration_guard(directory)
            self.store.save_dataset(dataset)
        return dataset

    def inspect(self, dataset_id: str) -> DatasetRecord:
        dataset = self.get(dataset_id)
        _root, directory, _normalized = self._resolve(dataset.root_id, dataset.relative_path)
        updated = dataset.model_copy(
            update={"inspection": self._inspect(directory), "updated_at": self._now()}
        )
        checkpoint()
        self.store.save_dataset(updated)
        return updated

    def get(self, dataset_id: str) -> DatasetRecord:
        from .data_auth import principal
        principal.get().check("dataset", dataset_id)
        dataset = self.store.get_dataset(dataset_id)
        if dataset is None:
            raise KeyError(dataset_id)
        return dataset

    def list(self) -> list[DatasetRecord]:
        from .data_auth import principal
        return [d for d in self.store.list_datasets() if "*" in principal.get().datasets or d.dataset_id in principal.get().datasets]

    def preview_sample_mapping(
        self,
        dataset_id: str,
        root_id: str,
        relative_path: str,
        *,
        binding: str,
        formats: list[str],
    ) -> DatasetPathPreview:
        dataset = self.get(dataset_id)
        if dataset.inspection.sample_count == 0 or not dataset.inspection.samples:
            dataset = self.inspect(dataset_id)
        if root_id != dataset.root_id:
            raise ValueError("请选择当前数据集所在的数据根目录")
        root, candidate, normalized = self.resolve_artifact_path(root_id, relative_path)
        dataset_path = Path(dataset.absolute_path).resolve()
        if not self._is_within(candidate, dataset_path):
            raise ValueError("选择的路径不在当前数据集目录中")

        selected_dataset_relative = candidate.relative_to(dataset_path).as_posix()
        if selected_dataset_relative == ".":
            selected_dataset_relative = ""
        selected_status, selected_message = self._path_match_status(candidate, binding, formats)
        if candidate == dataset_path:
            match = SamplePathMatch(
                sample_id="dataset",
                label=dataset.display_name,
                relative_path=normalized,
                absolute_path=str(candidate),
                status=selected_status,
                message=selected_message,
            )
            return DatasetPathPreview(
                dataset_id=dataset.dataset_id,
                binding_mode="dataset",
                selected_relative_path=normalized,
                coverage=self._coverage([match]),
                matches=[match],
                can_apply=selected_status == "matched",
                messages=["将当前数据集目录作为一个整体输入。"],
            )

        samples = dataset.inspection.samples
        selected_sample = next(
            (
                sample
                for sample in sorted(samples, key=lambda item: len(item.relative_root), reverse=True)
                if sample.relative_root
                and (
                    selected_dataset_relative == sample.relative_root
                    or selected_dataset_relative.startswith(sample.relative_root + "/")
                )
            ),
            None,
        )
        if len(samples) > 1 and selected_sample is not None:
            template = self._infer_sample_template(selected_dataset_relative, selected_sample)
            variables = list(dict.fromkeys(re.findall(r"\{([a-z_]+)\}", template)))
            if variables:
                matches = [
                    self._expand_sample_match(
                        root,
                        dataset_path,
                        dataset.relative_path,
                        sample,
                        template,
                        binding,
                        formats,
                    )
                    for sample in samples
                ]
                coverage = self._coverage(matches)
                messages = [
                    f"已从 {selected_sample.label} 推断路径规则，并检查 {coverage.total} 个样本。"
                ]
                if coverage.missing:
                    messages.append(f"有 {coverage.missing} 个样本未找到对应路径。")
                if coverage.invalid:
                    messages.append(f"有 {coverage.invalid} 个样本的路径类型或格式不符合要求。")
                return DatasetPathPreview(
                    dataset_id=dataset.dataset_id,
                    binding_mode="per_sample",
                    selected_sample_id=selected_sample.sample_id,
                    selected_relative_path=normalized,
                    path_template=template,
                    variables=variables,
                    coverage=coverage,
                    matches=matches,
                    can_apply=(
                        selected_status == "matched"
                        and coverage.total > 0
                        and coverage.matched == coverage.total
                    ),
                    messages=messages,
                )

        match = SamplePathMatch(
            sample_id=selected_sample.sample_id if selected_sample else "single",
            label=selected_sample.label if selected_sample else dataset.display_name,
            relative_path=normalized,
            absolute_path=str(candidate),
            status=selected_status,
            message=selected_message,
        )
        return DatasetPathPreview(
            dataset_id=dataset.dataset_id,
            binding_mode="single",
            selected_sample_id=selected_sample.sample_id if selected_sample else None,
            selected_relative_path=normalized,
            path_template=selected_dataset_relative,
            coverage=self._coverage([match]),
            matches=[match],
            can_apply=selected_status == "matched",
            messages=["当前选择将作为单一路径输入。"],
        )

    def _load_roots(self) -> dict[str, DataRoot]:
        configured = [
            item.strip()
            for item in os.getenv("LOCALFLOW_DATA_ROOTS", "").split(";")
            if item.strip()
        ]
        root_specs: list[tuple[str, Path]] = []
        for item in configured:
            label = ""
            path_text = item
            if "::" in item:
                label, path_text = item.split("::", 1)
            path = Path(path_text.strip()).expanduser().resolve()
            root_specs.append((label.strip(), path))
        if not root_specs:
            default_root = (self.project_root / "data").resolve()
            default_root.mkdir(parents=True, exist_ok=True)
            root_specs = [("", default_root)]
        roots: dict[str, DataRoot] = {}
        for configured_label, path in root_specs:
            if not path.is_dir():
                continue
            root_id = "root_" + hashlib.sha256(str(path).lower().encode("utf-8")).hexdigest()[:10]
            roots[root_id] = DataRoot(
                root_id=root_id,
                label=configured_label or path.name or str(path),
                path=str(path),
                readable=os.access(path, os.R_OK),
                writable=os.access(path, os.W_OK),
            )
        if not roots:
            raise RuntimeError("LOCALFLOW_DATA_ROOTS 中没有可用目录")
        return roots

    def _resolve(self, root_id: str, relative_path: str) -> tuple[DataRoot, Path, str]:
        root = self._roots.get(root_id)
        if root is None:
            raise ValueError("未知或未授权的数据根目录")
        root_path = Path(root.path).resolve()
        normalized_input = self._normalize_relative_path(relative_path)
        candidate = (root_path / normalized_input).resolve()
        if not self._is_within(candidate, root_path):
            raise ValueError("目录超出授权的数据根目录")
        if not candidate.is_dir():
            raise ValueError("选择的数据目录不存在")
        self._authorized_path(candidate)
        normalized = candidate.relative_to(root_path).as_posix()
        return root, candidate, "" if normalized == "." else normalized

    def _authorized_path(self, path):
        from .data_auth import principal
        if "*" not in principal.get().datasets and not any(self._is_within(path, Path(d.absolute_path)) for d in self.list()):
            raise PermissionError("当前账户未获授权浏览这个数据目录")

    def _inspect(self, directory: Path) -> DatasetInspection:
        file_count = 0
        total_bytes = 0
        truncated = False
        names: list[str] = []
        bval_files: list[Path] = []
        access_warnings: list[str] = []
        skipped_symlinks = 0

        def on_error(error: OSError) -> None:
            access_warnings.append(str(error))

        for current, dirs, files in os.walk(directory, followlinks=False, onerror=on_error):
            checkpoint(f"已扫描 {file_count} 个文件")
            current_path = Path(current).resolve()
            skipped_symlinks += sum((current_path / name).is_symlink() for name in dirs)
            dirs[:] = [
                name
                for name in dirs
                if not name.startswith(".")
                and not (current_path / name).is_symlink()
                and self._is_within((current_path / name).resolve(), directory)
            ]
            for filename in files:
                checkpoint()
                path = current_path / filename
                if path.is_symlink():
                    skipped_symlinks += 1
                    continue
                if not self._is_within(path.resolve(), directory):
                    continue
                file_count += 1
                names.append(filename.lower())
                try:
                    total_bytes += path.stat().st_size
                except OSError:
                    pass
                if filename.lower().endswith(".bval"):
                    bval_files.append(path)
                if file_count >= self.MAX_FILES:
                    truncated = True
                    break
            if truncated:
                break

        name_set = set(names)
        is_bids = "dataset_description.json" in name_set
        has_bval = any(name.endswith(".bval") for name in names)
        has_bvec = any(name.endswith(".bvec") for name in names)
        image_names = [
            name
            for name in names
            if name.endswith((".nii", ".nii.gz", ".mif"))
        ]
        has_dwi = any(
            re.search(r"(^|[_-])dwi([_.-]|$)", name)
            and not any(marker in name for marker in ("mask", "fod", "5tt", "response"))
            for name in image_names
        )
        has_t1w = any(
            re.search(r"(^|[_-])(t1w?|mprage)([_\-\.]|$)", name)
            for name in image_names
        )
        has_preprocessed_dwi = any(
            marker in name
            for name in names
            for marker in (
                "desc-preproc_dwi",
                "preprocessed_dwi",
                "dwi_preproc",
                "eddy_corrected",
            )
        )
        has_fod = any(
            "fod" in name and name.endswith((".mif", ".nii", ".nii.gz"))
            for name in names
        )
        has_five_tt = any(
            re.search(r"(^|[_-])5tt([_.-]|$)", name)
            and name.endswith((".mif", ".nii", ".nii.gz"))
            for name in names
        )
        joined = " ".join(names).upper()
        has_reverse_phase = ("DIR-AP" in joined and "DIR-PA" in joined) or (
            "_AP" in joined and "_PA" in joined
        )
        has_dicom = any(name.endswith(".dcm") for name in names)
        # A bval belongs to its named sequence, never an arbitrary global first
        # match. A dataset-level shell count is only known for a complete,
        # homogeneous set of boundedly inspected gradients.
        from .data_sequences import gradient_summary, read_small
        profiles = []
        for path in sorted(bval_files)[:32]:
            checkpoint()
            try:
                profiles.append({'bval': path.relative_to(directory).as_posix(),
                    'association': 'exact_stem', 'gradients': gradient_summary(read_small(path))})
            except (OSError, ValueError, UnicodeError):
                profiles.append({'bval': path.relative_to(directory).as_posix(), 'status': 'unknown'})
        profiles_complete = not truncated and not access_warnings and not skipped_symlinks and len(profiles) == len(bval_files)
        counts = {p['gradients']['shell_count'] for p in profiles if 'gradients' in p}
        shell_count = next(iter(counts)) if profiles_complete and len(counts) == 1 and all('gradients' in p for p in profiles) else None
        input_types = ["dataset_folder"]
        if is_bids and has_dwi:
            input_types.append("bids_dwi")
        if has_dwi or has_bval or has_bvec or has_dicom:
            input_types.append("raw_dwi")
        if has_t1w:
            input_types.append("t1w_image")
        if has_preprocessed_dwi and has_bval and has_bvec:
            input_types.append("preprocessed_dwi")
        if has_fod:
            input_types.append("fod_image")
        if has_five_tt:
            input_types.append("five_tt_image")

        sample_layout, samples, samples_truncated = self._index_samples(directory, is_bids)

        warnings = access_warnings[:3]
        if skipped_symlinks:
            warnings.append(f'登记扫描跳过了 {skipped_symlinks} 个符号链接；未核验其目标，缺失特征保持未知。')
        if not has_dwi and not has_dicom:
            warnings.append(self.DWI_NOT_FOUND_WARNING)
        if has_dwi and (not has_bval or not has_bvec):
            warnings.append(self.DWI_GRADIENT_WARNING)
        if not has_reverse_phase:
            warnings.append(self.REVERSE_PHASE_WARNING)
        if truncated:
            warnings.append(f"目录文件超过 {self.MAX_FILES} 个，本次只进行了有界扫描。")
        if samples_truncated:
            warnings.append(f"样本数量超过 {self.MAX_SAMPLES} 个，本次只索引前 {self.MAX_SAMPLES} 个。")

        features = {
            "is_bids": is_bids,
            "has_dwi": has_dwi,
            "has_bval": has_bval,
            "has_bvec": has_bvec,
            "has_t1w": has_t1w,
            "has_preprocessed_dwi": has_preprocessed_dwi,
            "has_reverse_phase": has_reverse_phase,
            "has_fod": has_fod,
            "has_five_tt": has_five_tt,
        }
        scan_complete = not truncated and not access_warnings and not skipped_symlinks
        states = {key: 'found' if value else 'missing' if scan_complete else 'unknown' for key, value in features.items()}
        # Filename recognition is a hint, not verified processing provenance.
        states['has_preprocessed_dwi'] = 'unknown'
        return DatasetInspection(
            file_count=file_count,
            total_bytes=total_bytes,
            truncated=truncated,
            is_bids=is_bids,
            has_dwi=has_dwi,
            has_bval=has_bval,
            has_bvec=has_bvec,
            has_t1w=has_t1w,
            has_preprocessed_dwi=has_preprocessed_dwi,
            has_reverse_phase=has_reverse_phase,
            has_fod=has_fod,
            has_five_tt=has_five_tt,
            shell_count=shell_count,
            input_types=input_types,
            features=features,
            feature_states=states,
            scan_complete=scan_complete,
            skipped_symlinks=skipped_symlinks,
            sequence_profiles=profiles,
            sequence_profiles_complete=profiles_complete,
            sample_layout=sample_layout,
            sample_count=len(samples),
            samples=samples,
            warnings=warnings,
            scanned_at=self._now(),
        )

    def _index_samples(
        self,
        directory: Path,
        is_bids: bool,
    ) -> tuple[str, list[DatasetSample], bool]:
        try:
            children = [
                child
                for child in sorted(directory.iterdir(), key=lambda item: item.name.lower())
                if child.is_dir()
                and not child.name.startswith(".")
                and self._is_within(child.resolve(), directory)
            ]
        except OSError:
            return "single", [self._single_sample(directory)], False

        samples: list[DatasetSample] = []
        if is_bids:
            for subject_dir in (child for child in children if child.name.startswith("sub-")):
                try:
                    sessions = [
                        child
                        for child in sorted(subject_dir.iterdir(), key=lambda item: item.name.lower())
                        if child.is_dir()
                        and child.name.startswith("ses-")
                        and self._is_within(child.resolve(), directory)
                    ]
                except OSError:
                    sessions = []
                if sessions:
                    for session_dir in sessions:
                        relative_root = session_dir.relative_to(directory).as_posix()
                        samples.append(
                            DatasetSample(
                                sample_id=relative_root,
                                label=f"{subject_dir.name} / {session_dir.name}",
                                relative_root=relative_root,
                                entities={
                                    "subject": subject_dir.name,
                                    "session": session_dir.name,
                                },
                            )
                        )
                else:
                    relative_root = subject_dir.relative_to(directory).as_posix()
                    samples.append(
                        DatasetSample(
                            sample_id=relative_root,
                            label=subject_dir.name,
                            relative_root=relative_root,
                            entities={"subject": subject_dir.name},
                        )
                    )
        if samples:
            truncated = len(samples) > self.MAX_SAMPLES
            return "bids", samples[: self.MAX_SAMPLES], truncated

        generic = [
            DatasetSample(
                sample_id=child.name,
                label=child.name,
                relative_root=child.relative_to(directory).as_posix(),
                entities={"sample": child.name},
            )
            for child in children
        ]
        if generic:
            truncated = len(generic) > self.MAX_SAMPLES
            return "directory", generic[: self.MAX_SAMPLES], truncated
        return "single", [self._single_sample(directory)], False

    @staticmethod
    def _single_sample(directory: Path) -> DatasetSample:
        return DatasetSample(
            sample_id="dataset",
            label=directory.name or "dataset",
            relative_root="",
            entities={},
        )

    @staticmethod
    def _infer_sample_template(relative_path: str, sample: DatasetSample) -> str:
        template = relative_path
        for key, value in sorted(sample.entities.items(), key=lambda item: len(item[1]), reverse=True):
            template = template.replace(value, "{" + key + "}")
        return template

    def _expand_sample_match(
        self,
        root: DataRoot,
        dataset_path: Path,
        dataset_relative_path: str,
        sample: DatasetSample,
        template: str,
        binding: str,
        formats: list[str],
    ) -> SamplePathMatch:
        try:
            rendered = template.format_map(sample.entities)
        except KeyError as exc:
            return SamplePathMatch(
                sample_id=sample.sample_id,
                label=sample.label,
                relative_path="",
                absolute_path="",
                status="invalid",
                message=f"样本缺少模板变量：{exc.args[0]}",
            )
        candidate = (dataset_path / rendered).resolve()
        if not self._is_within(candidate, dataset_path):
            return SamplePathMatch(
                sample_id=sample.sample_id,
                label=sample.label,
                relative_path="",
                absolute_path=str(candidate),
                status="invalid",
                message="展开后的路径超出数据集目录。",
            )
        root_path = Path(root.path).resolve()
        relative = candidate.relative_to(root_path).as_posix()
        status, message = self._path_match_status(candidate, binding, formats)
        return SamplePathMatch(
            sample_id=sample.sample_id,
            label=sample.label,
            relative_path=relative,
            absolute_path=str(candidate),
            status=status,
            message=message,
        )

    @staticmethod
    def _path_match_status(
        candidate: Path,
        binding: str,
        formats: list[str],
    ) -> tuple[str, str]:
        if not candidate.exists():
            return "missing", "路径不存在"
        if binding == "folder" and not candidate.is_dir():
            return "invalid", "需要文件夹"
        if binding == "file" and not candidate.is_file():
            return "invalid", "需要文件"
        if candidate.is_file() and formats:
            lowered = candidate.name.lower()
            if not any(lowered.endswith(item.lower()) for item in formats):
                return "invalid", "文件格式不符合输入要求"
        return "matched", "路径匹配"

    @staticmethod
    def _coverage(matches: list[SamplePathMatch]) -> SamplePathCoverage:
        return SamplePathCoverage(
            total=len(matches),
            matched=sum(item.status == "matched" for item in matches),
            missing=sum(item.status == "missing" for item in matches),
            invalid=sum(item.status == "invalid" for item in matches),
        )

    @staticmethod
    def _is_within(candidate: Path, root: Path) -> bool:
        try:
            candidate.relative_to(root)
            return True
        except ValueError:
            return False

    def _normalize_relative_path(self, relative_path: str) -> str:
        value = relative_path.strip().replace("\\", "/")
        if value.startswith("/") or value.startswith("//") or re.match(r"^[A-Za-z]:/", value):
            raise ValueError("请输入授权根目录内的相对路径，不要输入绝对路径")
        if any(part == ".." for part in value.split("/")):
            raise ValueError("路径不能包含上级目录跳转")
        if not self.allow_hidden_paths and any(
            part.startswith(".") and part not in {"", "."} for part in value.split("/")
        ):
            raise ValueError("出于安全考虑，当前不允许访问隐藏目录")
        return value.strip("/")

    @staticmethod
    def _breadcrumbs(root: DataRoot, normalized: str) -> list[PathBreadcrumb]:
        breadcrumbs = [PathBreadcrumb(label=root.label, relative_path="")]
        parts = [part for part in normalized.split("/") if part]
        for index, part in enumerate(parts):
            breadcrumbs.append(
                PathBreadcrumb(label=part, relative_path="/".join(parts[: index + 1]))
            )
        return breadcrumbs

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
