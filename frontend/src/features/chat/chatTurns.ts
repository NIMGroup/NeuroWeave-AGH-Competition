import { ApiError, request } from '../../api/http';
import { useLanguageStore } from '../../stores/languageStore';
import { canReconnect, createTurnReader, TurnRecordMissingError } from './turnRecovery';
import type { ChatRequest, ChatResponse } from './chatApi';
import type { TokenUsageSummary } from './TokenUsage';
import type { TurnOrigin } from './turnTracking';
export type Turn = {
  token_usage?: TokenUsageSummary;
  turn_id: string;
  conversation_id: string;
  status: string;
  request_kind?: 'user_message' | 'result_verification';
  updated_at?: string;
  cancel_requested?: boolean;
  message: string;
  result: ChatResponse | null;
  error: { message: string } | null;
};
const key = 'neuroweave:pending-chat-turns';
const registrationKey = key + ':registration';
let memoryPending: string[] = [];
let memoryOnly = false;
const originKey = key + ':origins';
const origins = new Map<string, TurnOrigin>();
export function turnOrigin(id: string): TurnOrigin | undefined {
  if (origins.has(id)) return origins.get(id);
  try {
    const value = JSON.parse(localStorage.getItem(originKey) ?? '{}')[id];
    if (value && typeof value.scope === 'string' && typeof value.title === 'string' &&
      (value.conversationId === null || typeof value.conversationId === 'string')) return value;
  } catch { /* Still tracked by its server ID. */ }
}
export function registerTurnOrigin(id: string, origin: TurnOrigin) {
  origins.set(id, origin);
  try {
    const stored = JSON.parse(localStorage.getItem(originKey) ?? '{}');
    localStorage.setItem(originKey, JSON.stringify({ ...stored, [id]: origin }));
  } catch { /* In-memory origin is enough while this page remains open. */ }
}
type SubmissionOutcome = { error?: unknown };
const submissions = new Map<string, {
  settled: Promise<SubmissionOutcome>;
  finish: (outcome: SubmissionOutcome) => void;
}>();
const registrationDeadlines = new Map<string, number>();
function storedRegistrations(): Record<string, number> {
  try {
    const stored = JSON.parse(localStorage.getItem(registrationKey) ?? '{}');
    if (!stored || typeof stored !== 'object' || Array.isArray(stored)) return {};
    return Object.fromEntries(Object.entries(stored).filter((entry): entry is [string, number] =>
      typeof entry[1] === 'number' && Number.isFinite(entry[1])));
  } catch { return {}; }
}
function registrationDeadline(id: string): number {
  return registrationDeadlines.get(id) ?? storedRegistrations()[id] ?? 0;
}
function rememberRegistration(id: string, deadline?: number) {
  if (deadline === undefined) registrationDeadlines.delete(id);
  else registrationDeadlines.set(id, deadline);
  try {
    const stored = storedRegistrations();
    if (deadline === undefined) delete stored[id];
    else stored[id] = deadline;
    localStorage.setItem(registrationKey, JSON.stringify(stored));
  } catch { /* In-memory registration still protects this page's readers. */ }
}
function beginSubmission(id: string) {
  let finish!: (outcome: SubmissionOutcome) => void;
  const settled = new Promise<SubmissionOutcome>(resolve => { finish = resolve; });
  submissions.set(id, { settled, finish });
  rememberRegistration(id, Date.now() + 60_000);
}
function finishSubmission(id: string, outcome: SubmissionOutcome = {}) {
  submissions.get(id)?.finish(outcome);
  submissions.delete(id);
}
export function newTurnId(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID() : `turn-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
export function pendingTurns(): string[] {
  if (memoryOnly) return memoryPending;
  try {
    const value = JSON.parse(localStorage.getItem(key) ?? '[]');
    memoryPending = Array.isArray(value) ? value.filter((id): id is string => typeof id === 'string') : [];
    return memoryPending;
  } catch {
    return memoryPending;
  }
}
const remember = (id: string, keep: boolean) => {
  if (!keep) {
    rememberRegistration(id);
    origins.delete(id);
    try {
      const stored = JSON.parse(localStorage.getItem(originKey) ?? '{}');
      delete stored[id]; localStorage.setItem(originKey, JSON.stringify(stored));
    } catch { /* Recovery by ID remains available on the server. */ }
  }
  memoryPending = [...new Set([...pendingTurns().filter(v => v !== id), ...(keep ? [id] : [])])];
  try {
    localStorage.setItem(key, JSON.stringify(memoryPending));
  } catch {
    memoryOnly = true; // Restricted storage must not drop observation or release the send lock.
  }
  window.dispatchEvent(new CustomEvent('neuroweave:chat-progress'));
};
function progress(turn: Turn) {
  const origin = turnOrigin(turn.turn_id);
  if (origin && origin.conversationId !== turn.conversation_id)
    registerTurnOrigin(turn.turn_id, { ...origin, conversationId: turn.conversation_id });
  window.dispatchEvent(
    new CustomEvent('neuroweave:chat-progress', { detail: turn })
  );
}

export function adoptActiveTurn(turn: Turn) {
  if (!['queued', 'running'].includes(turn.status)) return;
  if (!turnOrigin(turn.turn_id)) registerTurnOrigin(turn.turn_id, {
    scope: turn.conversation_id, conversationId: turn.conversation_id, title: ''
  });
  if (!pendingTurns().includes(turn.turn_id)) remember(turn.turn_id, true);
  progress(turn);
}

export function readActiveTurns() {
  return request<{ turns: Turn[] }>('/api/chat/turns', { signal: AbortSignal.timeout(15_000) });
}
const connectionMessage = () => useLanguageStore.getState().language.startsWith('zh')
  ? '连接暂时中断，尚未确认后台请求失败。原请求 ID 已保留，可通过请求状态栏继续查看。'
  : 'Connection interrupted; server failure is unconfirmed. The original request ID is retained for recovery.';
const missingMessage = () => useLanguageStore.getState().language.startsWith('zh')
  ? '暂未查到这条请求记录，尚不能确认消息是否已接收。原请求 ID 已保留，可恢复状态或查看会话记录。'
  : 'This request record is not available yet; acceptance is unconfirmed. Its original ID is retained. Recover its status or check the conversation history.';
export const readTurn = createTurnReader<Turn>(async id => {
  // Persisting an ID makes the panel observe it immediately. Wait for the POST
  // before querying, so a pre-registration 404 cannot poison either reader.
  const submission = submissions.get(id);
  if (submission) {
    const outcome = await submission.settled;
    if ('error' in outcome) throw outcome.error;
  }
  try {
    const turn = await request<Turn>(`/api/chat/turns/${encodeURIComponent(id)}`,
      { signal: AbortSignal.timeout(15_000) });
    rememberRegistration(id);
    return turn;
  } catch (error) {
    if (error instanceof ApiError && error.status === 404)
      throw new TurnRecordMissingError(missingMessage());
    throw error;
  }
}, {
  // A lost acknowledgement or a page reload can precede the server commit.
  // Only newly submitted IDs receive this bounded grace period.
  retryWhen: (error, id) => canReconnect(error) ||
    (error instanceof TurnRecordMissingError && Date.now() < registrationDeadline(id)),
  connection: (id, reconnecting) => window.dispatchEvent(new CustomEvent('neuroweave:chat-connection',
    { detail: { id, reconnecting } })),
  message: connectionMessage
});
export async function waitForTurn(id: string): Promise<ChatResponse> {
  for (;;) {
    const turn = await readTurn(id);
    progress(turn);
    if (turn.status === 'succeeded' && turn.result) {
      remember(id, false);
      return turn.result;
    }
    if (!['queued', 'running'].includes(turn.status))
      throw new Error(turn.error?.message ?? turn.message);
    await new Promise((resolve) => setTimeout(resolve, 900));
  }
}
export async function submitTurn(payload: ChatRequest): Promise<ChatResponse> {
  // Record before POST: a lost acknowledgement can still be recovered by ID.
  beginSubmission(payload.client_turn_id);
  remember(payload.client_turn_id, true);
  let turn: Turn;
  try {
    turn = await request<Turn>('/api/chat/turns', {
      method: 'POST', body: JSON.stringify(payload), signal: AbortSignal.timeout(15_000)
    });
  } catch (error) {
    if (!canReconnect(error) || (error instanceof ApiError && error.status === 429)) {
      finishSubmission(payload.client_turn_id, { error });
      if (error instanceof ApiError && error.status === 409)
        void readActiveTurns().then(value => value.turns.forEach(adoptActiveTurn)).catch(() => {});
      remember(payload.client_turn_id, false); throw error;
    }
    // The POST may already have been accepted. Recover its ID without another POST.
    finishSubmission(payload.client_turn_id);
    return waitForTurn(payload.client_turn_id);
  }
  finishSubmission(payload.client_turn_id);
  progress(turn);
  return waitForTurn(turn.turn_id);
}
export async function retryTurn(id: string) {
  const turn = await readTurn(id);
  if (['failed', 'interrupted', 'cancelled'].includes(turn.status)) {
    try {
      await request(`/api/chat/turns/${encodeURIComponent(id)}/retry`, { method: 'POST', signal: AbortSignal.timeout(15_000) });
    } catch (error) { if (!canReconnect(error)) throw error; }
  }
  return waitForTurn(id);
}
export async function forgetTurn(id: string) {
  remember(id, false);
}
