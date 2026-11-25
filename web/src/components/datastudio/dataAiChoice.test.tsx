/**
 * 左栏「数据」抽屉左下角「分析用的 AI」小图标：none 不出（仍是居中的胶囊）；其余三种状态靠形状区分；点开列表选「自动 / 某个 AI / 不用 AI 分析」、
 * 「连接其他 AI…」；点选即关、图标立刻换，失败回退并说一句；指定的那个用不了时仍在列表里看得出。接口全 mock。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { ApiError, aiDataChoiceGet, aiDataChoiceSet, aiDataFilesAll, type AiCapabilities, type AiDataUnderstandingChoice } from '@/lib/api'
import { setCurrentProjectId } from '@/lib/session'
import { useAiStore } from '@/store/aiStore'
import { useDataFigureStore } from '@/store/dataFigureStore'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'
import { DataDraft } from './DataDraft'
import { DataDrawer } from './DataDrawer'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
Element.prototype.hasPointerCapture ??= () => false

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  aiDataFilesAll: vi.fn(),
  aiDataChoiceGet: vi.fn(),
  aiDataChoiceSet: vi.fn(),
  aiDataFigureDiscard: vi.fn(async () => ({ ok: true })),
  fetchAiCapabilities: vi.fn(() => new Promise(() => {})),
}))
vi.mock('@/store/liveSync', () => ({ refreshProjectNow: vi.fn(async () => {}) }))
const connect = vi.hoisted(() => vi.fn())
vi.mock('@/store/connectAiStore', async (importOriginal) => ({ ...(await importOriginal<typeof import('@/store/connectAiStore')>()), requestConnectAi: connect }))
const files = vi.mocked(aiDataFilesAll)
const get = vi.mocked(aiDataChoiceGet)
const set = vi.mocked(aiDataChoiceSet)

let root: Root
let host: HTMLDivElement
const q = <T extends Element = HTMLElement>(s: string) => document.querySelector<T>(s)
const qa = <T extends Element = HTMLElement>(s: string) => [...document.querySelectorAll<T>(s)]
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)) })
const opt = (id: string, model = 'gpt-x') => ({ id, label: id.toUpperCase(), logo: 'openai', vendor: 'v', kind: 'key' as const, model })
const choice = (o: Partial<AiDataUnderstandingChoice> = {}): AiDataUnderstandingChoice => ({ state: 'on', mode: 'auto', connection: 'a', options: [opt('a'), opt('b', 'claude-y')], ...o })
const capsWith = (conns: { id: string; label: string; logo: string }[]) => ({ agents: [], vendors: [], connections: conns.map((c) => ({ ...c, kind: 'key', state: 'ready', models: [] })) }) as unknown as AiCapabilities
const icon = () => q('[data-data-ai-choice]')
const mount = async (c: AiDataUnderstandingChoice, caps = capsWith([{ id: 'a', label: 'A', logo: 'openai' }])) => {
  files.mockResolvedValue({ files: [], truncated: false, understanding: c })
  useAiStore.setState({ caps })
  await act(async () => root.render(<TooltipProvider><DataDrawer /><DataDraft /></TooltipProvider>))
  await flush()
  await flush()
}
const openMenu = async () => {
  await act(async () => {
    icon()!.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0, pointerType: 'mouse' }))
  })
}
const pick = async (value: string) => {
  await act(async () => qa(`[data-data-ai-option="${value}"]`)[0]!.dispatchEvent(new MouseEvent('click', { bubbles: true })))
}

beforeEach(() => {
  files.mockReset()
  get.mockReset()
  set.mockReset()
  connect.mockReset()
  get.mockImplementation(async () => ({ understanding: useDataFigureStore.getState().understandingChoice ?? choice() }))
  setCurrentProjectId('pa')
  useUiStore.setState({ status: null })
  useDataFigureStore.getState().clear()
  useProjectStore.setState({ project: { open: true, name: 'proj', writable: true } })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  document.body.innerHTML = ''
  useDataFigureStore.getState().clear()
  useAiStore.setState({ caps: null })
})

describe('图标：出不出、长什么样', () => {
  it('none：不出图标，仍是居中的「连接 AI」胶囊', async () => {
    await mount(choice({ state: 'none', connection: null, options: [] }))
    expect(icon()).toBeNull()
    expect(q('[data-data-connect]')).not.toBeNull()
  })

  it('on：图标是当前连接的厂商标志（有标志就不是 ✦），悬停「分析用的 AI：名字」；auto 时名字是实际在用的那个', async () => {
    await mount(choice())
    expect(icon()!.getAttribute('data-data-ai-choice')).toBe('on')
    expect(icon()!.getAttribute('aria-label')).toBe('分析用的 AI：A')
    expect(q('[data-data-connect]')).toBeNull()
  })

  it('off：✦ 上划斜线，悬停「不用 AI 分析」；形状与 on 不同', async () => {
    await mount(choice({ state: 'off', mode: 'off', connection: null }))
    expect(icon()!.getAttribute('data-data-ai-choice')).toBe('off')
    expect(icon()!.getAttribute('aria-label')).toBe('不用 AI 分析')
    expect(icon()!.querySelectorAll('svg')).toHaveLength(2) // 星 + 斜线
    expect(q('[data-data-connect]')).toBeNull()
  })

  it('unavailable：另一种形状（拔掉的插头），悬停写明用不了；不出胶囊', async () => {
    await mount(choice({ state: 'unavailable', mode: 'connection', connection: null, chosen: 'zz' }))
    expect(icon()!.getAttribute('data-data-ai-choice')).toBe('unavailable')
    expect(icon()!.getAttribute('aria-label')).toBe('该模型无法连接，请换一个尝试')
    expect(icon()!.querySelectorAll('svg')).toHaveLength(1)
    expect(q('[data-data-connect]')).toBeNull()
  })

  it('三种形状的 SVG 路径互不相同（不只靠颜色）', async () => {
    const shapes: string[] = []
    for (const c of [choice(), choice({ state: 'off', mode: 'off', connection: null }), choice({ state: 'unavailable', mode: 'connection', connection: null, chosen: 'zz' })]) {
      await mount(c)
      shapes.push(icon()!.innerHTML)
      await act(async () => root.unmount())
      root = createRoot(host)
      useDataFigureStore.getState().clear()
    }
    expect(new Set(shapes).size).toBe(3)
  })
})

describe('按钮是扁平的 ghost 图标钮；气泡是同一句、可达名也是它', () => {
  it('没有白底、没有投影；悬停才有浅灰底（ghost 样式）；在用 text-ink-2，关了 / 用不了 text-ink-3', async () => {
    await mount(choice())
    const cls = icon()!.className
    expect(cls).not.toContain('shadow-pop')
    expect(cls).not.toMatch(/(^|\s)bg-surface(\s|$)/)
    expect(cls).toContain('hover:bg-surface-hover')
    expect(cls).toContain('text-ink-2')
    await act(async () => root.unmount())
    root = createRoot(host)
    useDataFigureStore.getState().clear()
    await mount(choice({ state: 'off', mode: 'off', connection: null }))
    expect(icon()!.className).toContain('text-ink-3')
  })

  it('关闭态的斜线满幅贯穿图标（对角 3,3 → 21,21），不是一小截', async () => {
    await mount(choice({ state: 'off', mode: 'off', connection: null }))
    const paths = [...icon()!.querySelectorAll('svg path')].map((p) => p.getAttribute('d'))
    expect(paths).toContain('M3 3L21 21')
  })

  it('气泡真的接上了：聚焦按钮就出气泡，文案 = 可达名 =「分析用的 AI：A」', async () => {
    await mount(choice())
    await act(async () => icon()!.focus())
    await flush()
    const tip = document.querySelector('[data-tooltip-label]')
    expect(tip?.textContent).toBe('分析用的 AI：A')
    expect(icon()!.getAttribute('aria-label')).toBe('分析用的 AI：A')
  })

  it('关了 / 用不了的气泡也是各自那一句', async () => {
    await mount(choice({ state: 'off', mode: 'off', connection: null }))
    await act(async () => icon()!.focus())
    await flush()
    expect(document.querySelector('[data-tooltip-label]')?.textContent).toBe('不用 AI 分析')
  })
})

describe('列表', () => {
  it('自动、每个 AI 一行（名字 + 次要文字是型号）、不用 AI 分析、分隔线后「连接其他 AI…」；当前生效的有选中态', async () => {
    await mount(choice())
    await openMenu()
    const rows = qa('[data-data-ai-menu] [role=menuitemradio]')
    expect(rows.map((r) => r.getAttribute('data-data-ai-option'))).toEqual(['auto', 'conn:a', 'conn:b', 'off'])
    expect(rows[1]!.textContent).toContain('A')
    expect(rows[1]!.textContent).toContain('gpt-x')
    expect(rows[0]!.textContent).toBe('自动当前：A') // 自动且在用：第二行小字说现在用的是谁
    expect(rows[3]!.textContent).toBe('不用 AI 分析')
    // 五行（含「连接其他 AI…」）文字起点对齐：每一行都有自己的图标
    for (const r of rows) expect(r.querySelector('svg'), r.getAttribute('data-data-ai-option') ?? '').not.toBeNull()
    expect(rows.map((r) => r.getAttribute('aria-checked'))).toEqual(['true', 'false', 'false', 'false'])
    expect(q('[data-data-ai-connect]')!.textContent).toBe('连接其他 AI…')
    // 没有手拼的 radio / 600 字重
    expect(q('[data-data-ai-menu]')!.querySelector('[role=radio]')).toBeNull()
    expect(q('[data-data-ai-menu]')!.innerHTML).not.toContain('font-semibold')
  })

  it('指定了某个连接：它是选中的；off 时「不用 AI 分析」是选中的', async () => {
    await mount(choice({ mode: 'connection', chosen: 'b', connection: 'b' }))
    await openMenu()
    expect(qa('[data-data-ai-menu] [role=menuitemradio]').map((r) => r.getAttribute('aria-checked'))).toEqual(['false', 'false', 'true', 'false'])
  })

  it('指定的那个用不了且不在清单里：仍有一行（名字取自能力缓存）、选中、写明用不了', async () => {
    await mount(choice({ state: 'unavailable', mode: 'connection', connection: null, chosen: 'zz' }), capsWith([{ id: 'zz', label: 'ZZ-AI', logo: 'openai' }]))
    await openMenu()
    const row = q('[data-data-ai-option="conn:zz"]')!
    expect(row.textContent).toContain('ZZ-AI')
    expect(row.textContent).toContain('该模型无法连接，请换一个尝试')
    expect(row.getAttribute('aria-checked')).toBe('true')
  })

  it('名字也取不到：回退成通用的一句', async () => {
    await mount(choice({ state: 'unavailable', mode: 'connection', connection: null, chosen: 'zz' }), capsWith([]))
    await openMenu()
    expect(q('[data-data-ai-option="conn:zz"]')!.textContent).toContain('你选的模型')
  })

  it('打开时轻量重取一次；「连接其他 AI…」走现有的 requestConnectAi', async () => {
    await mount(choice())
    await openMenu()
    expect(get).toHaveBeenCalledTimes(1)
    await act(async () => q('[data-data-ai-connect]')!.dispatchEvent(new MouseEvent('click', { bubbles: true })))
    expect(connect).toHaveBeenCalledTimes(1)
  })
})

describe('选了之后', () => {
  it('点选即关、图标立刻换成所选（等的时候 aria-busy），回来后落定', async () => {
    await mount(choice())
    let release!: (v: { understanding: AiDataUnderstandingChoice }) => void
    set.mockReturnValue(new Promise((r) => (release = r)))
    await openMenu()
    await pick('off')
    expect(q('[data-data-ai-menu]')).toBeNull() // 已关
    expect(icon()!.getAttribute('data-data-ai-choice')).toBe('off') // 立刻换
    expect(icon()!.getAttribute('aria-busy')).toBe('true')
    expect(set.mock.calls[0]![0]).toEqual({ mode: 'off' })
    await act(async () => release({ understanding: choice({ state: 'off', mode: 'off', connection: null }) }))
    expect(icon()!.getAttribute('aria-busy')).toBeNull()
    expect(useDataFigureStore.getState().understandingChoice?.state).toBe('off')
  })

  it('选某个 AI：发 {mode: connection, connection: id}', async () => {
    await mount(choice())
    set.mockResolvedValue({ understanding: choice({ mode: 'connection', chosen: 'b', connection: 'b' }) })
    await openMenu()
    await pick('conn:b')
    expect(set.mock.calls[0]![0]).toEqual({ mode: 'connection', connection: 'b' })
    expect(icon()!.getAttribute('aria-label')).toBe('分析用的 AI：B')
  })

  it('失败：图标回到原来的，用胶囊说一句（后端那句），不卡在等待态', async () => {
    await mount(choice())
    set.mockRejectedValue(new ApiError('该模型无法连接，请换一个尝试', 400, { code: 'understanding_connection_unavailable' }))
    await openMenu()
    await pick('conn:b')
    await flush()
    expect(icon()!.getAttribute('data-data-ai-choice')).toBe('on')
    expect(icon()!.getAttribute('aria-busy')).toBeNull()
    expect(useUiStore.getState().status).not.toBeNull()
  })
})
