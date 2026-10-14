import { readFile, writeFile } from 'node:fs/promises'
import { createClient, memoryJournal } from '../../runtime/hackathon/agnes-harness/packages/sdk/src/index.node.ts'

export async function connect() {
  const owner = JSON.parse(await readFile(`${process.env.AGH_HOME}/data/daemon/owner.json`, 'utf8'))
  const client = createClient({ transport: { kind: 'unix', path: owner.socketPath }, auth: { kind: 'local' }, journal: memoryJournal() })
  await client.initialize()
  return client
}

export async function packageOperation(client, kind, params) {
  const profile = process.env.AGNES_PROFILE
  const receipt = await client.packages[kind]({ profile, clientId: await client.clientId(), commandId: crypto.randomUUID(), ...params })
  await writeFile(`${process.env.NW_HACKATHON_RUNTIME}/logs/package-last-operation.json`, JSON.stringify({kind, receipt}, null, 2))
  const end = Date.now() + 90000
  while (Date.now() < end) {
    const result = await client.packages.operation.get({ profile, operationId: receipt.operationId })
    if (['failed', 'cancelled', 'rolled-back'].includes(result.state)) throw new Error(`Package ${kind}: ${result.state} ${JSON.stringify(result.error || {})}`)
    if (result.state === 'completed') return result
    await new Promise(resolve => setTimeout(resolve, 250))
  }
  throw new Error('Package operation timeout')
}
