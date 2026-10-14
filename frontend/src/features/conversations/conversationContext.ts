import type { ConversationSnapshot } from './conversationTypes';
import type { ChatMessage } from '../chat/chatMessages';

// Read generations reconcile optimistic turns without matching message text.
// A response from a GET started before POST completion cannot retire that turn.
let readGeneration = 0;
export const nextConversationRead = () => ++readGeneration;
export const currentConversationRead = () => readGeneration;

export function restoreConversationContext(snapshot: ConversationSnapshot): Record<string, unknown> {
  const context = { ...snapshot.context };
  for (const field of ['interaction_mode', 'selected_skills', 'dataset_id', 'workflow_id']) {
    if (Object.hasOwn(context, field)) continue;
    const record = [...snapshot.messages].reverse().find(message =>
      (field !== 'selected_skills' || message.role === 'user') &&
      Object.hasOwn(message.metadata ?? {}, field) &&
      (field !== 'workflow_id' || typeof message.metadata[field] === 'string')
    );
    if (record) context[field] = record.metadata[field];
  }
  return context;
}

export function mergeConversationMessages(
  stored: ChatMessage[], transient: ChatMessage[], readGeneration: number,
): ChatMessage[] {
  const ids = new Set(stored.map(message => message.id));
  const turns = new Set(stored.filter(message => message.clientTurnId)
    .map(message => `${message.role}:${message.clientTurnId}`));
  return [...stored, ...transient.filter(message =>
    !ids.has(message.id) &&
    (!message.clientTurnId || !turns.has(`${message.role}:${message.clientTurnId}`)) &&
    (message.persistedAfterRead === undefined || readGeneration <= message.persistedAfterRead)
  )];
}
