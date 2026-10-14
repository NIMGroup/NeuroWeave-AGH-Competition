import { serverRuntime } from './server.mjs'

export const neuroWeave = {
  inject: ['extension', 'skills'],
  apply(ctx) {
    if (process.env.NEUROWEAVE_AGH_SERVER !== 'true')
      throw new Error('Use the dedicated NeuroWeave AGH server profile and launchers')
    return serverRuntime.apply(ctx)
  },
}

// Profile ID is retained so existing sessions continue using their installed preset.
export const presets = {
  'neuroweave-roi': {
    name: 'neuroweave-roi', extends: 'base', surfaces: ['cli', 'sdk', 'daemon'], disclosure: 'standard',
    model: { max_tokens: 4096, prompt_sections: [], route: Object.fromEntries(['primary', 'escalation', 'fast', 'compaction', 'verifier'].map(k => [k, { route: 'default', model: 'default' }])) },
    tools: { core: [], timeout_ms: 60000 }, skills_roots: [],
    budget: { max_steps: 60 }, compaction: { enabled: true, agent_callable: false },
    harness: { auto_refine: { enabled: false } },
    approval: { command_policy: [], on_unavailable: 'deny' },
  },
}
