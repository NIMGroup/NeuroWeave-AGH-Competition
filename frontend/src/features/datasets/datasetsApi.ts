import { request } from '../../api/http';

export type DataRoot = {
  root_id: string;
  label: string;
  path: string;
  readable: boolean;
  writable: boolean;
};

export type PathBreadcrumb = {
  label: string;
  relative_path: string;
};

export type DirectoryEntry = {
  name: string;
  relative_path: string;
  readable: boolean;
  is_symlink: boolean;
};

export type FileEntry = {
  name: string;
  relative_path: string;
  suffix: string;
  size_bytes: number;
  readable: boolean;
  is_symlink: boolean;
};

export type DirectoryListing = {
  root: DataRoot;
  relative_path: string;
  parent_relative_path: string | null;
  breadcrumbs: PathBreadcrumb[];
  directories: DirectoryEntry[];
  files: FileEntry[];
  directory_count: number;
  file_count: number;
  directories_truncated: boolean;
  files_truncated: boolean;
};

export type SamplePathMatch = {
  sample_id: string;
  label: string;
  relative_path: string;
  absolute_path: string;
  status: 'matched' | 'missing' | 'invalid' | 'pending';
  message: string;
};

export type SamplePathCoverage = {
  total: number;
  matched: number;
  missing: number;
  invalid: number;
};

export type DatasetPathPreview = {
  validation?: 'path_preview' | 'pending_dry_run';
  dataset_id: string;
  binding_mode: 'single' | 'per_sample' | 'dataset';
  selected_sample_id: string | null;
  selected_relative_path: string;
  path_template: string;
  variables: string[];
  coverage: SamplePathCoverage;
  matches: SamplePathMatch[];
  can_apply: boolean;
  messages: string[];
};

export type DatasetInspection = {
  file_count: number;
  total_bytes: number;
  truncated: boolean;
  is_bids: boolean;
  has_dwi: boolean;
  has_bval: boolean;
  has_bvec: boolean;
  has_t1w: boolean;
  has_preprocessed_dwi: boolean;
  has_fod: boolean;
  has_five_tt: boolean;
  has_reverse_phase: boolean;
  shell_count: number | null;
  input_types: string[];
  features: Record<string, boolean>;
  sample_layout: 'bids' | 'directory' | 'single';
  sample_count: number;
  warnings: string[];
  scanned_at: string;
};

export type Dataset = {
  dataset_id: string;
  root_id: string;
  relative_path: string;
  display_name: string;
  absolute_path: string;
  inspection: DatasetInspection;
  created_at: string;
  updated_at: string;
};

export function fetchDatasetRoots(signal?: AbortSignal): Promise<{ roots: DataRoot[] }> {
  return request<{ roots: DataRoot[] }>('/api/datasets/roots', {
    signal
  });
}

export function fetchDatasets(signal?: AbortSignal): Promise<{ datasets: Dataset[] }> {
  return request<{ datasets: Dataset[] }>('/api/datasets', {
    signal
  });
}

export function fetchDataset(datasetId: string, signal?: AbortSignal): Promise<Dataset> {
  return request<Dataset>(`/api/datasets/${encodeURIComponent(datasetId)}`, { signal });
}

export function linkDatasetWorkspace(datasetId: string, workspaceId: string | null): Promise<void> {
  return request<void>(`/api/datasets/${encodeURIComponent(datasetId)}/workspace`, {
    method: 'PATCH',
    body: JSON.stringify({ workspace_id: workspaceId })
  });
}

export function fetchDirectoryListing(
  rootId: string,
  relativePath: string,
  signal?: AbortSignal
): Promise<DirectoryListing> {
  const query = new URLSearchParams({ root_id: rootId });
  if (relativePath) query.set('relative_path', relativePath);
  return request<DirectoryListing>(`/api/datasets/directories?${query}`, { signal });
}
