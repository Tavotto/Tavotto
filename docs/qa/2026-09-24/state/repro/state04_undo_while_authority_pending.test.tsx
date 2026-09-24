/**
 * STATE-04 复现（**预期红 = 产品缺陷**，不进测试集）：松手后权威渲染还在路上时撤销，
 * 文档回到拖动前，但**画布上的预览位移留在 SVG 节点上不走**——元素看起来还在拖到的位置。
 *
 * 成因（读码 + 本用例）：拖动的预览会话提交后等待「拖动那一版」的渲染键；撤销让面板键回到
 * 缓存里的旧变体，而那份 SVG 字符串恰好就是画布上正挂着的显示退路 → `svgHtml` 不变 →
 * PanelView 的 `reattachPreview` effect 不跑；拖动手势也没有在 gestureCoordinator 登记，
 * `runUndoRedo → finishActiveGesture()` 收不到它。之后拖动那一版的回包到了，也只是入库
 * （当前键是旧变体），DOM 仍不换。画布与文档、选择框（按 exact manifest 画在原位）三方不一致。
 *
 * 跑法：docs/qa/2026-09-24/state/repro/run.sh vitest-repro state04_undo_while_authority_pending.test.tsx
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { literal } from '@/i18n'
import { MATPLOTLIB_SVG } from '@/lib/__fixtures__/matplotlibSvg'
import type { EngineRenderOptions, Manifest, ManifestElement } from '@/lib/api'
import { runUndoRedo } from '@/hooks/useKeyboard'
import { startElementDrag } from '@/canvas/interactions'
import { PanelView } from '@/canvas/PanelView'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { flushPreviewFrame, resetPreview } from '@/store/svgPreviewStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { emptyProject, type PanelObject } from '@/types/document'

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
    id: 'p1', type: 'panel', x: 0, y: 0, w: 101.6, h: 76.2, fileId: 'Fig1.pdf', fileKind: 'pdf',
    nativeW: 101.6, nativeH: 76.2, script: 'fig.py', overrides: [],
  }) as unknown as PanelObject
const layout = { width: mmToWorld(101.6), height: mmToWorld(76.2) }
const livePanel = (): PanelObject =>
  useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1') as PanelObject
const down = () => ({ clientX: 0, clientY: 0, button: 0, stopPropagation() {} }) as unknown as React.PointerEvent
const fire = (type: string, x: number, y: number) =>
  window.dispatchEvent(new MouseEvent(type, { clientX: x, clientY: y, bubbles: true }))

let container: HTMLDivElement
let root: Root
const show = async () => {
  await act(async () => root.render(<PanelView obj={livePanel()} />))
}
const tf = () =>
  container.querySelector('[data-element-svg="p1"] [id="axes_0.title"]')?.getAttribute('transform') ?? null

let resolveRender: (v: unknown) => void = () => {}
beforeEach(async () => {
  engineRender.mockReset()
  engineRender.mockImplementation(() => new Promise((r) => { resolveRender = r }))
  resetPreview()
  localStorage.clear()
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700 })
  useUiStore.setState({ tool: 'select', snapEnabled: false, elementPanelId: 'p1', selectedGids: [] })
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_state04')
  useDocumentStore.getState().commit(literal('加面板'), (d) => { d.objects.push(panelOf()) })
  useRenderStore.getState().patch(renderKeyOf(panelOf()), {
    fileId: 'Fig1.pdf', manifest, svg: MATPLOTLIB_SVG, rev: 1, status: 'ready', lastPatches: '[]',
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

describe('STATE-04：权威在途时撤销', () => {
  it('撤销之后画布不应再挂着拖动的预览位移', async () => {
    await show()
    startElementDrag(down(), livePanel(), textEl, layout)
    for (let i = 1; i <= 20; i++) {
      fire('pointermove', i * 10, i * 5)
      flushPreviewFrame()
    }
    fire('pointerup', 200, 100)
    await show()
    expect(tf()).toMatch(/^translate\(/) // 松手后等权威：预览挂着（正确）

    runUndoRedo(false)
    await show()
    expect(livePanel().overrides).toEqual([]) // 文档已撤销
    expect(tf(), '撤销后文档已无 override，画布却仍挂着拖动位移').toBeNull()
  })

  it('拖动那一版的回包之后才到：仍应回到原位', async () => {
    await show()
    startElementDrag(down(), livePanel(), textEl, layout)
    for (let i = 1; i <= 20; i++) {
      fire('pointermove', i * 10, i * 5)
      flushPreviewFrame()
    }
    fire('pointerup', 200, 100)
    await show()
    runUndoRedo(false)
    await show()
    await act(async () => {
      resolveRender({ rev: 2, manifest, svg: `${MATPLOTLIB_SVG}<!-- dragged -->`, warnings: [] })
    })
    await show()
    expect(tf(), '拖动那一版回包之后，撤销掉的位移仍挂在画布上').toBeNull()
  })
})
