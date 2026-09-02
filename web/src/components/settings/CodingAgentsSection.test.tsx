/**
 * 设置 → 编码 Agent 的一级页面。
 *
 * 盯三件事：① 探测中绝不先甩「未安装」；② 列表完全按后端返回的 `agents[]`
 * 走（顺序、显示名、状态一个都不在前端硬编码）；③ 一级页面里没有路径输入框、
 * 没有 Base URL / 密钥 / wire api。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchAiCapabilities: vi.fn(),
  patchAiAgent: vi.fn(),
}))

import { fetchAiCapabilities, patchAiAgent, type AiCapabilities } from '@/lib/api'
import { SettingsDialog } from '@/components/SettingsDialog'
import { t } from '@/i18n'
import { useAiStore } from '@/store/aiStore'
import { useUiStore } from '@/store/uiStore'

// Radix 的 Select 打开时会 scrollIntoView；jsdom 没有这个方法
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
import { agentCaps, capsOf, claudeCaps } from './testCaps'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const fetchMock = vi.mocked(fetchAiCapabilities)
const patchMock = vi.mocked(patchAiAgent)

const ag = (key: string, v?: Record<string, unknown>) =>
  t(`settings.agents.${key}`, { ns: 'dialogs', ...(v ?? {}) })

let host: HTMLDivElement
let root: Root

async function open(initial: AiCapabilities | null = capsOf([agentCaps(), claudeCaps()])) {
  if (initial) fetchMock.mockResolvedValue(initial)
  else fetchMock.mockRejectedValue(new Error('offline'))
  useAiStore.setState({ caps: null })   // agent（用户首选）由各用例自己设
  useUiStore.setState({ settingsOpen: true, settingsSection: 'ai' })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<SettingsDialog />)
  })
  await act(async () => {})
}

const text = () => document.body.textContent ?? ''
const buttons = () => [...document.querySelectorAll('button')] as HTMLButtonElement[]
const byName = (name: string | RegExp) =>
  buttons().find((b) => {
    const label = b.getAttribute('aria-label') ?? b.textContent ?? ''
    return typeof name === 'string' ? label.includes(name) : name.test(label)
  })
const switches = () =>
  [...document.querySelectorAll('[role="switch"]')] as HTMLButtonElement[]

beforeEach(() => {
  fetchMock.mockReset()
  patchMock.mockReset()
})

afterEach(() => {
  act(() => root.unmount())
  host.remove()
  document.body.innerHTML = ''
  useAiStore.setState({ caps: null, agent: null })
  useUiStore.setState({ settingsOpen: false, settingsSection: null })
})

describe('编码 Agent 一级页面', () => {
  it('导航项叫「编码 Agent」，不再叫 AI', async () => {
    await open()
    const nav = document.querySelector('nav')!
    expect(nav.textContent).toContain(ag('title'))
  })

  it('初次加载显示「正在检测」，绝不先说「未安装」', async () => {
    // fetch 挂着不 resolve = 一直停在探测中
    fetchMock.mockImplementation(() => new Promise(() => {}))
    useAiStore.setState({ caps: null })
    useUiStore.setState({ settingsOpen: true, settingsSection: 'ai' })
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
    await act(async () => {
      root.render(<SettingsDialog />)
    })
    expect(text()).toContain(ag('state.detecting'))
    expect(text()).not.toContain(ag('state.not_installed'))
    expect(text()).not.toContain(ag('state.broken'))
  })

  it('列表完全跟着后端的 agents[] 走：顺序、显示名、状态', async () => {
    await open(capsOf([claudeCaps({ state: 'not_installed', installed: false, usable: false }),
                       agentCaps()]))
    const items = [...document.querySelectorAll('li')].map((li) => li.textContent ?? '')
    const claudeAt = items.findIndex((x) => x.includes('Claude Code'))
    const codexAt = items.findIndex((x) => x.includes('Codex'))
    expect(claudeAt).toBeGreaterThanOrEqual(0)
    expect(claudeAt).toBeLessThan(codexAt)          // 顺序由 API 决定
    expect(text()).toContain(ag('state.ready'))
    expect(text()).toContain(ag('state.not_installed'))
  })

  it('未安装用中性状态，不是错误语义', async () => {
    await open(capsOf([agentCaps({ state: 'not_installed', installed: false, usable: false })]))
    const row = [...document.querySelectorAll('li')].find((li) =>
      li.textContent?.includes(ag('state.not_installed')),
    )!
    expect(row.querySelector('.text-danger')).toBeNull()
    expect(row.textContent).toContain(ag('subtitle.notInstalled', { product: 'Tavotto' }))
  })

  it('broken 与 not_installed 分开表达', async () => {
    await open(capsOf([agentCaps({ state: 'broken', installed: false, usable: false })]))
    expect(text()).toContain(ag('state.broken'))
    expect(text()).not.toContain(ag('state.not_installed'))
    expect(document.querySelector('.text-danger')).not.toBeNull()
  })

  it('一级页面每行只有名称 · 版本号 · 状态：没有路径、没有内部包名、没有说明段（ADR 0038）', async () => {
    await open()
    const list = document.querySelector('ul.overflow-hidden')!
    expect(list.textContent).toContain('Codex')
    expect(list.textContent).toContain('1.2.3')
    expect(list.textContent).not.toContain('codex-cli')           // 内部包名
    expect(list.textContent).not.toContain('/opt/homebrew/bin')    // 安装目录
    expect(text()).not.toContain('自动发现本机已经安装的编码 Agent')  // 长说明
    // 反方向那一节没有卡片外框、没有说明段：只有名字 + 外链
    expect(text()).toContain('Tavotto for Codex')
    expect(text()).not.toContain('在 Codex 会话中打开、编辑并导出科研图')
    expect(document.querySelectorAll('.rounded-md.border.border-border.bg-surface.p-3').length).toBe(0)
  })

  it('未安装的行说下一步；装坏了的行说清是坏了（第二行只在这两种情况出现）', async () => {
    await open(capsOf([agentCaps({ installed: false, state: 'not_installed', version: null, executable_path: null }), claudeCaps({ state: 'broken', installed: false })]))
    expect(text()).toContain(ag('subtitle.notInstalled', { product: 'Tavotto' }))
    expect(text()).toContain(ag('subtitle.broken'))
  })

  it('详情里路径与诊断可复制', async () => {
    await open()
    await act(async () => byName(ag('rowAria', { name: 'Codex' }))!.click())
    expect(byName(ag('detail.copyPath'))).toBeTruthy()
    // 高级设置默认折叠，展开诊断之后才有「复制诊断信息」
    const details = [...document.querySelectorAll('details')] as HTMLDetailsElement[]
    for (const d of details) d.open = true
    await act(async () => {})
    expect(byName(ag('detail.copyDiagnostics'))).toBeTruthy()
  })

  it('一级页面没有任何路径输入框，也没有 Base URL / 密钥 / 协议', async () => {
    await open()
    expect(document.querySelectorAll('input[type="text"], input[type="password"]').length).toBe(0)
    for (const key of ['detail.customPath', 'endpoint.baseUrl', 'endpoint.apiKey', 'endpoint.wire']) {
      expect(text()).not.toContain(ag(key))
    }
  })

  it('开关与「进详情」互不干扰，且没有嵌套 button', async () => {
    await open()
    patchMock.mockResolvedValue(capsOf([agentCaps({ enabled: false, state: 'disabled', usable: false }),
                                        claudeCaps()]))
    fetchMock.mockResolvedValue(capsOf([agentCaps({ enabled: false, state: 'disabled', usable: false }),
                                        claudeCaps()]))
    // 嵌套交互元素：任何 button 里都不该再有 button
    for (const b of buttons()) expect(b.querySelector('button')).toBeNull()

    const toggle = switches()[0]
    await act(async () => toggle.click())
    expect(patchMock).toHaveBeenCalledWith('codex', { enabled: false })
    // 点开关不该顺手打开详情
    expect(text()).not.toContain(ag('detail.diagnostics'))
  })

  it('点行主体进详情，返回还在列表', async () => {
    await open()
    const row = byName(ag('rowAria', { name: 'Codex' }))!
    await act(async () => row.click())
    expect(text()).toContain(ag('detail.overview'))
    expect(text()).toContain('/opt/homebrew/bin/codex')
    const back = byName(ag('backAria'))!
    await act(async () => back.click())
    expect(text()).toContain(ag('useInProduct', { product: 'Tavotto' }))
  })

  it('开关的 aria-label 说清是「在 Tavotto 中启用它」', async () => {
    await open()
    expect(switches()[0].getAttribute('aria-label')).toBe(
      ag('toggleAria', { name: 'Codex', product: 'Tavotto' }),
    )
  })

  it('未安装 / 装坏了时开关禁用', async () => {
    await open(capsOf([agentCaps({ state: 'not_installed', installed: false, usable: false })]))
    expect(switches()[0].disabled).toBe(true)
  })

  it('默认 Agent 只列可用的；只有一个时不画下拉框', async () => {
    await open(capsOf([agentCaps(), claudeCaps({ state: 'disabled', enabled: false, usable: false })]))
    expect(document.querySelector(`select[aria-label="${ag('defaultAgentAria')}"]`)).toBeNull()
    expect(text()).toContain(ag('defaultAgent'))
  })

  /** 打开默认 Agent 下拉（`ui/Select` 是 Radix，选项在 portal 里，要先点开） */
  const openDefaultAgentSelect = async (): Promise<HTMLElement[]> => {
    const trigger = document.querySelector(
      `[role="combobox"][aria-label="${ag('defaultAgentAria')}"]`,
    ) as HTMLElement
    expect(trigger, '默认 Agent 下拉不见了（原生 select 换成 ui/Select 之后是 combobox）').toBeTruthy()
    await act(async () => {
      trigger.click()
    })
    return [...document.body.querySelectorAll('[role="option"]')] as HTMLElement[]
  }

  it('两个都可用时给下拉框，且只列可用的', async () => {
    await open(capsOf([agentCaps(), claudeCaps()]))
    const options = await openDefaultAgentSelect()
    expect(options.map((o) => o.textContent?.trim())).toEqual(['Codex', 'Claude Code'])
  })

  it('下拉里选另一个 Agent：真的写进 store（迁移到 ui/Select 之后的交互覆盖）', async () => {
    await open(capsOf([agentCaps(), claudeCaps()]))
    const options = await openDefaultAgentSelect()
    await act(async () => {
      options[1].click()
    })
    expect(useAiStore.getState().agent).toBe('claude')
  })

  it('首选那个不可用时自动落到第一个可用的，但不改用户存着的首选值', async () => {
    useAiStore.setState({ agent: 'claude' })
    await open(capsOf([agentCaps(), claudeCaps({ state: 'needs_auth', usable: false })]))
    expect(useAiStore.getState().agent).toBe('claude')   // 首选值原样留着
    expect(text()).toContain('Codex')                    // 实际默认落到可用的那个
  })

  it('localStorage 里存了不存在的 Agent 也不崩，回退到第一个可用的', async () => {
    useAiStore.setState({ agent: 'opencode' })
    await open()
    expect(text()).toContain(ag('defaultAgent'))
    expect(useAiStore.getState().agent).toBe('opencode')
  })

  it('刷新失败保留上一次结果，并给一条非破坏性提示', async () => {
    await open()
    expect(text()).toContain('1.2.3')
    fetchMock.mockRejectedValue(new Error('boom'))
    const rescan = byName(ag('rescan'))!
    await act(async () => rescan.click())
    await act(async () => {})
    expect(text()).toContain(ag('refreshFailed'))
    expect(text()).toContain('1.2.3')                     // 旧结果还在
    expect(text()).not.toContain(ag('state.not_installed'))
  })

  it('检测结果有 aria-live 播报', async () => {
    await open()
    const live = document.querySelector('[aria-live="polite"]')
    expect(live?.textContent).toBe(ag('announce.done'))
  })

  it('两个方向分成两个小节，且不混为一谈', async () => {
    await open()
    expect(text()).toContain(ag('useInProduct', { product: 'Tavotto' }))
    expect(text()).toContain(ag('useFromAgents', { product: 'Tavotto' }))
    expect(text()).toContain(ag('codexIntegrationName', { product: 'Tavotto' }))
    // 「本机装了 codex CLI」绝不写成「Tavotto for Codex 已安装」
    const link = [...document.querySelectorAll('a')].find((a) =>
      a.textContent?.includes(ag('viewGuide')),
    )!
    expect(link.getAttribute('href')).toContain('github.com/Tavotto/Tavotto')
  })

  it('没有可用 Agent 时说清楚，并且不谎报', async () => {
    await open(capsOf([agentCaps({ state: 'not_installed', installed: false, usable: false })]))
    expect(text()).toContain(ag('noUsableAgent'))
  })

  it('最近检测时间用统一的日期格式', async () => {
    await open()
    expect(text()).toContain(ag('lastChecked', { time: '' }).trim().split('{')[0].trim())
  })
})
