import type { AssistantTaskState } from './AgentTaskStatus';

export function goalOutcome(task?: AssistantTaskState | null): 'ended' | 'completed' | null {
  return task?.user_closure?.outcome ?? (task?.user_completion ? 'completed' : null);
}
