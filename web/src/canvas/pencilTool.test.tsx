/**
 * 铅笔工具的画布一侧（ADR 0185 M8）：拖一笔只走预览平面（原始轨迹），松手拟合成一个 path、一条历史；吸附只在起点；
 * 终点回到起点闭合；⌥ 松手不简化；容差按屏幕像素算（缩放不同，同一笔在屏幕上拟合出的锚点数一样）；N 切到铅笔。
 */
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { useKeyboard } from '@/hooks/useKeyboard'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest'
import { literal } from '@/i18n'
import { pathToMm, rotatePoint } from '@/lib/pathEdit'
import { setVecopsSource } from '@/lib/vecops/kernel'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { pathDraftPreview } from '@/store/svgPreviewStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { emptyProject, type PathObject } from '@/types/document'
import { pencilPointerDown, pencilSettled } from './pencilController'

const WASM = path.resolve(process.cwd(), 'src/lib/vecops/vecops.wasm')
beforeAll(() =>
  setVecopsSource(async () => {
    const b = readFileSync(WASM)
    return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer
  }),
)
afterAll(() => setVecopsSource(null))

const zoomPx = (mm: number) => mmToWorld(mm) * useViewportStore.getState().zoom
const down = (x: number, y: number) =>
  ({ clientX: zoomPx(x), clientY: zoomPx(y), button: 0, preventDefault() {}, stopPropagation() {}, nativeEvent: new MouseEvent('pointerdown') }) as unknown as React.PointerEvent
const fire = (type: 'pointermove' | 'pointerup', x: number, y: number, init: MouseEventInit = {}) =>
  window.dispatchEvent(new MouseEvent(type, { clientX: zoomPx(x), clientY: zoomPx(y), bubbles: true, ...init }))
const objects = () => useDocumentStore.getState().doc.objects
const past = () => useDocumentStore.getState().past.length

/** 画一笔：sine 波（mm），可选 ⌥ 松手；回落定后的对象 */
async function stroke(points: [number, number][], init: MouseEventInit = {}) {
  await act(async () => {
    pencilPointerDown(down(...points[0]))
    for (const p of points.slice(1)) fire('pointermove', ...p)
  })
  const mid = pathDraftPreview()
  await act(async () => {
    fire('pointerup', ...points[points.length - 1], init)
    await pencilSettled()
  })
  return mid
}
const wave = (x0 = 10, y0 = 30, n = 120): [number, number][] =>
  Array.from({ length: n }, (_, i) => [x0 + (i / (n - 1)) * 60, y0 + 8 * Math.sin(i / 10)])

beforeEach(async () => {
  localStorage.clear()
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0 })
  useUiStore.setState({ tool: 'pencil', snapEnabled: true, snapToObjects: true, snapToGrid: false, snapToGuides: false })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_pencil_' + Math.random())
  useDocumentStore.setState({ past: [], future: [] })
})
afterEach(() => useInteractionStore.getState().end())

describe('铅笔', () => {
  it('画的过程只有预览（原始轨迹）；松手落成一个 path、一条历史，拟合后锚点远少于原始点', async () => {
    const mid = await stroke(wave())
    expect(mid?.nodes.length).toBeGreaterThan(50)
    expect(pathDraftPreview()).toBeNull()
    expect(objects()).toHaveLength(1)
    expect(past()).toBe(1)
    const p = objects()[0] as PathObject
    expect(p.type).toBe('path')
    expect(p.subpaths[0].closed).toBe(false)
    expect(p.subpaths[0].nodes.length).toBeLessThan(20)
  })

  it('⌥ 松手：不简化，保留原始的点', async () => {
    const pts = wave()
    await stroke(pts, { altKey: true })
    const p = objects()[0] as PathObject
    expect(p.subpaths[0].nodes.length).toBeGreaterThan(100)
  })

  it('终点回到起点附近：闭合', async () => {
    const ring: [number, number][] = Array.from({ length: 90 }, (_, i) => [50 + 20 * Math.cos((i / 88) * 2 * Math.PI), 50 + 15 * Math.sin((i / 88) * 2 * Math.PI)])
    await stroke(ring)
    expect((objects()[0] as PathObject).subpaths[0].closed).toBe(true)
  })

  it('吸附只在起点：起点吸到别的对象（x = 40 那条竖线）；画的过程中不吸、不出参考线', async () => {
    const r: PathObject = { id: 'r', type: 'path', x: 40, y: 10, w: 0.01, h: 20, subpaths: [{ closed: false, nodes: [{ p: [0, 0] }, { p: [0, 1] }] }], strokePt: 1, color: '#000', fill: null }
    useDocumentStore.getState().commit(literal('放'), (d) => void d.objects.push(r))
    const pts: [number, number][] = [[40.4, 50], ...Array.from({ length: 40 }, (_, i) => [40.4 + i * 0.5, 50 + (i % 2) * 0.4] as [number, number])]
    let snapDuring: number[] = []
    await act(async () => {
      pencilPointerDown(down(...pts[0]))
      for (const p of pts.slice(1)) fire('pointermove', ...p)
      snapDuring = [...useInteractionStore.getState().snapXs]
      fire('pointerup', ...pts[pts.length - 1], { altKey: true })
      await pencilSettled()
    })
    expect(snapDuring).toEqual([])
    const p = objects().find((o) => o.id !== 'r') as PathObject
    const first = rotatePoint(p, pathToMm(p)[0].nodes[0].p)
    // 起点吸到那条线上（线的框是 40–40.01，吸到哪一边都行），不是按下的 40.4
    expect(first[0]).toBeGreaterThanOrEqual(40 - 1e-9)
    expect(first[0]).toBeLessThanOrEqual(40.01 + 1e-9)
    const second = rotatePoint(p, pathToMm(p)[0].nodes[1].p)
    expect(second[0]).toBeCloseTo(40.4, 6) // 之后的点照原样，不吸（离 40 不到吸附容差也不吸）
  })

  it('容差按屏幕像素：同一笔在屏幕上一样，放大 4 倍画出来的锚点数与 1 倍时相同；同一笔在纸上一样，放大后贴得更紧（锚点更多）', async () => {
    // 带细小起伏的一笔：起伏约 0.4 mm，1 倍时在容差里被抹平，4 倍时超过容差要保留
    const bumpy = (k: number): [number, number][] =>
      Array.from({ length: 240 }, (_, i) => [10 / k + ((i / 239) * 60) / k, 30 / k + (6 * Math.sin(i / 25) + 0.4 * Math.sin(i / 3)) / k])
    await stroke(bumpy(1))
    const n1 = (objects()[0] as PathObject).subpaths[0].nodes.length
    useViewportStore.setState({ zoom: 4 })
    await stroke(bumpy(4))
    const n4 = (objects()[1] as PathObject).subpaths[0].nodes.length
    expect(n4).toBe(n1)
    await stroke(bumpy(1).map(([x, y]) => [x, y]))
    const n4paper = (objects()[2] as PathObject).subpaths[0].nodes.length
    expect(n4paper).toBeGreaterThan(n1)
  })

  it('原地点一下：什么都不画、不进历史', async () => {
    await stroke([[10, 10], [10.01, 10]])
    expect(objects()).toHaveLength(0)
    expect(past()).toBe(0)
  })

  it('N 切到铅笔（钢笔是 P）', async () => {
    function Host() {
      useKeyboard()
      return null
    }
    const el = document.createElement('div')
    document.body.appendChild(el)
    const root = createRoot(el)
    await act(async () => root.render(<Host />))
    useUiStore.setState({ tool: 'select' })
    await act(async () => void window.dispatchEvent(new KeyboardEvent('keydown', { key: 'n', bubbles: true, cancelable: true })))
    expect(useUiStore.getState().tool).toBe('pencil')
    await act(async () => root.unmount())
    el.remove()
  })
})
