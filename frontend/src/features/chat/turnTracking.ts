import type { Turn } from './chatTurns';
import { canReconnect, TurnConnectionError, TurnRecordMissingError } from './turnRecovery';

export type TurnOrigin = { scope: string; conversationId: string | null; title: string };
export type TrackedTurn = {
  id: string;
  origin?: TurnOrigin;
  turn?: Turn;
  error?: unknown;
  reconnecting: boolean;
};

export function belongsToConversation(item: TrackedTurn, conversationId: string | null, draftId: string) {
  return conversationId ? (item.turn?.conversation_id ?? item.origin?.conversationId) === conversationId
    : item.origin?.scope === draftId && !item.turn?.conversation_id;
}

export function turnIsPending(item: TrackedTurn) {
  return !item.turn || ['queued', 'running'].includes(item.turn.status);
}

export function turnIsFailed(item: TrackedTurn) {
  return Boolean(item.error || (item.turn && ['failed', 'interrupted', 'cancelled'].includes(item.turn.status)));
}

/** Resolve once from the submission origin; current UI selection never owns a reply. */
export function responseConversation(origin: TurnOrigin, responseId?: string) {
  return responseId ?? origin.conversationId ?? origin.scope;
}

export function requestStatus(item: TrackedTurn, zh: boolean) {
  if (item.error instanceof TurnRecordMissingError) return zh ? '暂未查到请求记录' : 'Request record unavailable';
  if (item.error && !canReconnect(item.error) && !(item.error instanceof TurnConnectionError))
    return zh ? '无法读取请求状态' : 'Unable to read request status';
  if (item.error || item.reconnecting) return zh ? '正在恢复连接' : 'Reconnecting';
  if (item.turn?.cancel_requested && turnIsPending(item)) return zh ? '正在停止请求' : 'Stopping request';
  if (item.turn?.request_kind === 'result_verification' && turnIsPending(item))
    return zh ? '正在核查执行结果' : 'Verifying execution result';
  const labels: Record<string, string> = zh
    ? { queued: '请求排队中', running: '请求处理中', succeeded: '请求已回复', failed: '回复失败', interrupted: '请求已中断', cancelled: '请求已取消' }
    : { queued: 'Request queued', running: 'Processing request', succeeded: 'Response ready', failed: 'Response failed', interrupted: 'Request interrupted', cancelled: 'Request cancelled' };
  return item.turn ? labels[item.turn.status] ?? item.turn.status : (zh ? '正在提交请求' : 'Submitting request');
}
