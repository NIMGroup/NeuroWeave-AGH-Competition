import { ApiError, request } from './http';

/**
 * Reusable helper for backend long-running operations.
 *
 * Flow: `POST ...-jobs` returns `{ operation_id, status, message }`, then the
 * operation is polled via `GET /api/operations/{id}` until it reaches a
 * terminal state (`succeeded` / `failed` / `cancelled`).
 */
export type OperationView = {
  operation_id: string;
  status: string;
  message: string;
};

type OperationPayload = OperationView & {
  result?: unknown;
  error?: { current?: unknown; message?: string };
};

const TERMINAL_STATUSES = new Set(['succeeded', 'failed', 'cancelled']);
const POLL_INTERVAL_MS = 750;
const MAX_POLL_FAILURES = 5;

export async function cancelOperation(operationId: string): Promise<void> {
  await request(`/api/operations/${encodeURIComponent(operationId)}/cancel`, {
    method: 'POST'
  });
}

export async function runOperation<T>(
  path: string,
  init: RequestInit,
  options: {
    signal?: AbortSignal;
    onProgress?: (operation: OperationView) => void;
  } = {}
): Promise<T> {
  const started = await request<OperationPayload>(path, init);
  if (!started.operation_id || !started.status) {
    throw new Error('Backend returned an invalid operation record');
  }
  let operation = started;
  let failures = 0;
  for (;;) {
    options.onProgress?.({
      operation_id: operation.operation_id,
      status: operation.status,
      message: operation.message
    });
    if (operation.status === 'succeeded') {
      return operation.result as T;
    }
    if (TERMINAL_STATUSES.has(operation.status)) {
      const message =
        typeof operation.error?.message === 'string' && operation.error.message
          ? operation.error.message
          : operation.message;
      if (operation.status === 'cancelled') {
        throw new DOMException(message || 'Operation cancelled', 'AbortError');
      }
      if (operation.error?.current) {
        throw new ApiError(409, message || 'Workflow changed', 'revision_conflict', operation.error.current);
      }
      throw new Error(message || 'Operation failed');
    }
    if (options.signal?.aborted) {
      throw new DOMException('Aborted', 'AbortError');
    }
    await new Promise<void>((resolve, reject) => {
      const stopped = () => {
        clearTimeout(timer);
        reject(new DOMException('Aborted', 'AbortError'));
      };
      const timer = setTimeout(() => {
        options.signal?.removeEventListener('abort', stopped);
        resolve();
      }, POLL_INTERVAL_MS);
      options.signal?.addEventListener('abort', stopped, { once: true });
    });
    try {
      operation = await request<OperationPayload>(
        `/api/operations/${encodeURIComponent(operation.operation_id)}`,
        { signal: options.signal }
      );
      failures = 0;
    } catch (error) {
      if (options.signal?.aborted) {
        throw error;
      }
      failures += 1;
      if (failures >= MAX_POLL_FAILURES) {
        throw error;
      }
      options.onProgress?.({
        ...operation,
        message: 'Connection interrupted, retrying operation status…'
      });
    }
  }
}
