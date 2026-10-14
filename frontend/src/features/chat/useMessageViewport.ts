import { useRef, useLayoutEffect, useCallback } from 'react';

/** Follow newly appended messages without scrolling ancestors or disturbing prepended history. */
export function useMessageViewport(
  activeConversationId: string | null,
  contextLoading: boolean,
  latestMessageId?: string
) {
  const followTail = useRef(true);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const scrollScope = activeConversationId ?? 'new-conversation';
  const initializedScrollScope = useRef<string | null>(null);
  useLayoutEffect(() => {
    if (contextLoading) return;
    const viewport = scrollRef.current;
    if (initializedScrollScope.current !== scrollScope) {
      initializedScrollScope.current = scrollScope;
      followTail.current = true;
    }
    // Opening history must jump directly to the latest page: smooth scrolling
    // from the top can otherwise trigger eager loading of every older page.
    // Prepending history and editing parameters leave latestMessageId unchanged.
    if (followTail.current && viewport)
      viewport.scrollTop = viewport.scrollHeight;
  }, [scrollScope, contextLoading, latestMessageId]);

  const restorePrepend = (anchor: { top: number; height: number } | null) => {
    const viewport = scrollRef.current;
    if (viewport && anchor)
      viewport.scrollTop = anchor.top + viewport.scrollHeight - anchor.height;
  };
  const setFollowTail = useCallback((follow: boolean) => {
    followTail.current = follow;
  }, []);
  return { setFollowTail, scrollRef, scrollScope, restorePrepend };
}
