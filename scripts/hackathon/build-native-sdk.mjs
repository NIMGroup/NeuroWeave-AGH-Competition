import { createRequire } from 'node:module';
import { resolve } from 'node:path';
const root = resolve(import.meta.dirname, '../..');
const native = resolve(root, 'runtime/hackathon/agnes-harness');
const require = createRequire(resolve(native, 'packages/cli/package.json'));
const { build } = require('esbuild');
await build({ stdin: { contents: `export { createClient, memoryJournal } from ${JSON.stringify(resolve(native, 'packages/sdk/src/index.node.ts'))};`,
  resolveDir: root, sourcefile: 'neuroweave-native-sdk.ts' }, bundle: true, platform: 'node', format: 'esm', target: 'node24',
  outfile: resolve(root, 'integrations/agh/native-sdk.mjs'), legalComments: 'eof',
  banner: { js: 'import { createRequire as nwCreateRequire } from "node:module"; const require = nwCreateRequire(import.meta.url);' } });
