import { writeFile } from 'node:fs/promises'
import { connect, packageOperation } from './agh-client.mjs'

const client = await connect()
try {
  const source = { type: 'file', ref: 'file:./integrations/agh' }
  const { preview } = await packageOperation(client, 'inspect', { source })
  if (!preview || preview.blockers.length) throw new Error('Plugin inspection blockers')
  await writeFile(`${process.env.NW_HACKATHON_RUNTIME}/logs/plugin-inspect.json`, JSON.stringify(preview, null, 2))
  const id = '@neuroweave/agh-roi'
  let installed = (await client.packages.list({ profile: process.env.AGNES_PROFILE })).packages.find(p => p.id === id)
  if (installed?.desired === 'enabled') {
    await packageOperation(client, 'disable', { id })
    installed = (await client.packages.list({ profile: process.env.AGNES_PROFILE })).packages.find(p => p.id === id)
  }
  if (!installed) await packageOperation(client, 'install', { source, expectedIntegrity: preview.integrity })
  else if (installed.integrity !== preview.integrity) await packageOperation(client, 'update', { id, source, expectedIntegrity: preview.integrity })
  await packageOperation(client, 'trust', { id, expectedIntegrity: preview.integrity, capabilityHash: preview.capabilityHash })
  await packageOperation(client, 'enable', { id })
  for (const helper of ['@agnes/skill-helper', '@agnes/mcp-helper', '@agnes/plugin-helper', '@agnes/document-reader']) {
    const item = (await client.packages.list({ profile: process.env.AGNES_PROFILE })).packages.find(p => p.id === helper)
    if (item?.desired === 'enabled') await packageOperation(client, 'disable', { id: helper })
  }
  const status = await client.packages.list({ profile: process.env.AGNES_PROFILE })
  await writeFile(`${process.env.NW_HACKATHON_RUNTIME}/logs/plugin-status.json`, JSON.stringify(status, null, 2))
  const active = status.packages.find(p => p.id === id)
  if (!active?.trusted || active.actual !== 'running') throw new Error('Plugin not running')
  console.log(`Plugin installed, trusted and running: ${id}; integrity=${preview.integrity}`)
} finally { await client.close() }
