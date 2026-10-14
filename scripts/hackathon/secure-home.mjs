import { mkdir, lstat, chmod } from 'node:fs/promises'
import { resolve, relative } from 'node:path'

export async function secureHome(home) {
  const runtime = resolve(import.meta.dirname, '../../runtime')
  const state = resolve(process.env.NW_HACKATHON_RUNTIME || resolve(runtime, 'server'))
  if (relative(runtime, state).startsWith('..')) throw Object.assign(new Error(), { data: { reason: 'CONFIG_HOME_PATH_NOT_ALLOWED' } })
  const expected = resolve(state, 'agh-home')
  if (resolve(home || '') !== expected) throw Object.assign(new Error(), { data: { reason: 'CONFIG_HOME_PATH_NOT_ALLOWED' } })
  await mkdir(home, { recursive: true, mode: 0o700 })
  const info = await lstat(home)
  if (!info.isDirectory() || info.isSymbolicLink()) throw Object.assign(new Error(), { data: { reason: 'CONFIG_HOME_NOT_REAL_DIRECTORY' } })
  if (info.uid !== process.getuid()) throw Object.assign(new Error(), { data: { reason: 'CONFIG_HOME_OWNER_MISMATCH' } })
  const before = (info.mode & 0o777).toString(8)
  await chmod(home, 0o700)
  console.log(`AGH_HOME: owner=current-user type=directory mode=${before}->700`)
}
if (process.argv[1] === import.meta.filename) {
  try { await secureHome(process.env.AGH_HOME) }
  catch (error) {
    const reason = /^CONFIG_[A-Z0-9_]+$/.test(error?.data?.reason || '') ? error.data.reason : 'CONFIG_HOME_PERMISSION_FAILURE'
    console.error(`Agnes configuration failed stage=initialize code=local reason=${reason}`)
    process.exitCode = 1
  }
}
