import { queryOptions } from '@tanstack/react-query';
import type { QueryClient } from '@tanstack/react-query';
import * as api from './libraryApi';

/** Palette, canvas and details share one cancellable method-contract request. */
export const methodQuery = (id: string, language: string) => queryOptions({
  queryKey: ['library-method', id, language],
  queryFn: ({ signal }) => api.method(id, signal),
  staleTime: 60_000,
  retry: 1
});

/** Publication state can change without changing its immutable version payload. */
export function refreshPipelineViews(client: QueryClient, id: string) {
  return Promise.all([
    ['library'], ['pipeline-trash'], ['pipeline-detail', id], ['pipeline-versions', id],
    ['pipeline-source', id], ['pipeline-migration', id]
  ].map(queryKey => client.invalidateQueries({ queryKey })));
}
