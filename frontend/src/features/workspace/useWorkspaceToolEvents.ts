import { useQuery } from '@tanstack/react-query';
import { fetchWorkspaceToolEvents } from './workspaceApi';

export function useWorkspaceToolEvents(activeTurn: string | null) {
  const query = useQuery({
    queryKey: ['workspace-tool-events', activeTurn],
    queryFn: ({ signal }) => fetchWorkspaceToolEvents(activeTurn!, signal),
    enabled: !!activeTurn,
    refetchInterval: activeTurn ? 900 : false
  });
  return { events: activeTurn ? (query.data ?? []) : [], error: query.error };
}
