import { request } from './http';

/**
 * Backend health probe. Used only as a lightweight connection indicator in
 * the application shell; there is no dedicated health page.
 */
export type Health = {
  status: string;
  mode: 'demo' | 'model';
  model: string;
  prompt_version?: string;
  execution?: {
    enabled: boolean;
    runner_count: number;
    ready_runner_count?: number;
  };
};

export function fetchHealth(signal?: AbortSignal): Promise<Health> {
  return request<Health>('/api/health?include_runtime=false', { signal });
}
