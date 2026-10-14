import { useMutation, useQueryClient } from '@tanstack/react-query';
import { sendChat } from './chatApi';
import type { ChatRequest, ChatResponse } from './chatApi';

export function useSendChat() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: ChatRequest): Promise<ChatResponse> => sendChat(payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['conversations'] });
    }
  });
}
