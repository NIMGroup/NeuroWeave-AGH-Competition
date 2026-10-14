import { useLanguageStore } from '../../stores/languageStore';
import { useQuery } from '@tanstack/react-query';
import { fetchSkills } from './skillsApi';

export function useSkills() {
  const language = useLanguageStore(s => s.language);
  return useQuery({
    queryKey: ['skills', language],
    queryFn: ({ signal }) => fetchSkills(signal),
    staleTime: 30_000,
    retry: 1
  });
}
