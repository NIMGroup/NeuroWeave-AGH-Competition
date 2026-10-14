import assert from 'node:assert/strict'
import { readFile, writeFile } from 'node:fs/promises'
import { connect } from './agh-client.mjs'
const root = process.env.NW_HACKATHON_RUNTIME
const client = await connect()
try {
  const packages = await client.packages.list({ profile: process.env.AGNES_PROFILE })
  const own = packages.packages.find(p => p.id === '@neuroweave/agh-roi')
  assert(own?.trusted && own.actual === 'running' && own.actualIntegrity === own.integrity)
  assert(packages.packages.filter(p => p.id !== own.id).every(p => p.desired !== 'enabled'))
  const tree = await client.packages.tree.get({ profile: process.env.AGNES_PROFILE })
  assert(tree.actual && !tree.pending && tree.reportOk)
  const events = (await readFile(`${root}/agh-home/data/audit/host.jsonl`, 'utf8')).trim().split('\n').map(JSON.parse)
  const current = events.slice(events.findLastIndex(e => e.kind === 'profile.resolved'))
  const denied = ['agnes/tools-core','agnes/tools-search','agnes/tools-web','agnes/refine','agnes/subagent','agnes/code-mode','agnes/hooks-runner']
  for (const id of denied) assert(current.some(e => e.kind === 'extension.failed' && e.detail.id === id))
  const loaded = current.filter(e => e.kind === 'extension.loaded').map(e => e.detail.id)
  assert(loaded.includes('agnes/compaction') && loaded.includes('agnes/mcp-search'))
  const names = current.filter(e => e.kind === 'extension.registered' && e.detail.package === own.id && e.detail.name.startsWith('nw_')).map(e => e.detail.name).sort()
  const { serverTools } = await import('../../integrations/agh/server.mjs')
  const contracts = JSON.parse(await readFile(new URL('../../integrations/agh/server-contracts.json', import.meta.url), 'utf8'))
  assert.deepEqual(names, serverTools(() => {}, contracts).map(t => t.name).sort())
  const roster = await client.clientModules.list(process.env.AGNES_PROFILE)
  assert(roster.statuses.some(s => s.packageId === own.id && s.state === 'ready'))
  const { presets } = await import('../../integrations/agh/index.mjs')
  assert.equal(presets['neuroweave-roi'].compaction.agent_callable, false)
  const result = { passed: true, at: new Date().toISOString(), plugin: own, tree_identity: tree.identity,
    observed_plugin_tools: names, denied_builtin_extensions: denied, loaded_extensions: loaded,
    source_verified_builtin_tools: ['tool_search','tool_describe'],
    note: 'Host audit and package actual state are observed. Two read-only discovery tools and disabled agent compaction are verified against the pinned AGH source; no OS sandbox is claimed.' }
  await writeFile(`${root}/logs/profile-check.json`, JSON.stringify(result, null, 2))
  console.log('Restricted Host/profile check passed')
} finally { await client.close() }
