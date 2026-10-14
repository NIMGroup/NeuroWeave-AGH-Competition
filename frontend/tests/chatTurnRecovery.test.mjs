import assert from 'node:assert/strict';
import { test } from 'node:test';
import { resolve } from 'node:path';
import { createServer } from 'vite';

const originals = Object.fromEntries(['window', 'localStorage', 'fetch'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
const storage = new Map();
Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: {
  getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value)
} });
globalThis.window = new EventTarget();
const server = await createServer({ root: resolve(import.meta.dirname, '..'), configFile: false,
  server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });
try {
  const load = path => server.ssrLoadModule('/src/' + path);
  const { ApiError } = await load('api/http.ts');
  const { createTurnReader, TurnConnectionError, TurnRecordMissingError } = await load('features/chat/turnRecovery.ts');
  const { submitTurn, waitForTurn, retryTurn, pendingTurns, readTurn, forgetTurn } = await load('features/chat/chatTurns.ts');
  const options = { pause: async () => {}, connection() {}, message: () => 'connection unconfirmed' };
  const ready = id => ({ turn_id: id, conversation_id: 'conversation', status: 'succeeded', message: 'done', error: null, result: { content: 'reply', conversation_id: 'conversation' } });
  const json = body => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } });

  await test('concurrent readers recover one original ID after network and gateway failures', async () => {
    const reads = [], connection = [], delays = [];
    const read = createTurnReader(async id => {
      reads.push(id);
      if (reads.length === 1) throw new TypeError('Failed to fetch');
      if (reads.length === 2) throw new ApiError(502, 'gateway unavailable');
      return ready(id);
    }, { ...options, pause: async ms => { delays.push(ms); }, connection: (id, state) => connection.push([id, state]) });
    const a = read('original'), b = read('original');
    assert.equal(a, b);
    assert.deepEqual(await a, ready('original'));
    assert.deepEqual(reads, ['original', 'original', 'original']);
    assert.deepEqual(delays, [1000, 2000]);
    assert.deepEqual(connection.at(-1), ['original', false]);
  });
  await test('permanent access and identity errors are surfaced without automatic retry', async () => {
    for (const status of [400, 401, 403, 404, 409, 422]) {
      let calls = 0;
      const error = new ApiError(status, 'definitive rejection');
      const read = createTurnReader(async () => { calls++; throw error; }, options);
      await assert.rejects(read('id'), e => e === error);
      assert.equal(calls, 1);
    }
    const abort = new DOMException('cancelled by observer', 'AbortError');
    const read = createTurnReader(async () => { throw abort; }, options);
    await assert.rejects(read('id'), e => e === abort);
  });
  await test('offline recovery has both an attempt bound and a wall-clock deadline', async () => {
    let calls = 0;
    const read = createTurnReader(async () => { calls++; throw new TypeError('offline'); }, options);
    await assert.rejects(read('id'), TurnConnectionError);
    assert.equal(calls, 7);
    let now = 0, timedCalls = 0;
    const timed = createTurnReader(async () => { timedCalls++; now += 30_000; throw new DOMException('timeout', 'TimeoutError'); },
      { ...options, clock: () => now, pause: async ms => { now += ms; } });
    await assert.rejects(timed('id'), TurnConnectionError);
    assert.equal(timedCalls, 2);
  });
  await test('a lost POST acknowledgement recovers the accepted ID without resubmission', async () => {
    const calls = [];
    globalThis.fetch = async (path, init) => {
      calls.push([path, init.method ?? 'GET']);
      if (init.method === 'POST') throw new TypeError('acknowledgement lost after acceptance');
      return json(ready('lost-ack'));
    };
    const response = await submitTurn({ client_turn_id: 'lost-ack', message: 'summarize this table' });
    assert.equal(response.content, 'reply');
    assert.deepEqual(calls.map(c => c[1]), ['POST', 'GET']);
    assert(calls[1][0].endsWith('/lost-ack'));
    assert(!pendingTurns().includes('lost-ack'));
  });
  await test('an immediate panel observer waits for registration and shares the successful read with the sender', async () => {
    const calls = [];
    let release;
    const accepted = new Promise(resolve => { release = resolve; });
    let observer;
    const observe = () => {
      if (pendingTurns().includes('registration-race'))
        observer ??= readTurn('registration-race');
    };
    window.addEventListener('neuroweave:chat-progress', observe);
    globalThis.fetch = async (path, init) => {
      calls.push([path, init.method ?? 'GET']);
      if (init.method === 'POST') return accepted;
      return json(ready('registration-race'));
    };
    try {
      const submitted = submitTurn({ client_turn_id: 'registration-race', message: 'organize these data' });
      await new Promise(resolve => setTimeout(resolve, 0));
      assert(observer);
      assert.deepEqual(calls.map(c => c[1]), ['POST'], 'GET must not precede registration');
      release(json({ ...ready('registration-race'), status: 'queued', result: null }));
      assert.equal((await submitted).content, 'reply');
      assert.equal((await observer).status, 'succeeded');
      assert.deepEqual(calls.map(c => c[1]), ['POST', 'GET'], 'observers must coalesce after acknowledgement');
      assert(!pendingTurns().includes('registration-race'));
    } finally { window.removeEventListener('neuroweave:chat-progress', observe); }
  });
  await test('lost acknowledgement with delayed registration retries only GET on the original ID', async () => {
    const calls = [];
    globalThis.fetch = async (path, init) => {
      calls.push([path, init.method ?? 'GET']);
      if (init.method === 'POST') throw new TypeError('acknowledgement lost');
      if (calls.length === 2) return new Response(JSON.stringify({ detail: 'Turn not found' }), { status: 404 });
      return json(ready('delayed-registration'));
    };
    assert.equal((await submitTurn({ client_turn_id: 'delayed-registration', message: 'hello' })).content, 'reply');
    assert.deepEqual(calls.map(c => c[1]), ['POST', 'GET', 'GET']);
    assert(calls.slice(1).every(([path]) => path.endsWith('/delayed-registration')));
    assert(!pendingTurns().includes('delayed-registration'));
  });
  await test('registration metadata survives a reload and expires for genuinely missing IDs', async () => {
    const registrationKey = 'neuroweave:pending-chat-turns:registration';
    storage.set(registrationKey, JSON.stringify({ 'reloaded-registration': Date.now() + 60_000, expired: Date.now() - 1 }));
    const calls = [];
    globalThis.fetch = async (path, init) => {
      calls.push([path, init.method ?? 'GET']);
      if (calls.length === 1 || !path.endsWith('/reloaded-registration'))
        return new Response(JSON.stringify({ detail: 'Turn not found' }), { status: 404 });
      return json(ready('reloaded-registration'));
    };
    assert.equal((await readTurn('reloaded-registration')).status, 'succeeded');
    assert(!Object.hasOwn(JSON.parse(storage.get(registrationKey)), 'reloaded-registration'));
    for (const id of ['expired', 'old-missing']) {
      const before = calls.length;
      await assert.rejects(readTurn(id), error => error instanceof TurnRecordMissingError && !error.message.includes('Turn not found'));
      assert.equal(calls.length, before + 1);
    }
    assert(calls.every(([, method]) => method === 'GET'));
    await forgetTurn('expired');
    assert(!Object.hasOwn(JSON.parse(storage.get(registrationKey)), 'expired'));
  });
  await test('registration retries remain bounded and do not retry access errors', async () => {
    let calls = 0, now = 0;
    const missing = new TurnRecordMissingError('Request record unavailable');
    const read = createTurnReader(async () => { calls++; throw missing; }, {
      ...options, clock: () => now, pause: async ms => { now += ms; },
      retryWhen: (error, id) => error === missing && id === 'submitting' && now < 10_000
    });
    await assert.rejects(read('submitting'), error => error === missing);
    assert.equal(calls, 5);
    const denied = new ApiError(403, 'access denied');
    let deniedCalls = 0;
    const restricted = createTurnReader(async () => { deniedCalls++; throw denied; },
      { ...options, retryWhen: error => error instanceof TurnRecordMissingError });
    await assert.rejects(restricted('submitting'), error => error === denied);
    assert.equal(deniedCalls, 1);
  });
  await test('a definitive POST rejection releases panel observers without issuing a status GET', async () => {
    const calls = [];
    let observer;
    const observe = () => {
      if (pendingTurns().includes('rejected-observed'))
        observer ??= readTurn('rejected-observed').catch(error => error);
    };
    window.addEventListener('neuroweave:chat-progress', observe);
    globalThis.fetch = async (path, init) => {
      calls.push([path, init.method ?? 'GET']);
      return new Response(JSON.stringify({ detail: 'access denied' }), { status: 403 });
    };
    try {
      await assert.rejects(submitTurn({ client_turn_id: 'rejected-observed', message: 'hello' }),
        error => error instanceof ApiError && error.status === 403);
      assert((await observer) instanceof ApiError);
      assert.deepEqual(calls.map(c => c[1]), ['POST']);
      assert(!pendingTurns().includes('rejected-observed'));
    } finally { window.removeEventListener('neuroweave:chat-progress', observe); }
  });
  await test('a transient polling failure does not turn a running request into failure', async () => {
    let reads = 0;
    globalThis.fetch = async () => {
      reads++;
      if (reads === 1) return json({ ...ready('reading'), status: 'running', result: null });
      if (reads === 2) throw new TypeError('brief disconnect');
      return json(ready('reading'));
    };
    assert.equal((await waitForTurn('reading')).content, 'reply');
    assert.equal(reads, 3);
  });
  await test('recovery of an already running request never POSTs the retry endpoint', async () => {
    const calls = [];
    globalThis.fetch = async (path, init) => {
      calls.push([path, init.method ?? 'GET']);
      return json(calls.length === 1 ? { ...ready('still-running'), status: 'running', result: null } : ready('still-running'));
    };
    assert.equal((await retryTurn('still-running')).content, 'reply');
    assert(calls.every(([, method]) => method === 'GET'));
  });
  await test('confirmed backend failure remains visible and rejected submissions are not left pending', async () => {
    globalThis.fetch = async () => json({ ...ready('failed'), status: 'failed', result: null, error: { message: 'actual model service failure' } });
    await assert.rejects(waitForTurn('failed'), /actual model service failure/);
    for (const status of [403, 409, 429]) {
      const calls = [];
      globalThis.fetch = async (path, init) => { calls.push([path, init.method ?? 'GET']); return new Response(JSON.stringify({ detail: 'rejected' }), { status }); };
      await assert.rejects(submitTurn({ client_turn_id: 'rejected-'+status, message: 'hello' }), e => e instanceof ApiError && e.status === status);
      assert.equal(calls.filter(([, method]) => method === 'POST').length, 1);
      assert(calls.filter(([, method]) => method === 'GET').every(([path]) => path === '/api/chat/turns'));
      assert(!pendingTurns().includes('rejected-'+status));
    }
  });
} finally {
  await server.close();
  for (const [key, descriptor] of Object.entries(originals)) {
    if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key];
  }
}
