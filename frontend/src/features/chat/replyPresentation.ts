import type { ChatMessage } from './chatMessages';

/** Fold only the legacy, generated audit paragraph; preserve ordinary answers. */
export function legacyProposalReply(message: ChatMessage): boolean {
  return message.role === 'assistant'
    && message.content.startsWith('方案尚未满足目标，当前不能运行：')
    && message.content.endsWith('尚未修改文件。')
    && Boolean(message.workspaceEvents?.some(event => event.data_operation?.status === 'pending'
      && event.data_operation.quality?.proposal_review?.decision === 'revise'));
}
