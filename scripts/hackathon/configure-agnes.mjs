// Official AGH configuration service writes secret:// references and its own revisions.
import { readFile, writeFile, lstat, chmod } from 'node:fs/promises'
import { createClient, memoryJournal } from '../../runtime/hackathon/agnes-harness/packages/sdk/src/index.node.ts'
import { secureHome } from './secure-home.mjs'

let stage = 'initialize'
let client
try {
  console.log(`AGH configuration stage: ${stage}`)
  const envPath = process.env.NW_HACKATHON_RUNTIME ? `${process.env.NW_HACKATHON_RUNTIME}/agnes.env` : new URL('../../runtime/server/agnes.env', import.meta.url)
  try {
    const info = await lstat(envPath)
    if (!info.isFile() || info.isSymbolicLink() || info.uid !== process.getuid()) throw Object.assign(new Error(), { data: { reason: 'CONFIG_UNSAFE_LOCAL_CREDENTIAL_FILE' } })
    await chmod(envPath, 0o600)
    process.loadEnvFile(envPath)
  } catch (error) {
    if (error.code !== 'ENOENT') throw Object.assign(new Error(), { data: { reason: 'CONFIG_UNSAFE_LOCAL_CREDENTIAL_FILE' } })
  }

  const key = process.env.NEUROWEAVE_AGNES_API_KEY
  const baseUrl = process.env.NEUROWEAVE_AGNES_BASE_URL || 'https://api.agnes-ai.cn/v1'
  const model = process.env.NEUROWEAVE_AGNES_MODEL || 'agnes-3.0-flash'
  if (!key) throw Object.assign(new Error(), { data: { reason: 'CONFIG_LOCAL_KEY_MISSING' } })
  if (baseUrl !== 'https://api.agnes-ai.cn/v1') throw Object.assign(new Error(), { data: { reason: 'CONFIG_LOCAL_BASE_URL_NOT_ALLOWED' } })
  await secureHome(process.env.AGH_HOME)
  const owner = JSON.parse(await readFile(`${process.env.AGH_HOME}/data/daemon/owner.json`, 'utf8'))
  client = createClient({ transport: { kind: 'unix', path: owner.socketPath }, auth: { kind: 'local' }, journal: memoryJournal() })
  await client.initialize()
  stage = 'get'; console.log(`AGH configuration stage: ${stage}`)
  const before = await client.config.get()
  stage = 'test'; console.log(`AGH configuration stage: ${stage}`)
  const catalog = await client.config.test({ providerId: 'agnes-ai', baseUrl, apiKey: key })
  if (catalog.verified !== true) throw Object.assign(new Error(), { data: { reason: 'CONFIG_CONNECTION_NOT_VERIFIED' } })
  if (!catalog.models.some(item => item.id === model)) throw Object.assign(new Error(), { data: { reason: 'CONFIG_REQUESTED_MODEL_ABSENT' } })
  stage = 'save'; console.log(`AGH configuration stage: ${stage}`)
  await client.config.save({ providerId: 'agnes-ai', baseUrl, apiKey: key, model, expectedRevision: before.revision })
  stage = 'get-after-save'; console.log(`AGH configuration stage: ${stage}`)
  const after = await client.config.get()
  if (!after.configured || after.provider?.id !== 'agnes-ai' || after.provider?.model !== model)
    throw Object.assign(new Error(), { data: { reason: 'CONFIG_SAVED_STATE_MISMATCH' } })
  // get() is the official redacted configuration projection. Do not export it wholesale.
  await writeFile(`${process.env.AGH_HOME}/configuration-ready.json`, JSON.stringify({
    configured: after.configured, verified: true, provider: 'agnes-ai', baseUrl, model, route: after.provider?.route,
    checked_at: new Date().toISOString(), source: 'AGH config.test + config.save',
    note: 'Catalog/configuration check only; real model tool calling has a separate acceptance record.',
  }, null, 2), { mode: 0o600 })
  console.log('Agnes configuration saved through AGH. Real tool-call acceptance is still required.')
} catch (error) {
  // Upstream transport errors can include request context. Never print raw errors here.
  const reason = typeof error?.data?.reason === 'string' && /^CONFIG_[A-Z0-9_]{1,80}$/.test(error.data.reason) ? error.data.reason : 'CONFIG_REASON_UNAVAILABLE'
  const code = Number.isInteger(error?.code) ? error.code : 'local'
  console.error(`Agnes configuration failed stage=${stage} code=${code} reason=${reason}`)
  process.exitCode = 1
} finally {
  await client?.close().catch(() => { console.error('AGH configuration cleanup: CONFIG_CLIENT_CLOSE_FAILED'); process.exitCode = 1 })
}
