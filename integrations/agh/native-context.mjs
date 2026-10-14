import { readFile } from 'node:fs/promises';

/** Only official projection queries; never load, attach, prompt or claim a session. */
export function nativeContextReader() {
  let client;
  let pending;
  const connect = async () => {
    if (client) return client;
    pending ??= (async () => {
      const { createClient, memoryJournal } = await import('./native-sdk.mjs');
      const owner = JSON.parse(await readFile(`${process.env.AGH_HOME}/data/daemon/owner.json`, 'utf8'));
      const value = createClient({ transport: { kind: 'unix', path: owner.socketPath }, auth: { kind: 'local' }, journal: memoryJournal() });
      await value.initialize(); client = value; return value;
    })().finally(() => { pending = undefined; });
    return pending;
  };
  return {
    warmup: connect,
    async read(sessionId) {
      const c = await connect();
      const ui = await c.call('_agnes/v1/session.projectUI', { sessionId, surface: 'web' });
      return ui.nodes.filter(node => node.kind === 'user').slice(-16).map(node => ({
        sequence: node.seq, text: node.content.filter(block => block.type === 'text').map(block => block.text).join('\n').slice(0, 8000),
      })).filter(node => node.text);
    },
    async close() { await (client ?? await pending)?.close(); client = undefined; },
  };
}
