import { useEffect, useRef, useState } from 'react';
import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { errorText, request } from '../../api/http';
import { adoptActiveTurn, forgetTurn, pendingTurns, readActiveTurns, readTurn, retryTurn, turnOrigin } from './chatTurns';
import type { Turn } from './chatTurns';
import type { TrackedTurn, TurnOrigin } from './turnTracking';
import { canReconnect, TurnConnectionError } from './turnRecovery';
import { useLanguageStore } from '../../stores/languageStore';

/** Mounted at the application root, independent of the visible page/conversation. */
export function useBackgroundChatTurns(onKnown: (turn: Turn, origin?: TurnOrigin) => void, onReady: (turn: Turn) => void) {
  const [ids, setIds] = useState(pendingTurns);
  const [reconnecting, setReconnecting] = useState<Set<string>>(() => new Set());
  const [actions, setActions] = useState<Record<string, 'retry' | 'cancel'>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const callbacks = useRef({ onKnown, onReady });
  const client = useQueryClient();
  const zh = useLanguageStore(s => s.language).startsWith('zh');
  useEffect(() => { callbacks.current = { onKnown, onReady }; });
  useEffect(() => {
    const refresh = (event: Event) => {
      const turn = (event as CustomEvent<Turn>).detail;
      if (turn?.turn_id) {
        client.setQueryData(['chat-turn', turn.turn_id], turn);
        callbacks.current.onKnown(turn, turnOrigin(turn.turn_id));
        if (!['queued', 'running'].includes(turn.status)) {
          // The sender/retry waiter may forget the ID in the same render batch.
          // Invalidate here as well, before removal can hide the terminal query.
          void client.invalidateQueries({ queryKey: ['conversation', turn.conversation_id] });
          void client.invalidateQueries({ queryKey: ['conversations'] });
          if (turn.status === 'succeeded') callbacks.current.onReady(turn);
        }
      }
      setIds(pendingTurns());
    };
    const connection = (event: Event) => {
      const state = (event as CustomEvent<{ id: string; reconnecting: boolean }>).detail;
      setReconnecting(previous => {
        const next = new Set(previous);
        if (state.reconnecting) next.add(state.id); else next.delete(state.id);
        return next;
      });
    };
    const storage = () => setIds(pendingTurns());
    window.addEventListener('neuroweave:chat-progress', refresh);
    window.addEventListener('neuroweave:chat-connection', connection);
    window.addEventListener('storage', storage);
    return () => {
      window.removeEventListener('neuroweave:chat-progress', refresh);
      window.removeEventListener('neuroweave:chat-connection', connection);
      window.removeEventListener('storage', storage);
    };
  }, [client]);
  const active = useQuery({
    queryKey: ['active-chat-turns'], queryFn: readActiveTurns,
    refetchInterval: 2000, refetchIntervalInBackground: true, retry: false
  });
  useEffect(() => {
    for (const turn of active.data?.turns ?? []) {
      // Discovery is a hint. A slower list response must not resurrect a turn
      // whose individual observer has already seen completion.
      const known = client.getQueryData<Turn>(['chat-turn', turn.turn_id]);
      if (known && !['queued', 'running'].includes(known.status)
        && (!turn.updated_at || !known.updated_at || turn.updated_at <= known.updated_at)) continue;
      if (known?.cancel_requested && !turn.cancel_requested) continue;
      if (!pendingTurns().includes(turn.turn_id)) adoptActiveTurn(turn);
    }
  }, [active.data, client]);
  const queries = useQueries({ queries: ids.map(id => ({
    queryKey: ['chat-turn', id],
    queryFn: () => readTurn(id),
    refetchInterval: (query: { state: { data?: Turn; error?: unknown } }) => {
      const { data, error } = query.state;
      if (error && !canReconnect(error) && !(error instanceof TurnConnectionError)) return false;
      return !data || ['queued', 'running'].includes(data.status) ? (error ? 5000 : 1000) : false;
    },
    refetchIntervalInBackground: true,
    retry: false
  })) });
  const records: TrackedTurn[] = queries.map((query, i) => ({ id: ids[i], origin: turnOrigin(ids[i]),
    turn: query.data, error: query.error ?? undefined, reconnecting: reconnecting.has(ids[i]) }));
  // Callback identity and changing token/progress text must not re-bind a draft.
  const observed = queries.map(q => q.data ? `${q.data.turn_id}:${q.data.conversation_id}:${q.data.status}` : '').join(',');
  const seen = useRef(new Set<string>());
  useEffect(() => {
    for (const item of observed.split(',').filter(Boolean)) {
      if (seen.current.has(item)) continue;
      seen.current.add(item);
      const id = item.split(':')[0];
      const turn = client.getQueryData<Turn>(['chat-turn', id]);
      if (!turn) continue;
      callbacks.current.onKnown(turn, turnOrigin(id));
      void client.invalidateQueries({ queryKey: ['conversations'] });
      if (!['queued', 'running'].includes(turn.status)) {
        void client.invalidateQueries({ queryKey: ['conversation', turn.conversation_id] });
        if (turn.status === 'succeeded') {
          callbacks.current.onReady(turn);
          void client.invalidateQueries({ queryKey: ['conversation', turn.conversation_id] }, { throwOnError: true })
            .then(() => forgetTurn(id)).catch(() => { seen.current.delete(item); });
        }
      }
    }
  }, [observed, client]);
  async function act(id: string, action: 'retry' | 'cancel') {
    if (actions[id] && !(action === 'cancel' && actions[id] === 'retry')) return;
    if (action === 'retry') for (const item of seen.current) if (item.startsWith(id + ':')) seen.current.delete(item);
    setActions(previous => ({ ...previous, [id]: action }));
    setErrors(previous => ({ ...previous, [id]: '' }));
    try {
      if (action === 'retry') await retryTurn(id);
      else {
        const turn = await request<Turn>(`/api/chat/turns/${encodeURIComponent(id)}/cancel`, { method: 'POST' });
        client.setQueryData(['chat-turn', id], turn);
        window.dispatchEvent(new CustomEvent('neuroweave:chat-progress', { detail: turn }));
      }
      await client.invalidateQueries({ queryKey: ['chat-turn', id] });
    } catch (error) {
      setErrors(previous => ({ ...previous, [id]: errorText(error, zh ? '请求处理失败' : 'Request failed') }));
    } finally {
      setActions(previous => {
        if (previous[id] !== action) return previous;
        const next = { ...previous }; delete next[id]; return next;
      });
    }
  }
  return { records, actions, errors, act,
    recover: (id: string) => client.refetchQueries({ queryKey: ['chat-turn', id] }),
    dismiss: (id: string) => { void forgetTurn(id); setIds(pendingTurns()); }
  };
}
