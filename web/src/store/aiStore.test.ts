import { beforeEach, describe, expect, it } from 'vitest'
import { agentById, effectiveAgent, usableAgents, type AiCapabilities } from '@/lib/api'
import { hasUsableAgent, prunePrefs, sessionAgentLabel, useAiStore } from './aiStore'

const LS_PREFS = 'tavotto.ai.prefs'

/** 后端返回的形状：顺序即界面顺序，能力由每个 Agent 自己声明 */
const CAPS: AiCapabilities = {
  agents: [
    {
      id: 'codex', display_name: 'Codex', icon_key: 'codex',
      state: 'ready', installed: true, enabled: true, usable: true,
      version: 'codex-cli 0.147.0', executable_path: '/usr/bin/codex',
      path_override: null, detection_source: 'path',
      models: ['gpt-5.6-luna'], default_model: 'gpt-5.6-luna',
      efforts: ['minimal', 'low', 'medium', 'high', 'max'], default_effort: 'max',
      endpoint: null, active_endpoint_id: null,
      features: { third_party_endpoints: true, model_selection: true,
                  effort_selection: true, wire_api_selection: true,
                  readiness_probe: true },
      diagnostics: { searched: [], broken_path: null, readiness: 'ready',
                     readiness_detail: null },
    },
    {
      id: 'claude', display_name: 'Claude Code', icon_key: 'claude',
      state: 'ready', installed: true, enabled: true, usable: true,
      version: 'claude 2.0', executable_path: '/usr/bin/claude',
      path_override: null, detection_source: 'path',
      models: ['sonnet', 'opus', 'haiku'], default_model: 'sonnet',
      efforts: [], default_effort: null,
      endpoint: null, active_endpoint_id: null,
      features: { third_party_endpoints: true, model_selection: true,
                  effort_selection: false, wire_api_selection: false,
                  readiness_probe: true },
      diagnostics: { searched: [], broken_path: null, readiness: 'ready',
                     readiness_detail: null },
    },
  ],
  endpoints: [],
  presets: [],
  checked_at_ms: 1_756_000_000_000,
}

const withAgents = (patch: Record<string, Partial<AiCapabilities['agents'][number]>>): AiCapabilities => ({
  ...CAPS,
  agents: CAPS.agents.map((a) => ({ ...a, ...(patch[a.id] ?? {}) })),
})

let served: AiCapabilities = CAPS
globalThis.fetch = (async (url: unknown) => {
  if (String(url).includes('/api/ai/capabilities')) {
    return new Response(JSON.stringify(served), { status: 200 })
  }
  return new Response('{}', { status: 404 })
}) as typeof fetch

describe('prunePrefs', () => {
  const allowed = (a: string) => (a === 'codex' ? ['gpt-5.6-luna'] : ['sonnet'])

  it('丢弃已经不在清单里的选择', () => {
    expect(prunePrefs({ codex: 'gpt-5' }, allowed)).toEqual({})
  })

  it('保留仍然有效的选择，并原样返回对象（不触发无谓的落盘）', () => {
    const prefs = { codex: 'gpt-5.6-luna', claude: 'sonnet' }
    expect(prunePrefs(prefs, allowed)).toBe(prefs)
  })

  it('清单为空时不动用户的选择（后端没给可选项 = 跟随 CLI 默认）', () => {
    const prefs = { codex: 'gpt-5' }
    expect(prunePrefs(prefs, () => [])).toBe(prefs)
  })

  it('对任意 Agent id 都工作，不只认两个写死的名字', () => {
    expect(prunePrefs({ opencode: 'x', codex: 'gpt-5.6-luna' },
                      (a) => (a === 'codex' ? ['gpt-5.6-luna'] : ['y'])))
      .toEqual({ codex: 'gpt-5.6-luna' })
  })
})

describe('capabilities 助手', () => {
  it('agentById / usableAgents 按后端顺序返回', () => {
    expect(agentById(CAPS, 'claude')?.display_name).toBe('Claude Code')
    expect(agentById(CAPS, 'nope')).toBeNull()
    expect(usableAgents(CAPS).map((a) => a.id)).toEqual(['codex', 'claude'])
  })

  it('disabled / broken / not installed / needs auth 都不进可选列表', () => {
    for (const state of ['disabled', 'broken', 'not_installed', 'needs_auth'] as const) {
      const caps = withAgents({ codex: { state, usable: false } })
      expect(usableAgents(caps).map((a) => a.id)).toEqual(['claude'])
    }
  })

  it('effectiveAgent：首选可用就用它，否则落到第一个可用的', () => {
    expect(effectiveAgent('claude', CAPS)).toBe('claude')
    expect(effectiveAgent('claude', withAgents({ claude: { usable: false } }))).toBe('codex')
    expect(effectiveAgent('opencode', CAPS)).toBe('codex')     // 存了个不存在的
    expect(effectiveAgent('codex', withAgents({
      codex: { usable: false }, claude: { usable: false },
    }))).toBeNull()
    expect(effectiveAgent('codex', null)).toBeNull()
  })

  it('hasUsableAgent 说了 AI 面板该给选择器还是给设置入口', () => {
    expect(hasUsableAgent(CAPS)).toBe(true)
    expect(hasUsableAgent(withAgents({ codex: { usable: false }, claude: { usable: false } })))
      .toBe(false)
    expect(hasUsableAgent(null)).toBe(false)
  })

  it('历史标签在 capabilities 不可用时回退到会话里的快照', () => {
    const session = { agent: 'codex', agentLabel: 'Codex' }
    expect(sessionAgentLabel(CAPS, session)).toBe('Codex')
    expect(sessionAgentLabel(null, session)).toBe('Codex')     // 不会变成空白
    expect(sessionAgentLabel(null, { agent: 'gone', agentLabel: '' })).toBe('gone')
  })
})

describe('loadCaps', () => {
  beforeEach(() => {
    served = CAPS
    localStorage.clear()
    useAiStore.setState({ caps: null, models: {}, efforts: {}, agent: null })
  })

  it('探测到新能力后，清掉记忆里已失效的模型', async () => {
    // CLI 换代前存下的选择：现在服务端会回 400 拒收
    useAiStore.setState({ models: { codex: 'gpt-5' }, efforts: { codex: 'medium' } })
    localStorage.setItem(LS_PREFS,
      JSON.stringify({ models: { codex: 'gpt-5' }, efforts: { codex: 'medium' } }))

    await useAiStore.getState().loadCaps(true)

    const s = useAiStore.getState()
    expect(s.models.codex).toBeUndefined()          // 回落到 default_model
    expect(s.efforts.codex).toBe('medium')          // medium 仍在清单里，留着
    expect(JSON.parse(localStorage.getItem(LS_PREFS)!).models).toEqual({})
  })

  it('有效的选择不被动到', async () => {
    useAiStore.setState({ models: { codex: 'gpt-5.6-luna' } })
    await useAiStore.getState().loadCaps(true)
    expect(useAiStore.getState().models.codex).toBe('gpt-5.6-luna')
  })

  it('首选 Agent 暂时不可用时**不改**用户存着的值', async () => {
    useAiStore.setState({ agent: 'claude' })
    served = withAgents({ claude: { state: 'disabled', enabled: false, usable: false } })
    await useAiStore.getState().loadCaps(true)
    expect(useAiStore.getState().agent).toBe('claude')
    // 实际派活的是第一个可用的
    expect(effectiveAgent('claude', useAiStore.getState().caps)).toBe('codex')
  })

  it('探测失败向上抛，caps 保留上一次成功的结果', async () => {
    await useAiStore.getState().loadCaps(true)
    const before = useAiStore.getState().caps
    const ok = globalThis.fetch
    globalThis.fetch = (async () => new Response('nope', { status: 500 })) as typeof fetch
    await expect(useAiStore.getState().loadCaps(true)).rejects.toBeTruthy()
    expect(useAiStore.getState().caps).toBe(before)
    globalThis.fetch = ok
  })

  it('记住的首选是任意字符串，不再被联合类型挡掉', () => {
    localStorage.setItem('tavotto.ai.agent', 'opencode')
    // readAgent 在 store 建立时求值，这里直接验 setAgent 的往返
    useAiStore.getState().setAgent('opencode')
    expect(localStorage.getItem('tavotto.ai.agent')).toBe('opencode')
    expect(useAiStore.getState().agent).toBe('opencode')
  })
})
