import { ApiError } from '../../api/http';

/** A failed status read says nothing about whether the server turn failed. */
export function canReconnect(error: unknown): boolean {
  return error instanceof TypeError || error instanceof SyntaxError ||
    (error instanceof DOMException && error.name === 'TimeoutError') ||
    (error instanceof ApiError && [408, 429, 500, 502, 503, 504].includes(error.status));
}

export class TurnConnectionError extends Error {
  constructor(message: string) { super(message); this.name = 'TurnConnectionError'; }
}

export class TurnRecordMissingError extends Error {
  constructor(message: string) { super(message); this.name = 'TurnRecordMissingError'; }
}

type RecoveryOptions = {
  pause?: (milliseconds: number) => Promise<unknown>;
  clock?: () => number;
  retryWhen?: (error: unknown, id: string) => boolean;
  connection: (id: string, reconnecting: boolean) => void;
  message: () => string;
};

/** Coalesce readers and retry only GETs; never replay a submission or a tool. */
export function createTurnReader<T>(read: (id: string) => Promise<T>, options: RecoveryOptions) {
  const inflight = new Map<string, Promise<T>>();
  const pause = options.pause ?? (ms => new Promise(resolve => setTimeout(resolve, ms)));
  const clock = options.clock ?? Date.now;
  const retryWhen = options.retryWhen ?? canReconnect;
  return (id: string): Promise<T> => {
    const existing = inflight.get(id);
    if (existing) return existing;
    const promise = (async () => {
      const deadline = clock() + 60_000;
      for (let failures = 0; ; failures++) {
        try {
          const value = await read(id);
          options.connection(id, false);
          return value;
        } catch (error) {
          if (!retryWhen(error, id)) throw error;
          options.connection(id, true);
          const remaining = deadline - clock();
          if (failures >= 6 || remaining <= 0) {
            if (!canReconnect(error)) throw error;
            throw new TurnConnectionError(options.message());
          }
          await pause(Math.min(1000 * 2 ** failures, 8000, remaining));
          if (clock() >= deadline) throw new TurnConnectionError(options.message());
        }
      }
    })().finally(() => inflight.delete(id));
    inflight.set(id, promise);
    return promise;
  };
}
