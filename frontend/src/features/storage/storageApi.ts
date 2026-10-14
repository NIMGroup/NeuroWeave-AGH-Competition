import { request } from '../../api/http';
import { runOperation } from '../../api/operations';
import type { OperationView } from '../../api/operations';

export type StorageVolume = {
  path: string;
  free_bytes: number;
  total_bytes: number;
  low_space: boolean;
};

export type StorageStatus = {
  backend_revision?: string;
  database_bytes: number;
  backup_count: number;
  retention: string;
  latest_backup: { created_at?: string; restore_verified?: boolean; error?: string } | null;
  volumes: StorageVolume[];
};

export type StorageInventory = {
  totals: { files: number; bytes: number; unreadable: number; symlinks_skipped: number };
  run_count: number;
  largest_runs: { run_id: string; status: string; bytes: number; files: number }[];
};

export function fetchStorageStatus(): Promise<StorageStatus> {
  return request<StorageStatus>('/api/maintenance/storage');
}

export function inspectStorage(
  handlers: { onProgress?: (operation: OperationView) => void } = {}
): Promise<StorageInventory> {
  return runOperation<StorageInventory>(
    '/api/maintenance/inventory-jobs',
    { method: 'POST' },
    handlers
  );
}
