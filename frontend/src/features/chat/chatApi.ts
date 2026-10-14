import { submitTurn } from './chatTurns';
import type { CapabilityRecommendation, CapabilityRoutingTrace } from './chatMessages';
import type { InteractionMode } from '../conversations/conversationTypes';
import type { Workflow } from '../workflows/workflowTypes';
import type { WorkspaceEvent } from '../workspace/workspaceTypes';
import type { TokenUsageSummary } from './TokenUsage';
import type { AssistantTaskState } from './AgentTaskStatus';

export type ChatRequest = {
  message: string;
  task_action?: 'message' | 'update_plan';
  conversation_id: string | null;
  workspace_id: string | null;
  client_turn_id: string;
  interaction_mode: InteractionMode;
  selected_skills: string[];
  dataset_id: string | null;
  language: string;
};

export type ChatResponse = {
  assistant_task?: AssistantTaskState | null;
  token_usage?: TokenUsageSummary;
  planning_offer?:string|null;
  type?: 'done' | 'recommendation' | 'workflow';
  content?: string;
  detail?: string;
  mode?: 'demo' | 'model';
  model_used?: string;
  conversation_id?: string;
  workspace_id?: string | null;
  workspace_tool_events?: WorkspaceEvent[];
  selected_skills?: string[];
  auto_selected?: boolean;
  interaction_mode?: InteractionMode;
  workflow_recommended?: boolean;
  clarification_required?: boolean;
  capability_routing?: CapabilityRoutingTrace;
  capability_recommendation?: CapabilityRecommendation;
  workflow?: Workflow | null;
};

export function sendChat(payload: ChatRequest): Promise<ChatResponse> {
  return submitTurn(payload);
}
