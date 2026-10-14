import { useQuery } from '@tanstack/react-query';
import { fetchHealth } from '../../api/health';

export function useBackendStatus() {
  return useQuery({
    queryKey: ['health'],
    queryFn: ({ signal }) => fetchHealth(signal),
    staleTime: 30_000,
    retry: 1,
    refetchOnWindowFocus: false
  });
}
