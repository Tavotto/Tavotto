/**
 * 网格点（`GridDots`）：点绝不变深（维护者铁律）、副点只在半格够大时淡入、吸上网格线那一行 / 列才换选中蓝。
 * jsdom 没有 2d 上下文：换一个只记录 `fill` 时 fillStyle 与矩形数的桩。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { GridDots } from './GridDots'
import { useInteractionStore } from '@/store/interactionStore'
import { useUiStore } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'

globalThis.IS_REACT_ACT_ENVIRONMENT = true

interface Fill {
  style: string
  alpha: number
  rects: number
}
let fills: Fill[]
let host: HTMLDivElement
let root: Root

beforeEach(() => {
  fills = []
  let rects = 0
  const ctx = {
    fillStyle: '',
    globalAlpha: 1,
    clearRect: vi.fn(),
    beginPath: () => {
      rects = 0
    },
    rect: () => {
      rects++
    },
    fill() {
      fills.push({ style: this.fillStyle, alpha: this.globalAlpha, rects })
    },
  }
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(ctx as never)
  vi.stubGlobal('devicePixelRatio', 2)
  // jsdom 不解析 var()：按探针节点上写的变量名给一个确定的 rgb（sel = 选中蓝，其余 = 纸上的墨）
  const realGcs = window.getComputedStyle.bind(window)
  vi.spyOn(window, 'getComputedStyle').mockImplementation((el: Element, pseudo?: string | null) => {
    const color = (el as HTMLElement).style?.color ?? ''
    if (color.includes('--color-sel')) return { color: 'rgb(70, 133, 226)' } as CSSStyleDeclaration
    if (color.includes('--color-paper-ink')) return { color: 'rgb(27, 27, 24)' } as CSSStyleDeclaration
    return realGcs(el, pseudo)
  })
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, viewW: 800, viewH: 600 })
  useUiStore.setState({ snapEnabled: true, snapToGrid: true })
  useInteractionStore.setState({ snapXs: [], snapYs: [] })
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

const mount = () => act(async () => root.render(<GridDots w={100} h={80} gridSize={10} />))

describe('GridDots', () => {
  it('canvas 的净变换是恒等：left/top = -pan/zoom，scale(1/zoom)；整个层不吃指针', async () => {
    await act(async () => useViewportStore.setState({ zoom: 2, panX: 40, panY: 30 }))
    await mount()
    const c = host.querySelector<HTMLCanvasElement>('[data-grid-dots]')!
    expect(c.style.left).toBe('-20px')
    expect(c.style.top).toBe('-15px')
    expect(c.style.transform).toBe('scale(0.5)')
    expect(c.className).toContain('pointer-events-none')
  })

  it('主点的墨是常量：缩放怎么变，主点那一档 alpha 不变；副点只在半格够大时出现，且不比主点深', async () => {
    const majors = new Set<number>()
    for (const zoom of [0.5, 1, 3, 8]) {
      fills = []
      await act(async () => useViewportStore.setState({ zoom }))
      await mount()
      majors.add(fills[0]!.alpha)
      const minor = fills.slice(1).find((f) => f.rects > 0)
      if (minor) expect(minor.alpha).toBeLessThanOrEqual(fills[0]!.alpha)
      if (zoom <= 0.5) expect(fills.length, `zoom ${zoom} 不画副点`).toBe(1)
      if (zoom >= 1) expect(fills.length, `zoom ${zoom} 画副点`).toBeGreaterThan(1)
    }
    expect(majors.size, '主点 alpha 不随缩放变化').toBe(1)
  })

  it('吸上网格线：那一行换选中蓝（sel），不在网格上的吸附（对象边）不亮；松开后淡出', async () => {
    await mount()
    const before = fills.length
    fills = []
    await act(async () => useInteractionStore.setState({ snapYs: [30], snapXs: [33.3] }))
    // 只有 y=30 在网格上
    const lit = fills.filter((f) => f.style.includes('70, 133, 226'))
    expect(lit.length).toBe(1)
    expect(before).toBeGreaterThan(0)
    fills = []
    await act(async () => useInteractionStore.setState({ snapYs: [] }))
    // 离开瞬间还在淡（alpha 略低于 0.9 或等于），随后 rAF 淡完
    expect(fills.some((f) => f.style.includes('70, 133, 226'))).toBe(true)
  })
})
