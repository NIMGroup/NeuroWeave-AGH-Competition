import type {
  InteractionMode,
  StoredConversationMessage
} from '../conversations/conversationTypes';
import type { Workflow } from '../workflows/workflowTypes';
import type { WorkspaceEvent } from '../workspace/workspaceTypes';
import type { TokenUsageSummary } from './TokenUsage';

export type CapabilityRoutingLevel = {
  level: number;
  parent_ids: string[];
  candidate_count: number;
  payload_chars: number;
  selected_node_ids: string[];
  source: string;
};

export type CapabilityRoutingTrace = {
  strategy: 'hierarchical' | 'heuristic' | 'semantic';
  levels: CapabilityRoutingLevel[];
  candidates_seen: number;
  payload_chars: number;
  full_tree_payload_chars: number;
  payload_ratio: number;
  selected_paths: string[][];
  intent_source: string;
  normalized_task: string;
  semantic_hints: string[];
  reused_input_capabilities?: string[];
  confidence: number;
};

export type CapabilityRecommendationItem = {
  capability_id: string;
  label: string;
  description: string;
  execution_level: 'advisory' | 'planning' | 'structured';
  skill_ids: string[];
  tree_path: string[];
};

export type CapabilityRecommendation = {
  title: string;
  summary: string;
  plan_kind: 'atomic' | 'composite';
  fully_executable: boolean;
  items: CapabilityRecommendationItem[];
  notice: string;
};

export type ChatMessage = {
  tokenUsage?: TokenUsageSummary;
  clientTurnId?: string;
  requestError?: string;
  planningOffer?:string;
  persistedAfterRead?: number;
  id: string;
  role: 'user' | 'assistant';
  content: string;
  createdAt: string;
  sequence?: number;
  skills?: string[];
  interactionMode?: InteractionMode;
  workflowRecommended?: boolean;
  clarificationRequired?: boolean;
  workflowId?: string;
  workflow?: Workflow | null;
  capabilityRouting?: CapabilityRoutingTrace;
  capabilityRecommendation?: CapabilityRecommendation;
  workspaceEvents?: WorkspaceEvent[];
};

function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string')
    : [];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function toChatMessage(record: StoredConversationMessage, turnUsage: Record<string, TokenUsageSummary> = {}): ChatMessage {
  const metadata = isRecord(record.metadata) ? record.metadata : {};
  const mode = metadata['interaction_mode'];
  const workflowId = metadata['workflow_id'];
  return {
    tokenUsage: typeof metadata['client_turn_id'] === 'string' ? turnUsage[metadata['client_turn_id']] : undefined,
    clientTurnId: typeof metadata['client_turn_id'] === 'string' ? metadata['client_turn_id'] : undefined,
    planningOffer:typeof metadata['planning_offer']==='string'?metadata['planning_offer']:undefined,
    id: `stored-${record.message_id}`,
    role: record.role,
    content: record.content,
    createdAt: record.created_at,
    sequence: record.sequence,
    skills: record.role === 'user' ? stringArray(metadata['selected_skills']) : undefined,
    interactionMode: mode === 'task' || mode === 'chat' ? mode : undefined,
    workflowRecommended:
      typeof metadata['workflow_recommended'] === 'boolean'
        ? (metadata['workflow_recommended'] as boolean)
        : undefined,
    clarificationRequired:
      typeof metadata['clarification_required'] === 'boolean'
        ? (metadata['clarification_required'] as boolean)
        : undefined,
    workflowId: typeof workflowId === 'string' ? workflowId : undefined,
    capabilityRouting: isRecord(metadata['capability_routing'])
      ? (metadata['capability_routing'] as CapabilityRoutingTrace)
      : undefined,
    capabilityRecommendation: isRecord(metadata['capability_recommendation'])
      ? (metadata['capability_recommendation'] as CapabilityRecommendation)
      : undefined,
    workspaceEvents: Array.isArray(metadata['workspace_tool_events'])
      ? (metadata['workspace_tool_events'] as WorkspaceEvent[])
      : undefined
  };
}

export function toChatMessages(records: StoredConversationMessage[], turnUsage: Record<string, TokenUsageSummary> = {}): ChatMessage[] {
  return records.map(record => toChatMessage(record, turnUsage));
}

/** A live refresh of the newest page must retain older pages the user loaded. */
export function refreshNewestMessages(current: ChatMessage[], newest: ChatMessage[]): ChatMessage[] {
  const first = newest.find(message => message.sequence !== undefined)?.sequence;
  if (first === undefined) return newest;
  const older = current.filter(message => message.sequence !== undefined && message.sequence < first);
  return prependUniqueMessages(newest, older);
}

export function prependUniqueMessages(current: ChatMessage[], older: ChatMessage[]): ChatMessage[] {
  if (older.length === 0) return current;
  const seen = new Set(current.map((message) => message.id));
  const fresh = older.filter((message) => !seen.has(message.id));
  if (fresh.length === 0) return current;
  return [...fresh, ...current];
}
