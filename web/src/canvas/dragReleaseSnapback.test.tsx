/**
 * 松手之后、新图到达之前：**被拖的元素停在松手的位置**，不先弹回原位（2026-09-24 用户报
 * 「拖到一个位置，它先回去、顿一会才到新的地方」）。
 *
 * 成因：拖动的预览位移直接写在 SVG 节点上（`svgPreviewStore`）；松手提交 override 让面板重渲，
 * 而 React 19 按 `===` 比 `dangerouslySetInnerHTML` 的 `{__html}` **对象**——每次渲染新写一个就
 * 每次原样重写 innerHTML，预览位移随旧节点一起没了。SVG 字符串没变，重挂预览的 effect 也不跑。
 * 修法：`lib/useHtmlMarkup` 按字符串复用同一个对象。
 *
 * `fakeRealtimeDrag.test` 那条「提交后位移一动不动」抓不到它：那里的 SVG 是手写
 * `document.body.innerHTML` 摆的，根本没经过 PanelView。这里**真渲染 PanelView**。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { literal } from '@/i18n'
import { MATPLOTLIB_SVG } from '@/lib/__fixtures__/matplotlibSvg'
import type { EngineRenderOptions, Manifest, ManifestElement } from '@/lib/api'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { flushPreviewFrame, resetPreview } from '@/store/svgPreviewStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { startElementDrag } from './interactions'
import { PanelView } from './PanelView'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const engineRender = vi.fn()
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: (id: string, patches: unknown[], opts?: EngineRenderOptions) =>
    engineRender(id, patches, opts),
}))

const textEl: ManifestElement = {
  gid: 'axes_0.title',
  role: 'title',
  label: '标题',
  bbox: [0.2, 0.05, 0.4, 0.08],
  editable: [],
  draggable: true,
  anchor: [0.4, 0.09],
  drag_prop: 'pos_frac',
}
const manifest: Manifest = {
  stem: 'Fig1',
  size_mm: [101.6, 76.2],
  elements: [
    { gid: 'figure', role: 'figure', label: '整图', bbox: [0, 0, 1, 1], editable: [], draggable: false },
    textEl,
  ],
}
const panelOf = (): PanelObject =>
  ({
    id: 'p1',
    type: 'panel',
    x: 0,
    y: 0,
    w: 101.6,
    h: 76.2,
    fileId: 'Fig1.pdf',
    fileKind: 'pdf',
    nativeW: 101.6,
    nativeH: 76.2,
    script: 'fig.py',
    overrides: [],
  }) as unknown as PanelObject
const layout = { width: mmToWorld(101.6), height: mmToWorld(76.2) }

const livePanel = (): PanelObject => {
  const p = useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1')
  if (p?.type !== 'panel') throw new Error('测试面板没了')
  return p
}
const down = (clientX = 0, clientY = 0) =>
  ({ clientX, clientY, button: 0, stopPropagation() {} }) as unknown as React.PointerEvent
const fire = (type: 'pointermove' | 'pointerup', clientX: number, clientY: number) =>
  window.dispatchEvent(new MouseEvent(type, { clientX, clientY, bubbles: true }))
const titleNode = () => container.querySelector('[data-element-svg="p1"] [id="axes_0.title"]')
const tf = () => titleNode()?.getAttribute('transform') ?? null

let container: HTMLDivElement
let root: Root
const show = async () => {
  await act(async () => root.render(<PanelView obj={livePanel()} />))
}

beforeEach(async () => {
  engineRender.mockReset()
  // 新图一直在路上：这条用例量的正是「等它」的那段空档
  engineRender.mockReturnValue(new Promise(() => {}))
  resetPreview()
  localStorage.clear()
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700 })
  useUiStore.setState({ tool: 'select', snapEnabled: false, elementPanelId: 'p1', selectedGids: [] })
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_snapback')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(panelOf())
  })
  useRenderStore.getState().patch(renderKeyOf(panelOf()), {
    fileId: 'Fig1.pdf',
    manifest,
    svg: MATPLOTLIB_SVG,
    rev: 1,
    status: 'ready',
    lastPatches: '[]',
  })
  useRenderStore.setState({ latest: { 'Fig1.pdf': renderKeyOf(panelOf()) } })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  resetPreview()
  useInteractionStore.getState().end()
})

describe('松手之后、新图到达之前', () => {
  it('提交让面板重渲：SVG 节点不重建，预览位移一直挂着（不弹回原位）', async () => {
    await show()
    const before = titleNode()
    expect(before, '真渲染出来的 SVG 里应当有这个元素').not.toBeNull()
    startElementDrag(down(0, 0), livePanel(), textEl, layout)
    for (let i = 1; i <= 20; i++) {
      fire('pointermove', i * 10, i * 5)
      flushPreviewFrame()
    }
    const applied = tf()
    expect(applied, '拖动中应当挂着预览位移').toMatch(/^translate\(/)
    fire('pointerup', 200, 100)
    expect(engineRender, '松手应当发出定稿渲染').toHaveBeenCalledTimes(1)
    // 画布就是这么做的：文档里的面板换了新引用（带着新 override），面板随之重渲
    await show()
    expect(livePanel().overrides.length, '松手应当写下 override').toBe(1)
    expect(titleNode(), 'SVG 被原样重写了一遍（节点换了）').toBe(before)
    expect(tf(), '预览位移没了：元素会先弹回原位').toBe(applied)
  })

  it('与拖动无关的重渲也不重写 SVG（同一份字符串 = 同一个节点）', async () => {
    await show()
    const before = titleNode()
    await act(async () => useUiStore.setState({ selectedGids: ['axes_0.title'] }))
    await show()
    expect(titleNode()).toBe(before)
  })
})
