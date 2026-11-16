/**
 * 主页 × 实验开关 `ai.taf.enabled`（「从数据到图」）：
 *  - 开关关着：现有用户看到的一切逐字不变——两版的 DOM 与合入前主干上取的摘要逐字相同；
 *  - 开关开着：老手版 = 品牌标 + 并排两张入口卡 + 灰字示例链接，没有叙事句；左卡点击选数据文件夹并进数据页（只做点击）；
 *  - 开关怎么拿：先按本机记的上一次画（不闪、不等后端），后台问一次、不一样才换；读不到当没变。
 */
import { createHash } from 'node:crypto'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ProjectStatus, RecentProject } from '@/lib/api'
import { ProjectPicker } from '@/components/ProjectPicker'
import { configureOnboardingPersistence, useOnboardingStore, type OnboardingStatus } from '@/store/onboardingStore'
import { useProjectStore } from '@/store/projectStore'
import { useDataFigureStore } from '@/store/dataFigureStore'
import { useTafFlagStore } from '@/store/tafFlagStore'
import '@/lib/dataFigureHost'
import { useAiStore } from '@/store/aiStore'
import { useTutorialStore } from '@/lib/onboarding/tutorial'
import { useUiStore } from '@/store/uiStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const desktop = vi.hoisted(() => ({
  isDesktop: vi.fn(() => true),
  pickDirectory: vi.fn(async (_title?: string): Promise<string | null> => '/Users/me/data-folder'),
  nativeFileDropAvailable: vi.fn(async () => false),
  onNativeFileDrop: vi.fn(async () => () => {}),
}))
vi.mock('@/lib/desktop', async (orig) => ({ ...(await orig<Record<string, unknown>>()), ...desktop }))
vi.mock('@/lib/onboarding/tutorial', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  runTutorialEntry: vi.fn(() => 'started'),
  openSampleProject: vi.fn(async () => ({ ok: true, kind: 'opened' })),
}))

let capsReply: 'on' | 'off' | 'fail' = 'fail'
globalThis.fetch = (async (url: unknown) => {
  const u = String(url)
  if (u.includes('/api/tutorial')) return new Response(JSON.stringify({ available: true, problems: [] }), { status: 200 })
  if (u.includes('/api/ai/capabilities')) {
    if (capsReply === 'fail') return new Response('{}', { status: 500 })
    return new Response(JSON.stringify({ agents: [], endpoints: [], presets: [], checked_at_ms: 0, taf_enabled: capsReply === 'on' }), { status: 200 })
  }
  return new Response('{}', { status: 404 })
}) as typeof fetch

const NOW = 1_800_000_000_000
const recent = (path: string): RecentProject => ({ path, name: path.split('/').pop()!, last_opened: NOW - 2 * 3600_000, exists: true, current: false })
const RECENT = [recent('/Users/me/Desktop/poster'), recent('/Users/me/Research/thesis')]

let root: Root
let host: HTMLDivElement
const open = vi.fn(async (_path: string, _create?: boolean, _entry?: unknown): Promise<ProjectStatus> => ({ open: true }))

async function mount(status: OnboardingStatus, flag: boolean) {
  useOnboardingStore.setState({ status })
  useTafFlagStore.setState({ enabled: flag, settled: true })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<ProjectPicker />)
  })
}
const q = <T extends Element = HTMLElement>(sel: string) => host.querySelector<T>(sel)
/** 渲染出来的主页，去掉随时间 / 随机变化的片段后的 HTML */
const html = () => q('main')!.outerHTML.replace(/\s(id|aria-describedby|aria-labelledby|for)="[^"]*"/g, '')

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(NOW)
  configureOnboardingPersistence(null)
  useOnboardingStore.getState().resetOnboarding()
  useTutorialStore.setState({ status: null, meta: null, busy: null, failure: null })
  useProjectStore.setState({ phase: 'none', project: null, recent: RECENT, opened: [], switching: false, open })
  useDataFigureStore.getState().clear()
  useUiStore.setState({ status: null })
  useAiStore.setState({ caps: null })
  open.mockClear()
  desktop.pickDirectory.mockClear()
  capsReply = 'fail'
  try { window.localStorage.removeItem('tavotto.taf-enabled') } catch { /* 没有存储也行 */ }
})
afterEach(() => {
  vi.useRealTimers()
  act(() => root.unmount())
  host.remove()
  document.body.innerHTML = ''
})

describe('开关关着：现有用户看到的一切逐字不变', () => {
  // 两个摘要是在**合入前的主干**（本 PR 动 HomeView / ProjectPicker 之前）上渲染同样的屏取的；开关关着时本 PR 的渲染必须与它逐字相同。
  // 摘要而不是整段 HTML 快照：src 下不放 .html（`pagePtLens` 的扩展名门禁），整段又太长；对不上时先看是不是有意改了首页。
  const BASE_DIGEST = { returning: '3c3be2879f5551c4', newcomer: '94356a7f49e3dbbe' } as const
  it.each([['returning', 'completed'], ['newcomer', 'not_started']] as const)('%s 版：DOM 与合入前主干逐字相同（摘要）', async (name, status) => {
    await mount(status, false)
    expect(createHash('sha256').update(html()).digest('hex').slice(0, 16)).toBe(BASE_DIGEST[name])
  })

  it('没有入口卡、叙事句与示例脚本按钮都在', async () => {
    await mount('completed', false)
    expect(q('[data-home-entry]')).toBeNull()
    expect(q('[data-home-narrative]')).not.toBeNull()
    expect(q('button[data-home-sample]')!.className).toContain('inline-flex') // 原来的按钮（不是灰字链接）
  })
})

describe('开关开着', () => {
  it('老手版：并排两张入口卡 + 品牌标；没有叙事句；示例脚本是灰字链接（有最近项目时在页面最底部）', async () => {
    await mount('completed', true)
    expect(q('[data-home-narrative]')).toBeNull()
    const cards = [...host.querySelectorAll('[data-home-entry]')]
    expect(cards.map((c) => c.getAttribute('data-home-entry'))).toEqual(['data', 'script'])
    expect(q('[data-home-entry="data"]')!.textContent).toContain('只有数据，还没有图')
    expect(q('[data-home-entry="data"]')!.textContent).toContain('选数据文件夹，让助手帮你画出来。')
    // 右卡仍是原来的拖放区 / 选脚本入口（稳定锚点没变）
    expect(q('[data-home-entry="script"]')!.hasAttribute('data-home-dropzone')).toBe(true)
    const sample = q('button[data-home-sample]')!
    expect(sample.className).not.toContain('inline-flex')
    const content = q('[data-home-content]')!
    expect(content.lastElementChild!.contains(sample)).toBe(true)
  })

  it('没有最近项目时，示例链接就在两张卡下面', async () => {
    useProjectStore.setState({ recent: [] })
    await mount('completed', true)
    const cards = q('[data-home-entry="data"]')!.closest('div.grid')!
    expect(cards.nextElementSibling!.contains(q('button[data-home-sample]'))).toBe(true)
  })

  it('新手版也拆成两张卡（否则新用户没有入口）；教程入口与导入按钮照旧', async () => {
    await mount('not_started', true)
    expect(q('[data-home-entry="data"]')).not.toBeNull()
    expect(q('button[data-home-import]')).not.toBeNull()
    expect(q('[data-onboarding-anchor="tutorial-entry"]')).not.toBeNull()
  })

  it('左卡只做点击：选一个文件夹 → 按「整个文件夹」打开 → 开「数据」抽屉；取消 / 打开失败不开', async () => {
    await mount('completed', true)
    await act(async () => q<HTMLButtonElement>('[data-home-entry="data"]')!.click())
    await act(async () => {})
    expect(desktop.pickDirectory).toHaveBeenCalled()
    expect(open).toHaveBeenCalledWith('/Users/me/data-folder', false, { entry: 'folder' })
    expect(useUiStore.getState()).toMatchObject({ leftTab: 'data', leftOpen: true })

    useUiStore.setState({ leftTab: 'assets' })
    desktop.pickDirectory.mockResolvedValueOnce(null)
    await act(async () => q<HTMLButtonElement>('[data-home-entry="data"]')!.click())
    await act(async () => {})
    expect(useUiStore.getState().leftTab).toBe('assets')

    open.mockRejectedValueOnce(new Error('nope'))
    await act(async () => q<HTMLButtonElement>('[data-home-entry="data"]')!.click())
    await act(async () => {})
    expect(useUiStore.getState().leftTab).toBe('assets')
  })
})

describe('开关怎么拿：不闪、不阻塞', () => {
  it('首次渲染按本机记的上一次画（同步，不等后端）：缓存是 1 → store 初始就是开', async () => {
    window.localStorage.setItem('tavotto.taf-enabled', '1')
    vi.resetModules()
    const fresh = await import('@/store/tafFlagStore')
    expect(fresh.useTafFlagStore.getState()).toMatchObject({ enabled: true, settled: false })
    window.localStorage.removeItem('tavotto.taf-enabled')
    vi.resetModules()
    const empty = await import('@/store/tafFlagStore')
    expect(empty.useTafFlagStore.getState().enabled).toBe(false)
  })

  it('后台问一次：回来是开 → 换版并记下；回来是关 → 换回并记下；读不到 → 当没变、不记', async () => {
    useTafFlagStore.setState({ enabled: false, settled: false })
    capsReply = 'on'
    await act(async () => useTafFlagStore.getState().refresh())
    expect(useTafFlagStore.getState()).toMatchObject({ enabled: true, settled: true })
    expect(window.localStorage.getItem('tavotto.taf-enabled')).toBe('1')

    useTafFlagStore.setState({ enabled: true, settled: false })
    capsReply = 'off'
    await act(async () => useTafFlagStore.getState().refresh())
    expect(useTafFlagStore.getState().enabled).toBe(false)
    expect(window.localStorage.getItem('tavotto.taf-enabled')).toBe('0')

    useTafFlagStore.setState({ enabled: true, settled: false })
    capsReply = 'fail'
    await act(async () => useTafFlagStore.getState().refresh())
    expect(useTafFlagStore.getState()).toMatchObject({ enabled: true, settled: false })
    expect(window.localStorage.getItem('tavotto.taf-enabled')).toBe('0')
  })

  it('助手面板已经探测过就直接用它的结果，不再多问一次', async () => {
    const spy = vi.spyOn(globalThis, 'fetch')
    useTafFlagStore.setState({ enabled: false, settled: false })
    useAiStore.setState({ caps: { agents: [], endpoints: [], presets: [], checked_at_ms: 0, taf_enabled: true } })
    await act(async () => useTafFlagStore.getState().refresh())
    expect(useTafFlagStore.getState().enabled).toBe(true)
    expect(spy).not.toHaveBeenCalledWith(expect.stringContaining('/api/ai/capabilities'), expect.anything())
    spy.mockRestore()
  })

  it('主页挂上就在后台问一次开关，回来是开则换成两张卡', async () => {
    capsReply = 'on'
    useOnboardingStore.setState({ status: 'completed' })
    useTafFlagStore.setState({ enabled: false, settled: false })
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
    await act(async () => {
      root.render(<ProjectPicker />)
    })
    await act(async () => { await new Promise((r) => setTimeout(r, 0)) })
    expect(q('[data-home-entry]')).not.toBeNull()
  })
})
