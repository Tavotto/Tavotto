import { beforeEach, describe, expect, it } from 'vitest'
import { prunePrefs, useAiStore, type AiAgent } from './aiStore'

const LS_PREFS = 'magplot.ai.prefs'

/** 本机 CLI 能力：codex 只认当前配置里的模型，claude 不给强度开关 */
const CAPS = {
  providers: {
    codex: {
      installed: true, path: '/usr/bin/codex', version: 'codex-cli 0.147.0',
      models: ['gpt-5.6-luna'], default_model: 'gpt-5.6-luna',
      efforts: ['minimal', 'low', 'medium', 'high', 'max'], default_effort: 'max',
    },
    claude: {
      installed: true, path: '/usr/bin/claude', version: 'claude 2.0',
      models: ['sonnet', 'opus', 'haiku'], default_model: 'sonnet',
      efforts: [], default_effort: null,
    },
  },
}

globalThis.fetch = (async (url: unknown) => {
  if (String(url).includes('/api/ai/capabilities')) {
    return new Response(JSON.stringify(CAPS), { status: 200 })
  }
  return new Response('{}', { status: 404 })
}) as typeof fetch

describe('prunePrefs', () => {
  const allowed = (a: AiAgent) => (a === 'codex' ? ['gpt-5.6-luna'] : ['sonnet'])

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

  it('只清掉过期的那个 provider', () => {
    expect(prunePrefs({ codex: 'gpt-5', claude: 'sonnet' }, allowed))
      .toEqual({ claude: 'sonnet' })
  })
})

describe('loadCaps', () => {
  beforeEach(() => {
    localStorage.clear()
    useAiStore.setState({ caps: null, models: {}, efforts: {} })
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
})
