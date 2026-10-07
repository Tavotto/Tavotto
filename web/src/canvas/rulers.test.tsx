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
import { useViewportStore } from '@/store/viewportStore'
import { emptyProject, type ShapeObject } from '@/types/document'

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
