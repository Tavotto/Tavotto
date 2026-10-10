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
import { TooltipProvider } from '@/components/ui/Tooltip'

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
    root.render(
      <TooltipProvider>
        <SettingsDialog />
      </TooltipProvider>,
    )
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

/** 展开详情页的一个折叠块（锚点 `data-agent-fold`；收起时内容不挂载） */
const openFold = (name: string) =>
  document
    .querySelector<HTMLButtonElement>(`[data-agent-fold="${name}"] > div > button`)!
    .click()

describe('编码 Agent 一级页面', () => {
  it('导航项叫「改图助手」，不再叫 AI', async () => {
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
      root.render(
      <TooltipProvider>
        <SettingsDialog />
      </TooltipProvider>,
    )
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
    expect(row.querySelector('.text-danger, [data-status-pill="danger"]')).toBeNull()
    expect(row.querySelector('[data-agent-state]')!.getAttribute('data-status-pill')).toBe('neutral')
    expect(row.textContent).toContain(ag('subtitle.notInstalled', { product: 'Tavotto' }))
  })

  it('broken 与 not_installed 分开表达', async () => {
    await open(capsOf([agentCaps({ state: 'broken', installed: false, usable: false })]))
    expect(text()).toContain(ag('state.broken'))
    expect(text()).not.toContain(ag('state.not_installed'))
    // 坏了是危险语气的状态胶囊；未安装是中性（见上一条）
    expect(document.querySelector('[data-agent-state="broken"]')!.getAttribute('data-status-pill')).toBe('danger')
  })

  it('一级页面每行只有名称 · 状态：没有版本、没有路径、没有说明段（ADR 0038；审计 T44）', async () => {
    await open()
    // 清单自 2026-09-15 全面打磨 D11 起不带外框：按它所在的小节定位，不按边框的类名
    const list = document.querySelector('[data-agent-section="in-app"] ul')!
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
    // 高级设置默认折叠，而折叠着的内容**根本不在 DOM 里**（全面打磨 D05 之后是
    // `DiagnosticDisclosure` + `Reveal`，不再是 `<details>`）：要读里面的东西得真的点开
    await act(async () => openFold('overview'))
    expect(byName(ag('detail.copyPath'))).toBeTruthy()
    await act(async () => openFold('diagnostics'))
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
    await act(async () => openFold('overview'))
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

  /**
   * 默认助手是组首**一个 Select**（2026-10-07 设计审计 §9.1，用户拍板；此前 2026-09-14 审计 D1 是每行行首一颗
   * 单选——一行里单选 + 开关 + 整行 + chevron 四种操作）。选项只有此刻可用的那几个，触发器上是实际会派给的那一个。
   */
  const defaultSelect = () =>
    document.querySelector<HTMLElement>('[data-agent-default] [role="combobox"]')
  const defaultOptions = async () => {
    await act(async () => defaultSelect()!.click())
    return [...document.querySelectorAll<HTMLElement>('[role="option"]')]
  }

  it('组首一个默认助手下拉：当前默认是它的值，选项只有可用的；行里不再有单选', async () => {
    await open(capsOf([agentCaps(), claudeCaps({ state: 'disabled', enabled: false, usable: false })]))
    expect(defaultSelect(), '没有默认助手下拉').toBeTruthy()
    expect(defaultSelect()!.textContent).toContain(agentCaps().display_name)
    expect(document.querySelectorAll('input[type="radio"]'), '行里不再有单选').toHaveLength(0)
    const options = await defaultOptions()
    expect(options.map((o) => o.textContent)).toEqual([agentCaps().display_name])
  })

  it('选另一个：真的写进 store，下拉跟着换', async () => {
    await open(capsOf([agentCaps(), claudeCaps()]))
    const options = await defaultOptions()
    const claude = options.find((o) => o.textContent === claudeCaps().display_name)!
    await act(async () => claude.click())
    expect(useAiStore.getState().agent).toBe('claude')
    expect(defaultSelect()!.textContent).toContain(claudeCaps().display_name)
  })

  it('首选那个不可用时显示第一个可用的，但不改用户存着的首选值', async () => {
    useAiStore.setState({ agent: 'claude' })
    await open(capsOf([agentCaps(), claudeCaps({ state: 'needs_auth', usable: false })]))
    expect(useAiStore.getState().agent).toBe('claude')   // 首选值原样留着
    expect(defaultSelect()!.textContent).toContain(agentCaps().display_name)
  })

  it('localStorage 里存了不存在的 Agent 也不崩，下拉回退到第一个可用的', async () => {
    useAiStore.setState({ agent: 'opencode' })
    await open()
    expect(defaultSelect()!.textContent).toContain(agentCaps().display_name)
    expect(useAiStore.getState().agent).toBe('opencode')
  })

  it('一个可用的都没有：下拉停用，并就近说清', async () => {
    await open(capsOf([agentCaps({ state: 'not_installed', installed: false, usable: false })]))
    expect((defaultSelect() as HTMLButtonElement).disabled).toBe(true)
    expect(text()).toContain(ag('noUsableAgent'))
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
      // 「重新诊断」是排障用的次级出口，在那一行的 ⋯ 里（2026-10-07 设计审计 §9.1）
      expect(document.querySelector('[data-agent-codex-integration] [data-codex-more]')).toBeTruthy()
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
  it('未安装 / 需要登录 / 可用是三句不同的话、三种不同的语气；「可用」也有字', async () => {
    const labels = ['not_installed', 'needs_auth', 'ready'].map((s) => ag(`state.${s}`))
    expect(new Set(labels).size).toBe(3)
    await open(
      capsOf([
        agentCaps({ state: 'needs_auth', usable: false }),
        claudeCaps({ installed: false, state: 'not_installed', usable: false, version: null, executable_path: null }),
      ]),
    )
    const rows = [...document.querySelectorAll('[data-agent-section="in-app"] ul li')]
    expect(rows[0].textContent).toContain(ag('state.needs_auth'))
    expect(rows[1].textContent).toContain(ag('state.not_installed'))
    // 状态一律是带字的 StatusPill（2026-10-07 设计审计 §9.1）：等级不只靠颜色——判据是那句话，
    // 语气（锚点派生的底与字）只是佐证
    const badgeOf = (r: Element) => r.querySelector('[data-agent-state]')!
    expect(badgeOf(rows[0]).getAttribute('data-status-pill')).toBe('warn')
    expect(badgeOf(rows[1]).getAttribute('data-status-pill')).toBe('neutral')
  })

  it('「可用」不再是一颗只有颜色的绿点：字是看得见的，不是 sr-only', async () => {
    await open()
    const ready = document.querySelector('[data-agent-state="ready"]')!
    expect(ready.getAttribute('data-status-pill')).toBe('ok')
    expect(ready.querySelector('.sr-only')).toBeNull()
    expect(ready.textContent).toContain(ag('state.ready'))
  })

})
