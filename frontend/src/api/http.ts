/**
 * Central HTTP client for the new frontend.
 *
 * All backend traffic goes through `request()`. Feature code must not call
 * raw `fetch()` directly. The backend is a FastAPI server whose error shape is
 * `{ detail: string }` or `{ detail: { code, message, current } }`.
 */

import { useLanguageStore } from '../stores/languageStore';

export type ApiErrorDetail = {
  code?: string;
  message?: string;
  current?: unknown;
};

export class ApiError extends Error {
  status: number;
  code?: string;
  current?: unknown;

  constructor(status: number, message: string, code?: string, current?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.current = current;
  }
}

export type RequestTransport = (path: string, init: RequestInit) => Promise<{ status: number; body: unknown }>;
let requestTransport: RequestTransport | null = null;
/** AGH plugins use the host's authenticated service relay, not a second socket. */
export function setRequestTransport(transport: RequestTransport | null) {
  requestTransport = transport;
}

export async function downloadFile(path: string) {
  let url = apiBase() + path;
  if (requestTransport) {
    const response = await requestTransport(path, {});
    if (response.status >= 400) throw new ApiError(response.status, '下载文件失败');
    url = (response.body as { download_url: string }).download_url;
    if (!url) throw new Error('下载链接不可用');
  }
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = '';
  anchor.click();
}

/**
 * Backend base URL. `VITE_LOCALFLOW_API_URL` wins when set; otherwise the
 * production build uses same-origin and development uses the Vite proxy
 * (see vite.config.ts) so local API calls do not hit CORS.
 */
export function apiBase(): string {
  const configured = import.meta.env.VITE_LOCALFLOW_API_URL as string | undefined;
  if (configured && configured.trim()) {
    return configured.trim().replace(/\/+$/, '');
  }
  if (import.meta.env.DEV) {
    return '';
  }
  return '';
}

async function parseError(response: Response): Promise<ApiError> {
  const fallback = `Request failed (${response.status})`;
  try {
    const payload = (await response.json()) as { detail?: unknown };
    if (typeof payload.detail === 'string' && payload.detail) {
      return new ApiError(response.status, payload.detail);
    }
    // FastAPI reports field validation as an array. Keep the field location
    // visible instead of hiding a fixable configuration error behind “422”.
    if (Array.isArray(payload.detail)) {
      const messages = payload.detail.flatMap((entry: { loc?: unknown[]; msg?: unknown }) =>
        typeof entry?.msg === 'string' ? [`${entry.loc?.join('.') ?? ''}: ${entry.msg}`] : []);
      return new ApiError(response.status, messages.join('; ') || fallback);
    }
    if (payload.detail && typeof payload.detail === 'object') {
      const detail = payload.detail as ApiErrorDetail;
      if (typeof detail.message === 'string' && detail.message) {
        return new ApiError(response.status, detail.message, detail.code, detail.current);
      }
    }
    return new ApiError(response.status, fallback);
  } catch {
    return new ApiError(response.status, fallback);
  }
}

/**
 * Readable one-line text for a caught value.
 *
 * `request()` rejects with an `ApiError` whose message is already the server's
 * `detail` string, so the normal path returns that message unchanged. Anything
 * else is handled defensively so the UI never renders `[object Object]` or a
 * stack trace; `fallback` is returned when there is no usable text.
 */
export function errorText(error: unknown, fallback = ''): string {
  if (error instanceof Error && error.message) return error.message;
  if (typeof error === 'string' && error.trim()) return error;
  if (error && typeof error === 'object') {
    // Structured API rejections may carry `message` without extending Error.
    const message = (error as { message?: unknown }).message;
    if (typeof message === 'string' && message.trim()) return message;
  }
  return fallback;
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  if (requestTransport) {
    if (init.signal?.aborted) throw new DOMException('Aborted', 'AbortError');
    const response = await requestTransport(path, init);
    if (init.signal?.aborted) throw new DOMException('Aborted', 'AbortError');
    if (response.status >= 400) {
      const payload = response.body as { detail?: string | ApiErrorDetail };
      const detail = payload?.detail;
      throw new ApiError(response.status, typeof detail === 'string' ? detail : detail?.message ?? `Request failed (${response.status})`,
        typeof detail === 'object' ? detail.code : undefined, typeof detail === 'object' ? detail.current : undefined);
    }
    return response.body as T;
  }
  const response = await fetch(`${apiBase()}${path}`, {
    ...init,
    credentials: 'include',
    headers: {
      'Accept-Language': useLanguageStore.getState().language,
      'X-NeuroWeave-Contract': '2',
      ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      ...init.headers
    }
  });
  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new Event('neuroweave-auth-expired'));
    throw await parseError(response);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}
