from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DataRoot(BaseModel):
    root_id: str
    label: str
    path: str
    readable: bool = True
    writable: bool = False


class PathBreadcrumb(BaseModel):
    label: str
    relative_path: str


class DirectoryEntry(BaseModel):
    name: str
    relative_path: str
    readable: bool = True
    is_symlink: bool = False


class FileEntry(BaseModel):
    name: str
    relative_path: str
    suffix: str = ""
    size_bytes: int = 0
    readable: bool = True
    is_symlink: bool = False


class DirectoryListing(BaseModel):
    root: DataRoot
    relative_path: str
    parent_relative_path: str | None = None
    breadcrumbs: list[PathBreadcrumb] = Field(default_factory=list)
    directories: list[DirectoryEntry] = Field(default_factory=list)
    files: list[FileEntry] = Field(default_factory=list)
    directory_count: int = 0
    file_count: int = 0
    directories_truncated: bool = False
    files_truncated: bool = False


class DatasetSample(BaseModel):
    sample_id: str
    label: str
    relative_root: str
    entities: dict[str, str] = Field(default_factory=dict)


class SamplePathMatch(BaseModel):
    sample_id: str
    label: str
    relative_path: str
    absolute_path: str
    status: Literal["matched", "missing", "invalid", "pending"]
    message: str = ""


class SamplePathCoverage(BaseModel):
    total: int = 0
    matched: int = 0
    missing: int = 0
    invalid: int = 0


class DatasetPathPreview(BaseModel):
    validation: Literal['path_preview', 'pending_dry_run'] = 'path_preview'
    dataset_id: str
    binding_mode: Literal["single", "per_sample", "dataset"] = "single"
    selected_sample_id: str | None = None
    selected_relative_path: str
    path_template: str = ""
    variables: list[str] = Field(default_factory=list)
    coverage: SamplePathCoverage = Field(default_factory=SamplePathCoverage)
    matches: list[SamplePathMatch] = Field(default_factory=list)
    can_apply: bool = False
    messages: list[str] = Field(default_factory=list)


class DatasetInspection(BaseModel):
    file_count: int = 0
    total_bytes: int = 0
    truncated: bool = False
    is_bids: bool = False
    has_dwi: bool = False
    has_bval: bool = False
    has_bvec: bool = False
    has_t1w: bool = False
    has_preprocessed_dwi: bool = False
    has_fod: bool = False
    has_five_tt: bool = False
    has_reverse_phase: bool = False
    shell_count: int | None = None
    input_types: list[str] = Field(default_factory=lambda: ["dataset_folder"])
    features: dict[str, bool] = Field(default_factory=dict)
    feature_states: dict[str, Literal['found', 'missing', 'unknown']] = Field(default_factory=dict)
    scan_complete: bool | None = None
    skipped_symlinks: int = 0
    sequence_profiles: list[dict] = Field(default_factory=list)
    sequence_profiles_complete: bool = False
    sample_layout: Literal["bids", "directory", "single"] = "single"
    sample_count: int = 0
    samples: list[DatasetSample] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    scanned_at: str


class DatasetRecord(BaseModel):
    workspace_id: str | None = None
    dataset_id: str
    root_id: str
    relative_path: str
    display_name: str
    absolute_path: str
    inspection: DatasetInspection
    created_at: str
    updated_at: str


class DatasetRegisterRequest(BaseModel):
    root_id: str = Field(min_length=1, max_length=100)
    relative_path: str = Field(default="", max_length=4_000)
    display_name: str | None = Field(default=None, max_length=200)
