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

  it('一级页面每行只有名称 · 状态：没有版本、没有路径、没有说明段（ADR 0038；审计 T44）', async () => {
    await open()
    const list = document.querySelector('ul.overflow-hidden')!
    expect(list.textContent).toContain('Codex')
    expect(list.textContent).not.toContain('1.2.3')                // 版本号归详情
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
    expect(text()).toContain(ag('useInProduct'))
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

  /** 每行里的「默认」按钮（2026-09-11 起不再有单独的下拉框） */
  const defaultButtons = () =>
    [...document.querySelectorAll<HTMLButtonElement>('button')].filter(
      (b) => b.textContent?.trim() === ag('defaultButton'),
    )

  it('每行自带「默认」按钮：当前默认的是按下态，不可用的按不了', async () => {
    await open(capsOf([agentCaps(), claudeCaps({ state: 'disabled', enabled: false, usable: false })]))
    const buttons = defaultButtons()
    expect(buttons).toHaveLength(2)
    expect(buttons.map((b) => b.getAttribute('aria-pressed'))).toEqual(['true', 'false'])
    expect(buttons[1].disabled).toBe(true)
    expect(document.querySelector('[role="combobox"]'), '不再有默认 Agent 下拉框').toBeNull()
  })

  it('按另一行的「默认」：真的写进 store，按下态跟着换', async () => {
    await open(capsOf([agentCaps(), claudeCaps()]))
    const before = defaultButtons()
    expect(before.map((b) => b.getAttribute('aria-pressed'))).toEqual(['true', 'false'])
    await act(async () => {
      before[1].click()
    })
    expect(useAiStore.getState().agent).toBe('claude')
    expect(defaultButtons().map((b) => b.getAttribute('aria-pressed'))).toEqual(['false', 'true'])
  })

  it('首选那个不可用时按下态落到第一个可用的，但不改用户存着的首选值', async () => {
    useAiStore.setState({ agent: 'claude' })
    await open(capsOf([agentCaps(), claudeCaps({ state: 'needs_auth', usable: false })]))
    expect(useAiStore.getState().agent).toBe('claude')   // 首选值原样留着
    expect(defaultButtons().map((b) => b.getAttribute('aria-pressed'))).toEqual(['true', 'false'])
  })

  it('localStorage 里存了不存在的 Agent 也不崩，按下态回退到第一个可用的', async () => {
    useAiStore.setState({ agent: 'opencode' })
    await open()
    expect(defaultButtons()[0].getAttribute('aria-pressed')).toBe('true')
    expect(useAiStore.getState().agent).toBe('opencode')
  })

  it('刷新失败保留上一次结果，并给一条非破坏性提示', async () => {
    await open()
    // 「上一次的结果」在列表上就是那两条状态（版本号已归详情，审计 T44）
    expect(text()).toContain(ag('state.ready'))
    fetchMock.mockRejectedValue(new Error('boom'))
    const rescan = byName(ag('rescan'))!
    await act(async () => rescan.click())
    await act(async () => {})
    expect(text()).toContain(ag('refreshFailed'))
    expect(text()).toContain(ag('state.ready'))           // 旧结果还在
    expect(text()).not.toContain(ag('state.not_installed'))
  })

  it('检测结果有 aria-live 播报', async () => {
    await open()
    const live = document.querySelector('[aria-live="polite"]')
    expect(live?.textContent).toBe(ag('announce.done'))
  })

  it('两个方向分成两个小节，且不混为一谈', async () => {
    await open()
    expect(text()).toContain(ag('useInProduct'))
    expect(text()).toContain(ag('useFromAgents'))
    // 两个小标题不许互为镜像：只差语序时读者得逐字比对才分得清（审计 T44）。
    // 判据是「一句不是另一句的重排」——把两串字符排序后必须不同。
    const sorted = (s: string) => [...s.replace(/\s/g, '')].sort().join('')
    expect(sorted(ag('useInProduct'))).not.toBe(sorted(ag('useFromAgents')))
    expect(text()).toContain(ag('codexIntegrationName', { product: 'Tavotto' }))
    // 「本机装了 codex CLI」绝不写成「Tavotto for Codex 已安装」
    const link = [...document.querySelectorAll('a')].find((a) =>
      a.textContent?.includes(ag('viewGuide')),
    )!
    expect(link.getAttribute('href')).toContain('github.com/Tavotto/Tavotto')
  })

  it('桌面模式下这一节里有「安装 Codex 集成」的入口（issue #170）', async () => {
    // 入口只在桌面壳里出现——浏览器模式下没有 tavotto-cli 可 spawn。
    // **量的是设置页里真的渲染出来了**，组件自己的单测证明不了它被挂上去了。
    ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
    try {
      await open()
      expect(text()).toContain(ag('codexInstall.action'))
      expect(text()).toContain(ag('codexInstall.doctor'))
    } finally {
      delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__
    }
  })

  it('浏览器模式下那一节只有名字 + 指南，没有按不动的按钮', async () => {
    await open()
    expect(text()).not.toContain(ag('codexInstall.action'))
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

describe('列表与详情不重复（审计 T44）', () => {
  it('一行只有名称 + 状态 + 启用开关 + 进详情，版本号不在列表上', async () => {
    await open()
    const row = [...document.querySelectorAll('li')].find((li) =>
      li.textContent?.includes('Codex'),
    )!
    expect(row.textContent).toContain(ag('state.ready'))
    expect(row.textContent).not.toContain('1.2.3')
    expect(row.querySelector('[data-agent-version]')).toBeNull()
    // 配置入口与开关仍在
    expect(row.querySelector(`[aria-label="${ag('rowAria', { name: 'Codex' })}"]`)).toBeTruthy()
    expect(row.querySelector('[role="switch"], button[aria-checked]')).toBeTruthy()
  })

  it('版本号在详情里，而且说的是版本号不是内部包名', async () => {
    await open()
    await act(async () => {
      document
        .querySelector<HTMLElement>(`[aria-label="${ag('rowAria', { name: 'Codex' })}"]`)!
        .click()
    })
    expect(text()).toContain('1.2.3')
    expect(text()).not.toContain('codex-cli 1.2.3')
  })
})

describe('三种状态明确区分（审计 T44 验收）', () => {
  it('未安装 / 需要登录 / 可用是三句不同的话、三个不同的图标', async () => {
    const labels = ['not_installed', 'needs_auth', 'ready'].map((s) => ag(`state.${s}`))
    expect(new Set(labels).size).toBe(3)
    await open(capsOf([agentCaps({ state: 'needs_auth', usable: false }), claudeCaps({ installed: false, state: 'not_installed', usable: false, version: null, executable_path: null })]))
    const rows = [...document.querySelectorAll('ul.overflow-hidden li')]
    expect(rows[0].textContent).toContain(ag('state.needs_auth'))
    expect(rows[1].textContent).toContain(ag('state.not_installed'))
    // **形状也不同**：等级不只靠颜色（灰度屏与色觉障碍下同样读得出）。
    // 判据是两个图标的 lucide 类名不相等，不是"有图标"
    // 量的是**状态徽标里**那个图标：一行里还有 Agent 的品牌图标，
    // 不指名道姓就会量到它，而它每个 Agent 本来就不一样（恒真）
    const badgeOf = (r: Element) => r.querySelector('[data-agent-state]')!
    const iconOf = (r: Element) =>
      [...(badgeOf(r).querySelector('svg')?.classList ?? [])].find((c) => c.startsWith('lucide-'))
    expect(iconOf(rows[0])).toBeTruthy()
    expect(iconOf(rows[0])).not.toBe(iconOf(rows[1]))
    // 颜色也不同，但它只是佐证——上面那条才是判据
    expect(badgeOf(rows[0]).className).not.toBe(badgeOf(rows[1]).className)
  })
})
