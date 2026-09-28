/**
 * 方向键微调的一段还没收尾（停顿不足 `NUDGE_QUIET_MS`）就按下指针（Codex #671）。
 *
 * 同一个 pointerdown 先经过 window 捕获阶段：`finishNudge` 在那里提交这一段的 override、
 * 让几何权威失效；React 还没重渲染，命中层处理器拿的仍是提交前那一帧的 obj / manifest。
 * 旧实现照这份过期基线起拖，松手写回「原位 + 拖动」，刚提交的键盘位移被整个盖掉。
 *
 * 钉住的事实：
 *   1. 这一下被吞掉——微调保留（一条撤销）、不开拖动、选区不动；
 *   2. 这一版的权威挂上画面之后再拖：落点 = 微调 + 拖动（基线来自文档里的新 override）；
 *   3. 选中框手柄（OverlaySvg）同一条闸：闭包里的面板过期时不起手。
 *
 * jsdom 说明：`getBoundingClientRect` 恒为 0，命中层桩一个与 layout × zoom 同口径的矩形
 * （与 twinAxesPick / spineZones 同一套）。
 */
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { literal } from '@/i18n'
import type { Manifest, ManifestElement } from '@/lib/api'
import { useKeyboard } from '@/hooks/useKeyboard'
import { useDocumentStore } from '@/store/documentStore'
import { resetGestureCoordinator } from '@/store/gestureCoordinator'
import { useInteractionStore } from '@/store/interactionStore'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { resetPreview } from '@/store/svgPreviewStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { seedExactRender } from '@/test/renderFixtures'
import { emptyProject, type PanelObject } from '@/types/document'
import { nudgeActive, resetNudge } from './nudge'
import { OverlaySvg } from './OverlaySvg'
import { PanelView } from './PanelView'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

/* -------------------------------- 测试数据 -------------------------------- */

const PAGE_W = 100
const PAGE_H = 80

const title: ManifestElement = {
  gid: 'axes_0.title',
  role: 'title',
  label: '标题',
  bbox: [0.3, 0.05, 0.2, 0.05],
  editable: [],
  draggable: true,
  anchor: [0.3, 0.08],
  drag_prop: 'pos_frac',
}

const manifest: Manifest = {
  stem: 'Fig1',
  size_mm: [PAGE_W, PAGE_H],
  elements: [
    { gid: 'figure', role: 'figure', label: '整图', bbox: [0, 0, 1, 1], editable: [], draggable: false },
    {
      gid: 'axes_0',
      role: 'axes',
      label: '子图 1',
      bbox: [0.1, 0.2, 0.8, 0.7],
      editable: [{ prop: 'position', type: 'rect', value: [0.1, 0.1, 0.8, 0.7] }],
      draggable: false,
      resizable: true,
    },
    title,
  ],
}

const panel = (): PanelObject =>
  ({
    id: 'p1',
    type: 'panel',
    x: 0,
    y: 0,
    w: PAGE_W,
    h: PAGE_H,
    fileId: 'Fig1.pdf',
    fileKind: 'pdf',
    nativeW: PAGE_W,
    nativeH: PAGE_H,
    script: 'fig.py',
    overrides: [],
  }) as PanelObject

const LAYOUT = { width: mmToWorld(PAGE_W), height: mmToWorld(PAGE_H) }
/** 标题 bbox 中心：按在这里命中标题 */
const ON_TITLE: [number, number] = [0.4, 0.075]
/** 拖动的屏幕位移（px，zoom 1） */
const DRAG_PX = 40

const axesPos = () =>
  livePanel().overrides.find((o) => o.gid === 'axes_0' && o.prop === 'position')?.value as
    | number[]
    | undefined
const livePanel = () => useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1') as PanelObject
const titlePos = () =>
  livePanel().overrides.find((o) => o.gid === title.gid && o.prop === 'pos_frac')?.value as
    | number[]
    | undefined
const past = () => useDocumentStore.getState().past

/* --------------------------------- 挂载 ---------------------------------- */

let root: Root
let container: HTMLDivElement

function Harness() {
  useKeyboard()
  const p = useDocumentStore((s) => s.doc.objects.find((o) => o.id === 'p1')) as PanelObject
  return createElement(PanelView, { obj: p })
}

/** 命中层（权威就位时才在）；jsdom 的矩形恒为 0，桩成 layout × zoom */
function hitLayer(): HTMLDivElement | null {
  const layer = container.querySelector('[data-authority="ready"]') as HTMLDivElement | null
  if (layer) {
    layer.getBoundingClientRect = () =>
      ({
        left: 0,
        top: 0,
        width: LAYOUT.width,
        height: LAYOUT.height,
        right: LAYOUT.width,
        bottom: LAYOUT.height,
        x: 0,
        y: 0,
        toJSON() {},
      }) as DOMRect
  }
  return layer
}

function tap(key: string) {
  act(() => {
    window.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }))
    window.dispatchEvent(new KeyboardEvent('keyup', { key, bubbles: true }))
  })
}

/**
 * 在命中层上按下、拖 DRAG_PX、松手。`beforeDown` 与按下在同一个 act 里跑：两者之间
 * React 不重渲染，模拟「处理器闭包还是上一帧」
 */
function pressAndDrag(layer: HTMLElement, [fx, fy]: [number, number], beforeDown?: () => void) {
  const x = fx * LAYOUT.width
  const y = fy * LAYOUT.height
  const down = new MouseEvent('pointerdown', { bubbles: true, cancelable: true, button: 0, clientX: x, clientY: y })
  Object.assign(down, { pointerType: 'mouse', pointerId: 1 })
  act(() => {
    beforeDown?.()
    layer.dispatchEvent(down)
  })
  act(() => {
    for (let i = 1; i <= 4; i++) {
      window.dispatchEvent(
        new MouseEvent('pointermove', { bubbles: true, clientX: x + (DRAG_PX * i) / 4, clientY: y }),
      )
    }
    window.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, clientX: x + DRAG_PX, clientY: y }))
  })
}

beforeEach(async () => {
  localStorage.clear()
  resetNudge()
  resetPreview()
  resetGestureCoordinator()
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700 })
  useUiStore.setState({
    tool: 'select',
    snapEnabled: false,
    elementPanelId: 'p1',
    selectedGids: [title.gid],
    status: null,
  })
  useSelectionStore.getState().clear()
  useInteractionStore.getState().end()
  useRenderStore.getState().clear()
  // 权威渲染一律悬着：提交之后这一版的权威由用例自己决定什么时候「到」
  useRenderStore.setState({ render: async () => {} })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_nudge_then_pointer')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(panel())
  })
  seedExactRender(livePanel(), manifest)
  useDocumentStore.setState({ past: [], future: [] })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => {
    root.render(createElement(Harness))
  })
})

afterEach(async () => {
  resetNudge()
  await act(async () => {
    root.unmount()
  })
  container.remove()
  resetPreview()
  useInteractionStore.getState().end()
})

describe('微调这一段还开着时按下指针', () => {
  it('按 → 后立刻在标题上按下拖动：这一下被吞掉，微调保留、一条撤销、不开拖动', () => {
    const layer = hitLayer()!
    tap('ArrowRight')
    expect(nudgeActive()).toBe(true)

    pressAndDrag(layer, ON_TITLE)

    expect(nudgeActive()).toBe(false)
    // 键盘这一段的位移：0.5 mm 按面板在页面上的宽度折成分数；拖动那 40 px 没有写进去
    expect(titlePos()![0]).toBeCloseTo(0.3 + 0.5 / PAGE_W, 9)
    expect(titlePos()![1]).toBeCloseTo(0.08, 9)
    expect(past()).toHaveLength(1)
    expect(useInteractionStore.getState().kind).toBe('none')
    expect(useUiStore.getState().selectedGids).toEqual([title.gid])
    // React 重渲染之后命中层就是「等权威」的停摆层
    expect(container.querySelector('[data-authority="syncing"]')).not.toBeNull()
  })

  it('这一版的权威挂上画面之后再拖：落点 = 微调 + 拖动，两条撤销', async () => {
    tap('ArrowRight')
    pressAndDrag(hitLayer()!, ON_TITLE)
    expect(titlePos()![0]).toBeCloseTo(0.3 + 0.5 / PAGE_W, 9)

    await act(async () => {
      seedExactRender(livePanel(), manifest)
    })
    pressAndDrag(hitLayer()!, ON_TITLE)

    expect(titlePos()![0]).toBeCloseTo(0.3 + 0.5 / PAGE_W + DRAG_PX / LAYOUT.width, 9)
    expect(titlePos()![1]).toBeCloseTo(0.08, 9)
    expect(past()).toHaveLength(2)
  })

  it('提交后的那一版恰好已经渲染过（权威在）：闭包里的 overrides 过期同样吞掉', () => {
    // 先走一遍同样的一段，让「→ 0.5 mm」那一版有现成的权威渲染，再撤回原位
    tap('ArrowRight')
    pressAndDrag(hitLayer()!, ON_TITLE)
    act(() => {
      seedExactRender(livePanel(), manifest)
      useDocumentStore.getState().undo()
    })
    expect(titlePos()).toBeUndefined()

    const layer = hitLayer()!
    tap('ArrowRight')
    pressAndDrag(layer, ON_TITLE)
    expect(titlePos()![0]).toBeCloseTo(0.3 + 0.5 / PAGE_W, 9)
    expect(useInteractionStore.getState().kind).toBe('none')
  })

  it('没有微调、overrides 没变，但权威在按下前一刻离开了画面（同一变体换了图）：同样不起手', () => {
    const layer = hitLayer()!
    useUiStore.setState({ selectedGids: [] })
    pressAndDrag(layer, ON_TITLE, () => {
      useRenderStore.getState().patch(renderKeyOf(livePanel()), { svg: '<svg data-next="1"/>' })
    })
    expect(titlePos()).toBeUndefined()
    expect(useUiStore.getState().selectedGids).toEqual([])
    expect(past()).toHaveLength(0)
  })

  it('对照：净位移为零的一段（一去一回）不写文档，按下照常起拖', () => {
    const layer = hitLayer()!
    tap('ArrowRight')
    tap('ArrowLeft')
    pressAndDrag(layer, ON_TITLE)

    expect(titlePos()![0]).toBeCloseTo(0.3 + DRAG_PX / LAYOUT.width, 9)
    expect(past()).toHaveLength(1)
  })
})

describe('选中框手柄：同一道闸', () => {
  it('选中子图按 → 后立刻按它的缩放手柄拖：不起手，微调保留、一条撤销', () => {
    useUiStore.setState({ selectedGids: ['axes_0'] })
    const overlay = document.createElement('div')
    document.body.appendChild(overlay)
    const overlayRoot = createRoot(overlay)
    act(() => overlayRoot.render(createElement(OverlaySvg)))
    const handles = [...overlay.querySelectorAll('rect')].filter((r) =>
      /resize/.test(r.getAttribute('style') ?? ''),
    )
    expect(handles).toHaveLength(8)

    tap('ArrowRight')
    const down = new MouseEvent('pointerdown', { bubbles: true, cancelable: true, button: 0, clientX: 0, clientY: 0 })
    Object.assign(down, { pointerType: 'mouse', pointerId: 1 })
    act(() => {
      handles[0].dispatchEvent(down)
    })
    expect(useInteractionStore.getState().kind).toBe('none')
    act(() => {
      window.dispatchEvent(new MouseEvent('pointermove', { bubbles: true, clientX: DRAG_PX, clientY: DRAG_PX }))
      window.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, clientX: DRAG_PX, clientY: DRAG_PX }))
    })

    const pos = axesPos()!
    expect(pos[0]).toBeCloseTo(0.1 + 0.5 / PAGE_W, 9)
    expect(pos[1]).toBeCloseTo(0.1, 9)
    expect(pos[2]).toBeCloseTo(0.8, 9)
    expect(pos[3]).toBeCloseTo(0.7, 9)
    expect(past()).toHaveLength(1)
    act(() => overlayRoot.unmount())
    overlay.remove()
  })
})
