export type Workspace = {
  id: string;
  name: string;
  root_path: string;
  status: string;
  description: string;
  manageable: boolean;
  permission_reason: string;
};

export type WorkspaceOperation = {
  id: string;
  action: string;
  source: string;
  target: string | null;
  conversation_id: string | null;
  status: string;
  error?: string;
  can_cancel?: boolean;
  cancel_requested?: boolean;
  result?: Record<string, unknown>;
  staging_path?: string;
  request: { permanent?: boolean; overwrite?: boolean; root_path?: string };
};

export type WorkspaceEvent = {
  tool: string;
  agent?: 'coordinator' | 'data' | 'methods' | 'runtime';
  status: string;
  count?: number;
  error?: string;
  operation?: WorkspaceOperation;
  data_operation?: import('../data/dataTypes').DataOperation;
  arguments?: Record<string, unknown>;
  time?: string;
};

/** Persisted tool events from older servers may contain a query summary here. */
export function isWorkspaceOperation(value: unknown): value is WorkspaceOperation {
  if (!value || typeof value !== 'object') return false;
  const record = value as Partial<WorkspaceOperation>;
  return typeof record.id === 'string' && typeof record.status === 'string'
    && typeof record.source === 'string'
    && ['mkdir', 'copy', 'move', 'rename', 'delete', 'restore', 'read-sensitive'].includes(record.action ?? '')
    && Boolean(record.request && typeof record.request === 'object' && !Array.isArray(record.request));
}

export type FsEntry = {
  name: string;
  path: string;
  relative_path: string;
  type: string;
  size: number;
  modified_at: string;
  permissions: string;
  readable: boolean;
  writable: boolean;
};

export type FsListing = {
  root_path: string;
  path: string;
  relative_path: string;
  items: FsEntry[];
  has_more: boolean;
  next_cursor: string | null;
};

export type FsRoots = {
  roots: string[];
  default_root: string;
};

export type TextPreview = {
  text: string;
  truncated: boolean;
  next_offset: number;
};

export type TrashItem = {
  id: string;
  original_path: string;
  deleted_at: string;
  status: string;
};
