// History cards share a small read queue; React Query deduplicates each key.
// Leaving a conversation aborts its reads, never a submitted server job.
export function createReadQueue(limit = 2) {
  let active = 0;
  const waiting: Array<() => void> = [];
  return async function run<T>(read: () => Promise<T>, signal?: AbortSignal): Promise<T> {
    if (active >= limit) await new Promise<void>(resolve => waiting.push(resolve));
    else active += 1;
    try {
      if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
      return await read();
    } finally {
      const next = waiting.shift();
      if (next) next();
      else active -= 1;
    }
  };
}
export const workflowReads = createReadQueue(2);
