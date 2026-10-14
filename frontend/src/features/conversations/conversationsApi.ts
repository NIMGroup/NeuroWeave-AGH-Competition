import { request, ApiError } from '../../api/http';
import type { ConversationPage, ConversationSnapshot } from './conversationTypes';

export type ListConversationsParams = {
  archived: boolean;
  query: string;
  cursor?: string | null;
  signal?: AbortSignal;
};

export function fetchConversations(params: ListConversationsParams): Promise<ConversationPage> {
  const search = new URLSearchParams({ limit: '20' });
  search.set('archived', String(params.archived));
  const trimmed = params.query.trim();
  if (trimmed) search.set('q', trimmed);
  if (params.cursor) search.set('cursor', params.cursor);
  return request<ConversationPage>(`/api/conversations?${search}`, {
    signal: params.signal
  });
}

export function fetchConversationSnapshot(
  conversationId: string,
  options: {
    limit?: number;
    beforeSequence?: number | null;
    signal?: AbortSignal;
  } = {}
): Promise<ConversationSnapshot> {
  const search = new URLSearchParams({
    limit: String(options.limit ?? 40)
  });
  if (options.beforeSequence != null) {
    search.set('before_sequence', String(options.beforeSequence));
  }
  return request<ConversationSnapshot>(
    `/api/conversations/${encodeURIComponent(conversationId)}?${search}`,
    { signal: options.signal }
  );
}

export function patchConversation(
  conversationId: string,
  update: { title?: string; archived?: boolean; workspace_id?: string | null }
): Promise<{ conversation_id: string; title?: string; archived?: boolean }> {
  return request(`/api/conversations/${encodeURIComponent(conversationId)}`, {
    method: 'PATCH',
    body: JSON.stringify(update)
  });
}

export async function removeConversation(conversationId: string): Promise<void> {
  try {
    await request<void>(`/api/conversations/${encodeURIComponent(conversationId)}`, {
      method: 'DELETE'
    });
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return;
    throw error;
  }
}
