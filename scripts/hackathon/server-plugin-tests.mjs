import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { serverTools, typebox, promptContext } from '../../integrations/agh/server.mjs'

const contracts = JSON.parse(readFileSync(new URL('../../integrations/agh/server-contracts.json', import.meta.url)))

test('context hook captures native messages then reads only prompt context, never scheduler or execution', async () => {
  const calls = [], signal = new AbortController().signal
  const context = {session:{key:'native-session'},signal,log:{warn(){}}}
  const state = {selected:{workflow_id:'wf_123456789abc'},task:{goal:'synthetic goal'},shared_facts:{value:1},
    workflow:{workflow_id:'wf_123456789abc',revision:4,status:'confirmed'},
    dataset:{dataset_id:'synthetic',absolute_path:'/synthetic',inspection:{sample_count:1}},workspace:{root_path:'/output'}}
  const result = await promptContext(async (operation,body,key,tool,abort) => {
    calls.push(operation); assert.equal(key,context.session.key); assert.equal(tool,''); assert.equal(abort,signal)
    if(operation==='native-messages') {assert.deepEqual(body.messages,[{sequence:12,text:'run reviewed plan'}]);return {ok:true}}
    assert.equal(operation,'prompt-context'); return {ok:true,result:state}
  }, async key => {assert.equal(key,context.session.key);return [{sequence:12,text:'run reviewed plan'}]},context)
  assert.deepEqual(calls,['native-messages','prompt-context'])
  const projected = JSON.parse(result.sections.find(section=>section.id==='neuroweave/current-context').content)
  assert.deepEqual(projected.task,state.task);assert.deepEqual(projected.shared_facts,state.shared_facts)
  assert.equal(projected.workflow_revision,4);assert.equal(projected.facts_are_not_permission,true)
})

test('context hook fails closed when capture or prompt context is unavailable', async () => {
  const context = {session:{key:'session'},signal:new AbortController().signal,log:{warn(){}}}
  for (const failed of ['native-messages','prompt-context']) {
    const calls=[]
    await assert.rejects(promptContext(async operation => {
      calls.push(operation);return {ok:operation!==failed,result:{}}
    },async()=>[],context), /capture rejected|context unavailable/)
    assert.deepEqual(calls,failed==='native-messages'?['native-messages']:['native-messages','prompt-context'])
  }
})

test('management services satisfy official Surface names and both package allow-lists', () => {
  const manifest = JSON.parse(readFileSync(new URL('../../integrations/agh/package.json', import.meta.url)))
  const descriptor = JSON.parse(readFileSync(new URL('../../integrations/agh/extensions/main/agnes.client.json', import.meta.url)))
  const declared = manifest.agnes.plugins[0].services
  assert.deepEqual(new Set(descriptor.client.services), new Set(declared))
  for (const name of declared) assert.match(name, /^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*)*$/)
  for (const name of ['nw.workspace.query', 'nw.workspace.edit', 'nw.session.removalcheck','nw.action.submit','nw.action.status']) assert(declared.includes(name))
})
test('server tools keep action scope fingerprints and suppress repeated approvals', async () => {
  const calls = []
  const tools = serverTools(async (...args) => { calls.push(args); return { ok: true, result: {} } }, contracts)
  assert(tools.length > 30)
  assert(tools.some(t => t.name === 'nw_list_skill_methods'))
  assert(tools.some(t => t.name === 'nw_inventory_data_files'))
  const execute = tools.find(t => t.name === 'nw_apply_action')
  assert.equal(execute.meta.requiresApproval, 'never')
  assert(tools.every(tool=>tool.meta.requiresApproval==='never'))
  const first = execute.classify({ action_id: 'action_'+'a'.repeat(32), action_digest: 'b'.repeat(64) })
  const second = execute.classify({ action_id: 'action_'+'c'.repeat(32), action_digest: 'b'.repeat(64) })
  assert.notDeepEqual(first.approvalScopes, second.approvalScopes)
  assert.equal(first.requiresApproval, 'never')
  assert(first.approvalScopes.every(scope => /^[A-Za-z][A-Za-z0-9_.:-]{0,63}$/.test(scope)))
  await execute.execute({ action_id: 'action_'+'a'.repeat(32), action_digest: 'b'.repeat(64), reviewed_scope: {} },
    { session: { key: 'host-session', toolUseId: 'host-tool' } })
  assert.equal(calls[0][2], 'host-session')
  assert.equal(calls[0][3], 'host-tool')
})
test('nested contracts retain TypeBox kinds and forbid authority fields', () => {
  const schema = typebox(contracts.workflow, contracts.workflow.$defs)
  assert.equal(schema[Symbol.for('TypeBox.Kind')], 'Object')
  assert.equal(schema.additionalProperties, false)
  assert(!Object.hasOwn(schema.properties, 'actor'))
  for (const field of ['contract', 'plan', 'bindings', 'steps']) assert(!Object.hasOwn(schema.properties, field))
  assert(schema.required.includes('route_id'))
  assert.equal(schema.properties.route_id[Symbol.for('TypeBox.Kind')], 'String')
})

test('3003 route planning and scoped shared facts are exposed without execution authority', () => {
  const tools = serverTools(() => {}, contracts)
  const route = tools.find(tool => tool.name === 'nw_method_routes')
  assert(route.parameters.properties.targets)
  assert(!Object.hasOwn(route.parameters.properties,'task_contract'))
  for (const field of ['goal_artifact_types','required_output_types','required_method_ids','goal_output_refs','required_output_refs','retrieval_queries']) {
    assert(!Object.hasOwn(contracts.goal.$defs.TaskContract.properties,field),field)
  }
  const facts = tools.find(tool => tool.name === 'nw_shared_facts')
  assert.equal(route.meta.isReadOnly, true)
  assert.equal(route.meta.requiresApproval, 'never')
  assert(!Object.hasOwn(route.parameters.properties, 'existing_inputs'))
  assert.equal(facts.parameters.additionalProperties, false)
  assert(!Object.hasOwn(facts.parameters.properties, 'conversation_id'))
  const draft = tools.find(tool => tool.name === 'nw_prepare_workflow')
  assert.equal(draft.meta.requiresApproval, 'never')
  assert.equal(draft.meta.isReadOnly, false)
  assert.equal(draft.meta.isConcurrencySafe, false)
  assert.equal(draft.meta.replay, 'never')
  assert.equal(tools.find(tool => tool.name === 'nw_prepare_execution').meta.requiresApproval, 'never')
  for (const name of ['nw_update_task_state','nw_finish_task']) {
    const tool = tools.find(item => item.name === name)
    assert.equal(tool.meta.requiresApproval,'never')
    assert.equal(tool.meta.isReadOnly,false)
    assert.equal(tool.meta.replay,'never')
  }
})
