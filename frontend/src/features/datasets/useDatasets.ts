import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { QueryClient } from '@tanstack/react-query';
import { cancelOperation, runOperation } from '../../api/operations';
import type { OperationView } from '../../api/operations';
import {
  fetchDataset,
  fetchDatasetRoots,
  fetchDatasets,
  fetchDirectoryListing
} from './datasetsApi';
import type { Dataset } from './datasetsApi';

function invalidateDatasets(queryClient: QueryClient) {
  void queryClient.invalidateQueries({ queryKey: ['datasets'] });
}

export function useSelectedDataset(datasetId: string | null) {
  return useQuery({
    queryKey: ['datasets', 'detail', datasetId],
    enabled: datasetId !== null,
    queryFn: ({ signal }) => {
      if (!datasetId) throw new Error('No dataset selected');
      return fetchDataset(datasetId, signal);
    },
    staleTime: 30_000,
    retry: 1
  });
}

export function useDatasetRoots() {
  return useQuery({
    queryKey: ['datasets', 'roots'],
    queryFn: ({ signal }) => fetchDatasetRoots(signal),
    staleTime: 60_000,
    retry: 1
  });
}

export function useRegisteredDatasets() {
  return useQuery({
    queryKey: ['datasets', 'list'],
    queryFn: ({ signal }) => fetchDatasets(signal),
    staleTime: 15_000,
    retry: 1
  });
}

export function useDirectoryListing(rootId: string, relativePath: string) {
  return useQuery({
    queryKey: ['datasets', 'directories', rootId, relativePath],
    enabled: rootId !== '',
    queryFn: ({ signal }) => fetchDirectoryListing(rootId, relativePath, signal),
    staleTime: 10_000,
    retry: 1
  });
}

export type DatasetJobHandlers = {
  onProgress?: (operation: OperationView) => void;
  signal?: AbortSignal;
};
export function useRegisterDataset() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (
      input: {
        rootId: string;
        relativePath: string;
        displayName: string;
      } & DatasetJobHandlers
    ) =>
      runOperation<Dataset>(
        '/api/datasets/register-jobs',
        {
          method: 'POST',
          body: JSON.stringify({
            root_id: input.rootId,
            relative_path: input.relativePath,
            display_name: input.displayName
          })
        },
        { onProgress: input.onProgress, signal: input.signal }
      ),
    onSuccess: () => {
      invalidateDatasets(queryClient);
    }
  });
}

export function useInspectDataset() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: { datasetId: string } & DatasetJobHandlers) =>
      runOperation<Dataset>(
        `/api/datasets/${encodeURIComponent(input.datasetId)}/inspect-jobs`,
        { method: 'POST' },
        { onProgress: input.onProgress, signal: input.signal }
      ),
    onSuccess: () => {
      invalidateDatasets(queryClient);
    }
  });
}

export { cancelOperation };
