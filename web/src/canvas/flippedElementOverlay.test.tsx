/**
 * 翻转过的面板上编辑图内元素（#832 / #833 评审）。
 *
 * 画布画这张图时，PanelView 给内容层一个 CSS transform（`lib/panelTransform`：先在内容空间翻转、再旋转）。
 * 此前叠在图上的东西只认旋转：选中框与手柄（`OverlaySvg.ElementBoxes`）停在元素的**镜像**位置，点击命中的是
 * 镜像处的元素，往右拖 / 按 → 元素在画面上往左走。
 *
 * 钉住的事实（flipH、flipV、flipH + 90° 三种面板）：
 *   1. 选中框经覆盖层的 SVG transform 落到画面上，与 PanelView 的 CSS transform 把元素画到的位置重合；
 *   2. 点在画面上看得见的元素上，命中的就是它；
 *   3. 拖动 / 方向键在内容里走的方向，经 CSS transform 回到画面上，与指针 / 按键的方向一致；
 *   4. 图内元素框手柄的光标按手柄在**画面上**的方位给（翻转面板上内容的东北角画在西北）；
 *   5. 裁剪框（画面方位的手柄）拖哪条边、画面上动的就是哪条边，整张图在画布上纹丝不动。
 *
 * 两个 transform 字符串都是组件真实输出的（PanelView 的 style、OverlaySvg 的 `<g transform>`），用例各自
 * 解析、各自作用到元素框上再比——不经过被测的换算函数。jsdom 不排版：`getBoundingClientRect` 恒为 0，命中层
 * 桩成面板在屏幕上的外接框（zoom 1、pan 0 时 1 mm = mmToWorld(1) px）。
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
import { useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { resetPreview } from '@/store/svgPreviewStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { seedExactRender } from '@/test/renderFixtures'
import { emptyProject, type PanelObject } from '@/types/document'
import { resetNudge } from './nudge'
import { OverlaySvg } from './OverlaySvg'
import { useQuickEdit } from './quickEditStore'
import { PanelView } from './PanelView'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

/* -------------------------------- 测试数据 -------------------------------- */

// 内容（未旋转）100 × 80 mm；标题偏在左上角，镜像位置一眼可辨
const CW = 100
const CH = 80
const title: ManifestElement = {
  gid: 'axes_0.title',
  role: 'title',
  label: '标题',
  bbox: [0.1, 0.05, 0.2, 0.05],
  editable: [],
  draggable: true,
  anchor: [0.1, 0.08],
  drag_prop: 'pos_frac',
}
// 子图与标题不重叠：点标题命中的是标题
const axes: ManifestElement = {
  gid: 'axes_0',
  role: 'axes',
  label: '子图 1',
  bbox: [0.15, 0.3, 0.6, 0.5],
  editable: [{ prop: 'position', type: 'rect', value: [0.15, 0.2, 0.6, 0.5] }],
  draggable: false,
  resizable: true,
}
const manifest: Manifest = {
  stem: 'Fig1',
  size_mm: [CW, CH],
  elements: [
    { gid: 'figure', role: 'figure', label: '整图', bbox: [0, 0, 1, 1], editable: [], draggable: false },
    axes,
    title,
  ],
}

type Case = { name: string; flip: Partial<PanelObject>; w: number; h: number }
const CASES: Case[] = [
  { name: 'flipH', flip: { flipH: true }, w: CW, h: CH },
  { name: 'flipV', flip: { flipV: true }, w: CW, h: CH },
  { name: 'flipH + 90°', flip: { flipH: true, rotation: 90 }, w: CH, h: CW },
]

// 面板放在 (10, 20) mm：中心不在原点，绕中心的变换写错了也藏不住
const PX = 10
const PY = 20
const px = (mm: number) => mmToWorld(mm)

/* ----------------------------- 变换字符串解析 ----------------------------- */

/** 2×3 仿射 [a, b, c, d, e, f]：x' = a x + c y + e，y' = b x + d y + f */
type M = [number, number, number, number, number, number]
const I: M = [1, 0, 0, 1, 0, 0]
const mul = (m: M, n: M): M => [
  m[0] * n[0] + m[2] * n[1],
  m[1] * n[0] + m[3] * n[1],
  m[0] * n[2] + m[2] * n[3],
  m[1] * n[2] + m[3] * n[3],
  m[0] * n[4] + m[2] * n[5] + m[4],
  m[1] * n[4] + m[3] * n[5] + m[5],
]
const rot = (deg: number): M => {
  const r = (deg * Math.PI) / 180
  return [Math.cos(r), Math.sin(r), -Math.sin(r), Math.cos(r), 0, 0]
}
const tr = (x: number, y: number): M => [1, 0, 0, 1, x, y]
const apply = (m: M, x: number, y: number): [number, number] => [m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]]

/** CSS（绕元素中心，只用到 rotate(Ndeg) / scale(a, b)），列表从左往右右乘 = 从右往左作用于点 */
function parseCss(s: string | undefined): M {
  let m = I
  for (const [, fn, args] of (s ?? '').matchAll(/(\w+)\(([^)]*)\)/g)) {
    const v = args.split(/[\s,]+/).filter(Boolean).map(parseFloat)
    if (fn === 'rotate') m = mul(m, rot(v[0]))
    else if (fn === 'scale') m = mul(m, [v[0], 0, 0, v[1] ?? v[0], 0, 0])
    else throw new Error(`css ${fn}`)
  }
  return m
}

/** SVG transform 属性：rotate(a [cx cy]) / translate(x y) / scale(x [y]) */
function parseSvg(s: string | null): M {
  let m = I
  for (const [, fn, args] of (s ?? '').matchAll(/(\w+)\(([^)]*)\)/g)) {
    const v = args.split(/[\s,]+/).filter(Boolean).map(parseFloat)
    if (fn === 'rotate') m = mul(m, mul(tr(v[1] ?? 0, v[2] ?? 0), mul(rot(v[0]), tr(-(v[1] ?? 0), -(v[2] ?? 0)))))
    else if (fn === 'translate') m = mul(m, tr(v[0], v[1] ?? 0))
    else if (fn === 'scale') m = mul(m, [v[0], 0, 0, v[1] ?? v[0], 0, 0])
    else throw new Error(`svg ${fn}`)
  }
  return m
}

type Box = { x: number; y: number; w: number; h: number }
function aabb(m: M, b: Box): Box {
  const pts = [
    apply(m, b.x, b.y),
    apply(m, b.x + b.w, b.y),
    apply(m, b.x, b.y + b.h),
    apply(m, b.x + b.w, b.y + b.h),
  ]
  const xs = pts.map((p) => p[0])
  const ys = pts.map((p) => p[1])
  return { x: Math.min(...xs), y: Math.min(...ys), w: Math.max(...xs) - Math.min(...xs), h: Math.max(...ys) - Math.min(...ys) }
}

/* --------------------------------- 挂载 ---------------------------------- */

let root: Root
let container: HTMLDivElement

function Harness() {
  useKeyboard()
  const p = useDocumentStore((s) => s.doc.objects.find((o) => o.id === 'p1')) as PanelObject
  return createElement('div', null, createElement(PanelView, { obj: p }), createElement(OverlaySvg))
}

const livePanel = () => useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1') as PanelObject

async function mount(c: Case, extra: Partial<PanelObject> = {}) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_flipped_element')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push({
      id: 'p1', type: 'panel', x: PX, y: PY, w: c.w, h: c.h, fileId: 'Fig1.pdf', fileKind: 'pdf',
      nativeW: CW, nativeH: CH, script: 'fig.py', overrides: [], ...c.flip, ...extra,
    } as PanelObject)
  })
  seedExactRender(livePanel(), manifest)
  useDocumentStore.setState({ past: [], future: [] })
  await act(async () => {
    root.render(createElement(Harness))
  })
}

/** PanelView 内容层的 CSS 变换（绕内容中心 = 面板中心） */
function contentCss(): M {
  const layer = container.querySelector('[data-authority="ready"]') as HTMLElement
  return parseCss(layer.parentElement!.style.transform)
}

/** 面板中心（屏幕 px） */
const centre = (c: Case): [number, number] => [px(PX + c.w / 2), px(PY + c.h / 2)]

/** PanelView 把标题画到的屏幕框：内容层里的框（以内容中心为原点）经 CSS 变换、再平移到面板中心 */
function titleOnScreen(c: Case, dfx = 0, dfy = 0): Box {
  const [cx, cy] = centre(c)
  const m = mul(tr(cx, cy), contentCss())
  const [bx, by, bw, bh] = title.bbox
  return aabb(m, {
    x: px((bx + dfx - 0.5) * CW),
    y: px((by + dfy - 0.5) * CH),
    w: px(bw * CW),
    h: px(bh * CH),
  })
}

/** 命中层的屏幕矩形 = 面板在屏幕上的外接框（变换过的内容层的 AABB） */
function stubHitLayer(c: Case): HTMLElement {
  const layer = container.querySelector('[data-authority="ready"]') as HTMLElement
  const left = px(PX)
  const top = px(PY)
  const width = px(c.w)
  const height = px(c.h)
  layer.getBoundingClientRect = () =>
    ({ left, top, width, height, right: left + width, bottom: top + height, x: left, y: top, toJSON() {} }) as DOMRect
  return layer
}

function pointer(type: string, target: EventTarget, x: number, y: number) {
  const ev = new MouseEvent(type, { bubbles: true, cancelable: true, button: 0, clientX: x, clientY: y })
  Object.assign(ev, { pointerType: 'mouse', pointerId: 1 })
  target.dispatchEvent(ev)
}

const near = (a: number, b: number) => expect(a).toBeCloseTo(b, 4)
/** 覆盖层的框按像素网格取整、描边内缩半像素（`rectAttrs`）：差不过 1.5 px；镜像位置差的是几十 px */
function expectBox(a: Box, b: Box) {
  for (const k of ['x', 'y', 'w', 'h'] as const) expect(Math.abs(a[k] - b[k])).toBeLessThanOrEqual(1.5)
}

beforeEach(() => {
  localStorage.clear()
  resetNudge()
  resetPreview()
  resetGestureCoordinator()
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700 })
  useUiStore.setState({ tool: 'select', snapEnabled: false, elementPanelId: 'p1', selectedGids: [title.gid], status: null })
  useSelectionStore.getState().clear()
  useInteractionStore.getState().end()
  useRenderStore.getState().clear()
  useRenderStore.setState({ render: async () => {} })
  useQuickEdit.getState().close()
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(async () => {
  resetNudge()
  await act(async () => root.unmount())
  container.remove()
  useInteractionStore.getState().end()
  useUiStore.setState({ elementPanelId: null, selectedGids: [] })
})

describe.each(CASES)('$name 面板上的图内元素', (c) => {
  it('选中框经覆盖层的变换落在 PanelView 画出的标题上（不是它的镜像）', async () => {
    await mount(c)
    const rect = container.querySelector('[data-element-box="selected"]') as SVGRectElement
    expect(rect).not.toBeNull()
    const local: Box = {
      x: +rect.getAttribute('x')!,
      y: +rect.getAttribute('y')!,
      w: +rect.getAttribute('width')!,
      h: +rect.getAttribute('height')!,
    }
    const g = rect.closest('g[transform]')
    const onScreen = aabb(parseSvg(g?.getAttribute('transform') ?? null), local)
    expectBox(onScreen, titleOnScreen(c))
  })

  it('点在画面上看得见的标题上：命中标题', async () => {
    useUiStore.setState({ selectedGids: [] })
    await mount(c)
    const layer = stubHitLayer(c)
    const b = titleOnScreen(c)
    act(() => {
      pointer('pointerdown', layer, b.x + b.w / 2, b.y + b.h / 2)
      pointer('pointerup', window, b.x + b.w / 2, b.y + b.h / 2)
    })
    expect(useUiStore.getState().selectedGids).toEqual([title.gid])
  })

  it('拖动：元素在画面上走的方向与指针一致', async () => {
    await mount(c)
    const layer = stubHitLayer(c)
    const b = titleOnScreen(c)
    const [x0, y0] = [b.x + b.w / 2, b.y + b.h / 2]
    const [ddx, ddy] = [30, 12]
    act(() => {
      pointer('pointerdown', layer, x0, y0)
      for (let i = 1; i <= 4; i++) pointer('pointermove', window, x0 + (ddx * i) / 4, y0 + (ddy * i) / 4)
    })
    const drag = useInteractionStore.getState().gidDrag!
    expect(drag.gid).toBe(title.gid)
    // 内容里的分数位移经 PanelView 的 CSS 变换回到画面：就是指针走的那一段
    const after = titleOnScreen(c, drag.dfx, drag.dfy)
    near(after.x - b.x, ddx)
    near(after.y - b.y, ddy)
    act(() => pointer('pointerup', window, x0 + ddx, y0 + ddy))
  })

  it('方向键：按 → / ↓，元素在画面上往右 / 往下走', async () => {
    await mount(c)
    const b = titleOnScreen(c)
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true }))
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true }))
    })
    const drag = useInteractionStore.getState().gidDrag!
    const after = titleOnScreen(c, drag.dfx, drag.dfy)
    near(after.x - b.x, px(0.5))
    near(after.y - b.y, px(0.5))
  })
})

/* ------------------------------ 手柄光标 ------------------------------ */

const CURSOR_OF: Record<string, string> = {
  n: 'ns-resize', s: 'ns-resize', e: 'ew-resize', w: 'ew-resize',
  ne: 'nesw-resize', sw: 'nesw-resize', nw: 'nwse-resize', se: 'nwse-resize',
}
/** 画面上的位移 → 方位名（纵向在前）；容差盖住像素取整 */
const dirOf = (dx: number, dy: number) => (dy < -2 ? 'n' : dy > 2 ? 's' : '') + (dx > 2 ? 'e' : dx < -2 ? 'w' : '')

describe.each(CASES)('$name 面板上的子图缩放手柄', (c) => {
  it('每个手柄的光标与它在画面上的方位一致', async () => {
    useUiStore.setState({ selectedGids: [axes.gid] })
    await mount(c)
    const box = container.querySelector('[data-element-box="selected"]') as SVGRectElement
    const m = parseSvg(box.closest('g[transform]')?.getAttribute('transform') ?? null)
    const bx = +box.getAttribute('x')! + +box.getAttribute('width')! / 2
    const by = +box.getAttribute('y')! + +box.getAttribute('height')! / 2
    const [cx, cy] = apply(m, bx, by)
    const handles = [...container.querySelectorAll<SVGRectElement>('[data-element-handle]')]
    expect(handles).toHaveLength(8)
    for (const h of handles) {
      const [hx, hy] = apply(m, +h.getAttribute('x')! + +h.getAttribute('width')! / 2, +h.getAttribute('y')! + +h.getAttribute('height')! / 2)
      const onScreen = dirOf(hx - cx, hy - cy)
      expect(h.style.cursor, `手柄 ${h.dataset.elementHandle} 画在画面的 ${onScreen}`).toBe(CURSOR_OF[onScreen])
    }
    // 沿边命中带同一套
    for (const strip of container.querySelectorAll<SVGElement>('[data-edge-strip]')) {
      const content = strip.getAttribute('data-edge-strip')!
      const h = container.querySelector<SVGRectElement>(`[data-element-handle="${content}"]`)!
      expect(strip.style.cursor).toBe(h.style.cursor)
    }
  })
})

/* ------------------------------ 裁剪框 ------------------------------ */

// 已经裁过一刀：取景窗是整图的 x 0.1–0.7、y 0.15–0.85（内容 60 × 56 mm）
const CROP = { x: 0.1, y: 0.15, w: 0.6, h: 0.7 }

/** 整图上一个内容分数点此刻画在屏幕哪儿：PanelView 的内容层（取景窗）+ 里面整图的布局 + CSS 变换 */
function imagePointOnScreen(fx: number, fy: number): [number, number] {
  // PanelView：`[data-display]` 底下第一层是取景窗（带 CSS 变换），它的第一个孩子是整张图（按裁剪偏移、放大）
  const layer = container.querySelector<HTMLElement>('[data-display] > div')!
  const img = layer.firstElementChild as HTMLElement
  const p = livePanel()
  const [cx, cy] = [px(p.x + p.w / 2), px(p.y + p.h / 2)]
  const lw = parseFloat(layer.style.width)
  const lh = parseFloat(layer.style.height)
  const local: [number, number] = [
    parseFloat(img.style.left) + fx * parseFloat(img.style.width) - lw / 2,
    parseFloat(img.style.top) + fy * parseFloat(img.style.height) - lh / 2,
  ]
  const [x, y] = apply(parseCss(layer.style.transform), ...local)
  return [cx + x, cy + y]
}

const pageBox = () => {
  const p = livePanel()
  return { l: px(p.x), t: px(p.y), r: px(p.x + p.w), b: px(p.y + p.h) }
}

function dragCrop(sel: string, dx: number, dy: number) {
  const el = container.querySelector(sel) as SVGElement
  expect(el, sel).not.toBeNull()
  act(() => {
    pointer('pointerdown', el, 100, 100)
    for (let i = 1; i <= 4; i++) pointer('pointermove', window, 100 + (dx * i) / 4, 100 + (dy * i) / 4)
    pointer('pointerup', window, 100 + dx, 100 + dy)
  })
}

describe.each([
  // 画面上的手柄、画面上的位移、画面上该动的那条边
  { ...CASES[0], handle: 'e', dx: -20, dy: 0, edge: 'r' as const },
  { ...CASES[0], handle: 'w', dx: 15, dy: 0, edge: 'l' as const },
  { ...CASES[1], handle: 's', dx: 0, dy: -15, edge: 'b' as const },
  { ...CASES[1], handle: 'n', dx: 0, dy: 12, edge: 't' as const },
  { ...CASES[2], handle: 'e', dx: -20, dy: 0, edge: 'r' as const },
  { ...CASES[2], handle: 's', dx: 0, dy: -15, edge: 'b' as const },
])('$name 面板裁剪：拖画面上的 $handle 边', (c) => {
  it('动的是画面上那条边，别的边与整张图都不动', async () => {
    useUiStore.setState({ elementPanelId: null, selectedGids: [] })
    const [w, h] = c.flip.rotation ? [CROP.h * CH, CROP.w * CW] : [CROP.w * CW, CROP.h * CH]
    await mount({ ...c, w, h }, { crop: { ...CROP } })
    act(() => useUiStore.getState().setCropTarget('p1'))
    const before = pageBox()
    const corners = [imagePointOnScreen(0, 0), imagePointOnScreen(1, 1)]
    dragCrop(`[data-crop-handle="${c.handle}"]`, c.dx, c.dy)
    const after = pageBox()
    for (const k of ['l', 't', 'r', 'b'] as const) {
      const moved = k === c.edge ? (k === 'l' || k === 'r' ? c.dx : c.dy) : 0
      expect(after[k] - before[k], `边 ${k}`).toBeCloseTo(moved, 3)
    }
    const now = [imagePointOnScreen(0, 0), imagePointOnScreen(1, 1)]
    for (let i = 0; i < 2; i++) {
      expect(now[i][0]).toBeCloseTo(corners[i][0], 3)
      expect(now[i][1]).toBeCloseTo(corners[i][1], 3)
    }
  })
})

describe.each(CASES)('$name 面板裁剪：拖取景窗', (c) => {
  it('取景窗跟着指针在画面上走，整张图不动', async () => {
    useUiStore.setState({ elementPanelId: null, selectedGids: [] })
    const [w, h] = c.flip.rotation ? [CROP.h * CH, CROP.w * CW] : [CROP.w * CW, CROP.h * CH]
    await mount({ ...c, w, h }, { crop: { ...CROP } })
    act(() => useUiStore.getState().setCropTarget('p1'))
    const before = pageBox()
    const corners = [imagePointOnScreen(0, 0), imagePointOnScreen(1, 1)]
    // 往右下拖一小段（取景窗四周都还有余地，不会被夹住）
    const el = container.querySelector('[data-crop-handle]')!.parentElement!.parentElement!
    const frame = [...el.querySelectorAll('rect')].find((r) => r.style.cursor === 'move')!
    act(() => {
      pointer('pointerdown', frame, 100, 100)
      for (let i = 1; i <= 4; i++) pointer('pointermove', window, 100 + i * 2, 100 + i)
      pointer('pointerup', window, 108, 104)
    })
    const after = pageBox()
    expect(after.l - before.l).toBeCloseTo(8, 3)
    expect(after.t - before.t).toBeCloseTo(4, 3)
    expect(after.r - after.l).toBeCloseTo(before.r - before.l, 3)
    const now = [imagePointOnScreen(0, 0), imagePointOnScreen(1, 1)]
    for (let i = 0; i < 2; i++) {
      expect(now[i][0]).toBeCloseTo(corners[i][0], 3)
      expect(now[i][1]).toBeCloseTo(corners[i][1], 3)
    }
  })
})

