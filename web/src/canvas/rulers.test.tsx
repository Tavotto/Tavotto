/**
 * 标尺（2026-10-07 设计审计 §10.1）：单位角写出单位；选中对象时标尺上画一条选区带；颜色按挂载量一次
 * （此前每帧 `getComputedStyle`）；刻度数字是整数、不写 -0。
 * 主语：一个记录调用的假 2D context（jsdom 没有 canvas）——画了哪些 fillRect / fillText；
 * 以及 `getComputedStyle(documentElement)` 被调用的次数。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { Rulers } from './Rulers'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { useWorkspaceStore } from '@/store/workspace'
import { emptyProject, type PanelObject, type ShapeObject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

interface Call {
  op: string
  args: unknown[]
  fill?: unknown
  alpha?: number
}
let calls: Call[] = []
function fakeCtx() {
  const ctx = {
    fillStyle: '' as unknown,
    strokeStyle: '' as unknown,
    globalAlpha: 1,
    lineWidth: 1,
    font: '',
    textBaseline: '',
  } as Record<string, unknown>
  for (const op of ['setTransform', 'clearRect', 'beginPath', 'moveTo', 'lineTo', 'stroke', 'save', 'restore', 'translate', 'rotate'])
    ctx[op] = () => {}
  ctx.fillRect = (...args: unknown[]) => calls.push({ op: 'fillRect', args, fill: ctx.fillStyle, alpha: ctx.globalAlpha as number })
  ctx.fillText = (...args: unknown[]) => calls.push({ op: 'fillText', args })
  return ctx
}

const rect: ShapeObject = {
  id: 'r1', type: 'shape', shape: 'rect', x: 10, y: 10, w: 20, h: 10,
  strokePt: 1, color: '#111', fill: null,
}

let root: Root
let host: HTMLDivElement
let realGetContext: typeof HTMLCanvasElement.prototype.getContext

beforeEach(async () => {
  calls = []
  realGetContext = HTMLCanvasElement.prototype.getContext
  HTMLCanvasElement.prototype.getContext = (() => fakeCtx()) as never
  document.documentElement.style.setProperty('--color-sel', '#4685e2')
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0 })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_rulers')
  useDocumentStore.getState().commit(literal('放'), (d) => {
    d.objects.push(rect)
  })
  useSelectionStore.getState().clear()
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  HTMLCanvasElement.prototype.getContext = realGetContext
  useWorkspaceStore.getState().clear()
  vi.restoreAllMocks()
})

describe('标尺', () => {
  it('单位角写出 mm', async () => {
    await act(async () => root.render(<Rulers viewW={600} viewH={400} />))
    expect(host.querySelector('[data-ruler-unit]')!.textContent).toBe('mm')
  })

  it('选中对象：两条标尺上都有一段淡 sel 的选区带；没选中就没有', async () => {
    await act(async () => root.render(<Rulers viewW={600} viewH={400} />))
    const bands = () => calls.filter((c) => c.op === 'fillRect' && c.fill === '#4685e2')
    expect(bands()).toHaveLength(0)
    calls = []
    await act(async () => useSelectionStore.getState().set(['r1']))
    expect(bands()).toHaveLength(2)
    expect(bands().every((b) => (b.alpha ?? 1) < 0.5)).toBe(true)
  })

  // Codex #833：旋转 90° 的 20×10 矩形看得见的是 x 15–25 / y 5–25（中心 20, 15），不是未旋转的 x 10–30 / y 10–20
  it('旋转对象：选区带按看得见的外接框（visualBounds），与 zoomToSelection 同一口径', async () => {
    useDocumentStore.getState().commit(literal('转'), (d) => {
      ;(d.objects[0] as ShapeObject).rotationDeg = 90
    })
    await act(async () => root.render(<Rulers viewW={600} viewH={400} />))
    calls = []
    await act(async () => useSelectionStore.getState().set(['r1']))
    const px = (mm: number) => Math.round(mmToWorld(mm)) + 0.5
    const bands = calls.filter((c) => c.op === 'fillRect' && c.fill === '#4685e2').map((c) => c.args as number[])
    // 顶部标尺：fillRect(s0, 0, s1 - s0, h)；左侧标尺：fillRect(0, s0, w, s1 - s0)
    const xBand = bands.find((a) => a[1] === 0)!
    const yBand = bands.find((a) => a[0] === 0 && a[1] !== 0)!
    expect([xBand[0], xBand[0] + xBand[2]]).toEqual([px(15), px(25)])
    expect([yBand[1], yBand[1] + yBand[3]]).toEqual([px(5), px(25)])
  })

  it('颜色按挂载量一次：视口动了重画，但不再每帧读样式', async () => {
    const spy = vi.spyOn(window, 'getComputedStyle')
    await act(async () => root.render(<Rulers viewW={600} viewH={400} />))
    const afterMount = spy.mock.calls.length
    for (let i = 1; i <= 5; i++) await act(async () => useViewportStore.setState({ panX: i * 10 }))
    expect(spy.mock.calls.length).toBe(afterMount)
  })

  it('刻度数字是整数，不写 -0', async () => {
    await act(async () => root.render(<Rulers viewW={600} viewH={400} />))
    const labels = calls.filter((c) => c.op === 'fillText').map((c) => String(c.args[0]))
    expect(labels.length).toBeGreaterThan(0)
    for (const l of labels) expect(l).toMatch(/^-?\d+$/)
    expect(labels).not.toContain('-0')
  })
})

/**
 * 快速编辑这一屏只画正在编辑的那张图（Codex #833 P2）：选区里挂着的版面对象（从排版带进来的、⌘A 留下的）
 * 不进选区带——带只跨那张图；选区里只有版面对象就没有带。对照组：排版里带跨整个选区。
 * 今天 `CanvasStage` 在快速编辑里不挂标尺，这里直接挂 `Rulers` 量的是它自己的第二道判据。
 */
describe('标尺的选区带在快速编辑里只认那张图', () => {
  const panel: PanelObject = {
    id: 'p1', type: 'panel', fileId: 'Fig1.pdf', fileKind: 'pdf', nativeW: 80, nativeH: 60,
    overrides: [], x: 100, y: 50, w: 40, h: 30,
  }
  const px = (mm: number) => Math.round(mmToWorld(mm)) + 0.5
  const xBand = () => {
    const b = calls.filter((c) => c.op === 'fillRect' && c.fill === '#4685e2').map((c) => c.args as number[])
    const x = b.find((a) => a[1] === 0)
    return x ? [x[0], x[0] + x[2]] : null
  }
  beforeEach(() => {
    useDocumentStore.getState().commit(literal('加图'), (d) => {
      d.objects.push(panel)
    })
  })

  it('排版里：带跨整个选区（对照组）', async () => {
    await act(async () => root.render(<Rulers viewW={600} viewH={400} />))
    calls = []
    await act(async () => useSelectionStore.getState().set(['r1', 'p1']))
    expect(xBand()).toEqual([px(10), px(140)])
  })

  it('快速编辑里选区含那张图与版面对象：带只跨那张图', async () => {
    useWorkspaceStore.getState().enterFastEdit('p1')
    await act(async () => root.render(<Rulers viewW={600} viewH={400} />))
    calls = []
    await act(async () => useSelectionStore.getState().set(['r1', 'p1']))
    expect(xBand()).toEqual([px(100), px(140)])
  })

  it('快速编辑里选区只有版面对象：没有带', async () => {
    useWorkspaceStore.getState().enterFastEdit('p1')
    await act(async () => root.render(<Rulers viewW={600} viewH={400} />))
    // 先让带出现（选那张图），再换成只有版面对象：带的落点变了必然重画，量的是那一次重画
    await act(async () => useSelectionStore.getState().set(['p1']))
    expect(xBand()).toEqual([px(100), px(140)])
    calls = []
    await act(async () => useSelectionStore.getState().set(['r1']))
    // 主语核对：确实重画了（刻度照画），不是什么都没画
    expect(calls.some((c) => c.op === 'fillText')).toBe(true)
    expect(calls.filter((c) => c.op === 'fillRect' && c.fill === '#4685e2')).toHaveLength(0)
  })
})

/**
 * 换主题后标尺重画成新颜色（Codex #834 P2）：墨色按挂载量一次是对的，但缓存必须以「当前生效的主题」为键——
 * 此前 `useMemo(readInk, [])` 永不失效，切浅 / 深色（或跟随系统时系统换外观）后两条标尺留在旧色里，
 * 之后平移 / 缩放的重画也一直拿旧色。主语：底色那一笔 `fillRect(0, 0, …)` 的 fillStyle。
 * 显式偏好走真路径：`uiStore.setTheme` → `applyTheme` 挂 `data-theme`，值来自样式表里的 `[data-theme='dark']`；
 * 跟随系统走假 matchMedia 的 change 事件（jsdom 不算 @media，值表的切换用根上的内联变量模拟）。
 */
describe('标尺跟着生效主题重画', () => {
  const bgFills = () =>
    calls
      .filter((c) => c.op === 'fillRect' && ['0,0,600,20', '0,0,20,400'].includes(c.args.join(',')))
      .map((c) => c.fill)
  let sheet: HTMLStyleElement
  beforeEach(() => {
    sheet = document.createElement('style')
    sheet.textContent = ":root { --color-bg: #f0f0f0; } :root[data-theme='dark'] { --color-bg: #101010; }"
    document.head.appendChild(sheet)
  })
  afterEach(() => {
    sheet.remove()
    useUiStore.getState().setTheme('system')
    document.documentElement.style.removeProperty('--color-bg')
  })

  it('设置里切到深色：两条标尺当场重画成深色，之后平移也不回到旧色', async () => {
    useUiStore.getState().setTheme('light')
    await act(async () => root.render(<Rulers viewW={600} viewH={400} />))
    expect(bgFills()).toEqual(['#f0f0f0', '#f0f0f0'])
    calls = []
    await act(async () => useUiStore.getState().setTheme('dark'))
    expect(bgFills()).toEqual(['#101010', '#101010'])
    calls = []
    await act(async () => useViewportStore.setState({ panX: 30 }))
    expect(bgFills()).toEqual(['#101010', '#101010'])
  })

  it('跟随系统：系统换外观（matchMedia change）标尺跟着重画', async () => {
    const listeners = new Set<(e: MediaQueryListEvent) => void>()
    let dark = false
    const mql = {
      get matches() { return dark },
      media: '(prefers-color-scheme: dark)',
      addEventListener: (_: string, fn: (e: MediaQueryListEvent) => void) => listeners.add(fn),
      removeEventListener: (_: string, fn: (e: MediaQueryListEvent) => void) => listeners.delete(fn),
    }
    vi.stubGlobal('matchMedia', vi.fn(() => mql))
    try {
      useUiStore.getState().setTheme('system')
      document.documentElement.style.setProperty('--color-bg', '#f0f0f0')
      await act(async () => root.render(<Rulers viewW={600} viewH={400} />))
      expect(bgFills()).toEqual(['#f0f0f0', '#f0f0f0'])
      calls = []
      // 系统换成深色：媒体查询那一段生效（这里用内联变量模拟），然后 change 事件
      dark = true
      document.documentElement.style.setProperty('--color-bg', '#101010')
      await act(async () => {
        for (const fn of listeners) fn({ matches: true } as MediaQueryListEvent)
      })
      expect(bgFills()).toEqual(['#101010', '#101010'])
      calls = []
      await act(async () => useViewportStore.setState({ panX: 30 }))
      expect(bgFills()).toEqual(['#101010', '#101010'])
    } finally {
      vi.unstubAllGlobals()
    }
  })
})
