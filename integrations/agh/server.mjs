import { readFileSync, lstatSync } from 'node:fs'
import { nativeContextReader } from './native-context.mjs'

const Kind = Symbol.for('TypeBox.Kind')
const string = extra => ({ type: 'string', ...extra })
const object = properties => ({ type: 'object', properties, required: Object.keys(properties), additionalProperties: false })
const reference = { action_id: string({ pattern: '^action_[a-f0-9]{32}$' }) }
const nativeTools = [
  ['update_task_state', null, true, 'Save or revise the persistent natural-language goal before planning. Declare every explicitly existing or missing input with exact native user quotes; missing inputs use available=false. Obtain artifact_type from the selected method input slot type, never invent a type from a role name. Do not author internal output types or references here: use nw_method_routes.targets and stage_outputs from real catalog output_refs. Follow-ups revise the same goal until the user closes it. Metadata only; never execution authority.'],
  ['finish_task', null, true, 'Report this turn outcome from real saved artifacts. This never confirms user completion or archives a goal. Use awaiting_inputs for editable drafts with unbound inputs.'],
  ['context', {}, false, 'Read current server dataset, output directory, workflow and task state for this AGH session. Call before planning; facts do not grant execution authority.'],
  ['prepare_workflow', null, true, 'Deliver an editable workflow draft immediately from nw_method_routes. Pass goal and route_id ONLY; for revising an existing draft also pass workflow_id and expected_revision. Never pass contract, plan or bindings. The server restores the registered graph and defaults. The user chooses inputs, parameters and resources in the workflow card after delivery; do not ask for these confirmations in chat first. This only saves a draft and never executes computation or changes data files.'],
  ['method_routes', null, false, 'Inherit the canonical natural-language goal and declarations saved by nw_update_task_state. Select targets from actual catalog output_refs; stage_outputs must match each required_stages index. The server derives all output types from registered ports. Returns durable route IDs and resumable search summaries. External files are selected after draft delivery; do not scan data, invent bindings, or add unrequested producers.'],
  ['shared_facts', { owner: { type: 'string', enum: ['data', 'methods', 'runtime'] }, fact_id: string({}), entity_id: string({}), field: string({}), offset: { type: 'integer', minimum: 0 }, limit: { type: 'integer', minimum: 1, maximum: 100 } }, false, 'Read the current AGH conversation and selection-scoped 3003 shared facts. Reuse lossless method contracts and typed data/runtime evidence; facts do not authorize execution.'],
  ['artifact_types', {}, false, 'Read registered artifact type IDs for diagnostics or user-declared input compatibility. Scientific goal outputs must come from actual catalog output_refs via nw_method_routes.targets, not from guessing type names or querying this whole dictionary.'],
  ['prepare_execution', { workflow_id: string({ pattern: '^wf_[a-f0-9]{12}$' }), expected_revision: { type: 'integer', minimum: 1 }, execution: { type: 'object', additionalProperties: true } }, true,
    'Prepare an immutable execution proposal for a preflighted, confirmed workflow. Includes inputs, parameters, output, node/resources and exact revision. This model tool never grants user_requested authority; actual execution still requires the user to click the workbench action button.'],
  ['action_details', reference, false, 'Read the complete frozen action, resource/input/output scope, user_requested authority and current state. Do not ask for another approval.'],
  ['read_workflow', { workflow_id: string({ pattern: '^wf_[a-f0-9]{12}$' }) }, false,
    'Read an actual workflow and the latest runs owned by this AGH session. Returns draft, preflight, confirmation and real run state.'],
  ['read_run', { run_id: string({ pattern: '^run_[a-f0-9]+$' }) }, false,
    'Read actual run progress, Slurm scheduler identities, steps, artifacts, failures and recent execution events. Query after timeout; queued/running never means completed.'],
  ['apply_action', { ...reference, action_digest: string({ pattern: '^[a-f0-9]{64}$' }), reviewed_scope: { type: 'object', additionalProperties: true } }, true,
    'Execute exactly a frozen action whose nw_action_details.user_requested is true: the user already clicked its workbench Run/action button. No additional approval is needed. Copy reviewed_scope unchanged; the backend validates user intent, ownership, revision, resources and input fingerprints. A model-prepared action alone cannot execute. Replays return their original result; unknown dispatches are never repeated.'],
  ['verify_run', { run_id: string({ pattern: '^run_[a-f0-9]+$' }) }, false,
    'Verify an actual succeeded run: output files and recorded content hashes. This is artifact integrity, not universal scientific or clinical validity. Read method quality reports and explain remaining scientific checks.'],
]

// TypeBox is the official AGH schema contract. Plain JSON schemas are converted,
// including nested unions and optional members; no runtime dependency is copied.
export function typebox(schema, defs = {}) {
  if (schema.$ref) return typebox(defs[schema.$ref.split('/').at(-1)], defs)
  const result = { ...schema }
  delete result.$defs
  if (schema.enum) {
    return { [Kind]: 'Union', anyOf: schema.enum.map(value => ({ [Kind]: 'Literal', type: typeof value, const: value })) }
  }
  if (schema.anyOf) return { ...result, [Kind]: 'Union', anyOf: schema.anyOf.map(s => typebox(s, defs)) }
  const kinds = { object: 'Object', array: 'Array', string: 'String', integer: 'Integer', number: 'Number', boolean: 'Boolean', null: 'Null' }
  if (schema.type === 'object') result.properties = Object.fromEntries(Object.entries(schema.properties ?? {}).map(([key, value]) => [key, typebox(value, value.$defs ?? defs)]))
  if (schema.items) result.items = typebox(schema.items, defs)
  if (schema.additionalProperties && typeof schema.additionalProperties === 'object') result.additionalProperties = typebox(schema.additionalProperties, defs)
  return { ...result, [Kind]: kinds[schema.type] ?? 'Any' }
}

export function serverTools(send, contracts) {
  const definitions = contracts.tools.map(tool => ({ ...tool, operation: 'tool', encode: args => ({ name: tool.business_name, arguments: args }) }))
  for (const [name, fields, mutation, description] of nativeTools) {
    definitions.push({ name: `nw_${name}`, mutation, description,
      parameters: name === 'update_task_state' ? contracts.goal : name === 'finish_task' ? contracts.finish : name === 'prepare_workflow' ? contracts.workflow : name === 'method_routes' ? contracts.route : name === 'shared_facts' ? { type: 'object', properties: fields, additionalProperties: false } : name === 'prepare_execution' ? object({ ...fields, execution: contracts.execution }) : object(fields),
      operation: ({ update_task_state: 'task-update', finish_task: 'task-finish', prepare_workflow: 'workflow-prepare', prepare_execution: 'action-prepare',
        method_routes: 'method-routes', shared_facts: 'shared-facts', artifact_types: 'artifact-types',
        action_details: 'action-read', apply_action: 'action-apply', verify_run: 'verify-run', read_workflow: 'read-workflow', read_run: 'read-run' })[name] ?? name,
      encode: name === 'prepare_execution' ? args => ({ path: `/api/workflows/${args.workflow_id}/runs`, method: 'POST',
        body: { expected_revision: args.expected_revision, execution: args.execution } }) : args => args })
  }
  return definitions.map(definition => ({
    name: definition.name, description: definition.description,
    parameters: typebox(definition.parameters, definition.parameters.$defs),
    meta: { isReadOnly: !definition.mutation, isDestructive: definition.name === 'nw_apply_action',
      isConcurrencySafe: !definition.mutation, isOpenWorld: false,
      costHint: undefined, deferLoading: false,
      replay: definition.mutation ? (definition.name === 'nw_apply_action' ? 'idempotent' : 'never') : 'safe',
      requiresApproval: 'never' },
    ...(definition.name === 'nw_apply_action' ? {
      policyVersion: 'nw-user-action-v2',
      classify: args => ({ isReadOnly: false, isDestructive: true, replay: 'idempotent', requiresApproval: 'never',
        approvalScopes: [`nw.action.${args.action_id.slice(7)}.${args.action_digest.slice(0, 12)}`] }),
    } : {}),
    async execute(args, context) {
      const result = await send(definition.operation, definition.encode(args), context.session.key, context.session.toolUseId, context.signal)
      return { content: [{ type: 'text', text: JSON.stringify(result) }], structured: result,
        isError: result.ok !== true || result.result?.status >= 400 }
    },
  }))
}

export async function promptContext(send, readNativeMessages, context) {
  const key = context.session.key
  try {
    const captured = await send('native-messages', { messages: await readNativeMessages(key) }, key, '', context.signal)
    if (!captured.ok) throw new Error('NeuroWeave native message capture rejected')
  } catch (error) {
    context.log.warn('NeuroWeave native user capture failed: ' + error.name + ': ' + error.message)
    throw error
  }
  const state = await send('prompt-context', {}, key, '', context.signal)
  if (!state.ok) throw new Error('NeuroWeave prompt context unavailable')
  const value = state.result
  return { sections: [{ id: 'neuroweave/domain', order: 240,
    content: readFileSync(new URL('./prompts/neuro-weave.md', import.meta.url), 'utf8') },
    { id: 'neuroweave/current-context', order: 241, content: JSON.stringify({
      session_id: key, selected: value?.selected, selected_guidance: value?.selected_guidance,
      dataset: value?.dataset ? {
        id: value.dataset.dataset_id, path: value.dataset.absolute_path, inspection: {
          sample_count: value.dataset.inspection?.sample_count, input_types: value.dataset.inspection?.input_types,
          features: value.dataset.inspection?.features } } : null,
      output: value?.workspace, workflow_id: value?.workflow?.workflow_id,
      workflow_revision: value?.workflow?.revision, workflow_status: value?.workflow?.status,
      data_operations: value?.data_operations?.map(o => ({ id: o.id, status: o.status, title: o.title })),
      business_baseline: value?.business_baseline, method_routes: value?.method_routes, shared_facts: value?.shared_facts,
      task: value?.task,
      facts_are_not_permission: true }) }] }
}

export const serverRuntime = {
  apply(ctx) {
    const path = process.env.NW_BRIDGE_TOKEN_FILE
    const stat = lstatSync(path)
    if (!stat.isFile() || stat.isSymbolicLink() || stat.uid !== process.getuid() || (stat.mode & 0o077)) throw new Error('Unsafe bridge credential')
    const token = readFileSync(path, 'utf8').trim()
    if (token.length < 32) throw new Error('Invalid bridge credential')
    const contracts = JSON.parse(readFileSync(new URL('./server-contracts.json', import.meta.url), 'utf8'))
    const reader = nativeContextReader()
    ctx.effect(() => { void reader.warmup().catch(() => {}); return () => reader.close() })
    const send = async (operation, args, session, tool = '', signal) => {
      const port = process.env.NW_BRIDGE_PORT ?? '8127'
      if (!/^\d{4,5}$/.test(port) || Number(port) > 65535) throw new Error('Invalid loopback bridge port')
      const response = await fetch(`http://127.0.0.1:${port}/bridge/v2/${operation}`, {
        method: 'POST', redirect: 'error', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}`,
          'X-NeuroWeave-Session': session, 'X-NeuroWeave-Tool-Use': tool },
        body: JSON.stringify(args), signal: AbortSignal.any([...(signal ? [signal] : []), AbortSignal.timeout(120000)]),
      })
      const body = await response.text()
      if (body.length > 4 * 1024 * 1024) throw new Error('Bridge response exceeds bounded result size')
      return JSON.parse(body)
    }
    const extension = ctx.extension()
    for (const tool of serverTools(send, contracts)) extension.registerTool(tool)
    extension.registerHook('context', (_payload, context) => promptContext(send, key => reader.read(key), context))
    extension.registerHook('approval_request', async (payload, context) => {
      if (payload.request.tool !== 'nw_apply_action') return {}
      const args = payload.request.argv
      if (!args || typeof args !== 'object' || !args.action_id) return {}
      const result = await send('action-read', { action_id: args.action_id }, context.session.key, '', context.signal)
      if (!result.ok || result.result.action_digest !== args.action_digest) throw new Error('Prepared approval identity does not match')
      const proposal = result.result.proposal
      const wf = proposal.state.workflow
      const detail = { action_id: args.action_id, digest: args.action_digest, request: proposal.request,
        workflow: wf ? { title: wf.title, revision: wf.revision, output: wf.workspace_root_path,
          dataset: wf.dataset?.absolute_path, methods: wf.steps.map(s => ({ id: s.id, method: s.selected_method_id, parameters: s.selected_parameters })),
          quality: wf.dry_run_report } : proposal.state,
        input_count: Object.keys(proposal.state.inputs ?? {}).length }
      return { request: { summary: '执行 NeuroWeave 已冻结的具体服务器方案',
        context: JSON.stringify(detail).slice(0, 4000) } }
    })
    const inputSchema = { type: 'object', properties: {
      session_key: { type: 'string', minLength: 1, maxLength: 200 }, input: { type: 'object', additionalProperties: true } },
      required: ['session_key', 'input'], additionalProperties: false }
    for (const [name, operation, kind] of [
      ['nw.panel.query', 'panel-query', 'query'], ['nw.panel.edit', 'panel-edit', 'effect'],
      ['nw.session.context', 'context', 'query'], ['nw.session.select', 'select', 'effect'],
      ['nw.action.prepare', 'action-prepare-user', 'effect'], ['nw.action.read', 'action-read', 'query'],
      ['nw.action.submit', 'action-submit-user', 'effect'], ['nw.action.status', 'action-status', 'query'],
    ]) {
      ctx.services.register({ name, kind, inputSchema, outputSchema: { type: 'object', additionalProperties: true },
        timeoutMs: 30000, maxResultBytes: 1024 * 1024,
        handler: (input, context) => send(operation, input.input, input.session_key, '', context.signal) })
    }
    for (const [name, operation, kind] of [
      ['nw.workspace.query', 'workspace-preferences', 'query'],
      ['nw.workspace.edit', 'workspace-update', 'effect'],
      ['nw.workspace.remove', 'workspace-remove', 'effect'],
      ['nw.session.removalcheck', 'session-removal-check', 'query'],
    ]) {
      ctx.services.register({ name, kind,
        inputSchema: { type: 'object', properties: { input: { type: 'object', additionalProperties: true } }, required: ['input'], additionalProperties: false },
        outputSchema: { type: 'object', additionalProperties: true }, timeoutMs: 30000, maxResultBytes: 1024 * 1024,
        handler: (input, context) => send(operation, input.input, '', '', context.signal) })
    }
    if (ctx.skills) {
      ctx.skills.register({ name: 'neuroweave-scientific-workflow',
        description: '使用服务器真实数据，按登记方法契约规划、预检，由工作台运行按钮授权 Slurm 执行并核验产物。',
        body: readFileSync(new URL('./prompts/scientific-workflow.md', import.meta.url), 'utf8') })
      const skills = JSON.parse(readFileSync(new URL('./domain-skills.json', import.meta.url), 'utf8'))
      for (const skill of skills) ctx.skills.register({ ...skill,
        name: 'neuroweave-' + skill.name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, ''),
        body: `NeuroWeave catalog identifier: ${skill.name}. Use this original identifier with catalog tools.\n\n${skill.body}` })
    }
  },
}
