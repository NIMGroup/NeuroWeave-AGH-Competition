import test from 'node:test'
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'
for (const [name, script, variables, reason] of [
  ['disallowed gateway fails before RPC, without echoing a key', 'configure-agnes.mjs', { NEUROWEAVE_AGNES_BASE_URL: 'https://invalid.invalid', NEUROWEAVE_AGNES_API_KEY: 'unused-local-test-value' }, 'CONFIG_LOCAL_BASE_URL_NOT_ALLOWED'],
  ['home override is refused without creating a directory', 'secure-home.mjs', { AGH_HOME: resolve('runtime/hackathon/unused-home-check') }, 'CONFIG_HOME_PATH_NOT_ALLOWED'],
]) test(name, () => {
  const result = spawnSync('tsx', [`scripts/hackathon/${script}`], { env: { ...process.env, ...variables }, encoding: 'utf8' })
  const output = result.stdout + result.stderr
  assert.equal(result.status, 1)
  assert(output.includes(reason))
  assert(!output.includes('unused-local-test-value') && !output.includes('\n    at ') && !output.includes('stage: save'))
  assert(!existsSync(resolve('runtime/hackathon/unused-home-check')))
})
