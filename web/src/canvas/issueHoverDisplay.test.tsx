/**
 * 问题面板的元素悬停轮廓 ↔ 画布上**真正显示着**的那个元素（#832 评审）。
 *
 * 挂真实的 `ProblemPanel` + `PanelView` + `OverlaySvg`，从问题行指上去，比两样东西：
 *   - 显示：`PanelView` 内容层实际挂着的 CSS transform（从 DOM 读，不从被测代码算）作用在元素框上；
 *   - 轮廓：`IssueOverlay` 画出来的 rect 与它的 SVG transform。
 * 两边都归一到面板包围盒（0..1）再比，所以与缩放、平移无关；缩放取 130%（评审复现的倍率）。
 * 水平 / 垂直翻转、翻转 + 旋转逐一量：只转不翻的轮廓会落在镜像位置。
 *
 * jsdom 不做布局，变换由这里的小解析器按 CSS / SVG 规范（列表从右往左作用到点上）自己合成。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { ProblemPanel } from '@/components/left/ProblemPanel'
import { literal } from '@/i18n'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { runValidation, useValidationStore } from '@/store/validationStore'
import { mmToPx, mmToViewX, mmToViewY, useViewportStore } from '@/store/viewportStore'
import { useWorkspaceStore } from '@/store/workspace'
import { seedExactRender } from '@/test/renderFixtures'
import { emptyProject, type PanelObject } from '@/types/document'
import { OverlaySvg } from './OverlaySvg'
import { PanelView } from './PanelView'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: () => new Promise(() => {}),
}))

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

/** 刻度文字故意放在左上角一小条：任何一个方向翻错都离得很远 */
const TICKS: [number, number, number, number] = [0.08, 0.1, 0.3, 0.06]
const manifest = {
  stem: 'Fig1',
  size_mm: [40, 30],
  elements: [
    {
      gid: 'axes_0.xticks',
      role: 'ticks',
      label: 'X 刻度文字',
      bbox: TICKS,
      draggable: false,
      editable: [{ prop: 'fontsize', type: 'number', value: 6 }],
    },
  ],
}

const basePanel: PanelObject = {
  id: 'p1',
  type: 'panel',
  fileId: 'Fig1.pdf',
  fileKind: 'pdf',
  nativeW: 40,
  nativeH: 30,
  overrides: [],
  x: 10,
  y: 5,
  w: 40,
  h: 30,
  script: 'fig1.py',
}

let container: HTMLDivElement
let root: Root

function Harness() {
  const p = useDocumentStore((s) => s.doc.objects.find((o) => o.id === 'p1')) as PanelObject
  return <PanelView obj={p} />
}

async function setup(over: Partial<PanelObject>) {
  const panel = { ...basePanel, ...over }
  useViewportStore.setState({ zoom: 1.3, panX: 17, panY: 9, originX: 0, originY: 0, viewW: 900, viewH: 700 })
  useUiStore.setState({
    tool: 'select',
    elementPanelId: null,
    selectedGids: [],
    issueHover: null,
    problemFilter: null,
    problemScope: null,
    problemCursor: null,
    problemView: 'figure',
    problemDrill: null,
    problemContext: null,
    leftTab: 'problems',
    leftOpen: true,
  })
  useWorkspaceStore.getState().clear()
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
  useRenderStore.setState({ render: async () => {} })
  useValidationStore.setState({ results: [], issues: [], ready: false, failed: false, running: false })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_issue_hover_display')
  useDocumentStore.getState().commit(literal('准备'), (d) => {
    d.page = { w: 80, h: 60 }
    d.objects = [{ ...panel }]
  })
  useAssetStore.setState({ byId: { 'Fig1.pdf': { id: 'Fig1.pdf', mtime: 1 } } } as never)
  seedExactRender(panel, manifest as never)
  runValidation()
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <ProblemPanel />
        <Harness />
        <OverlaySvg />
      </TooltipProvider>,
    )
  })
  return panel
}

beforeEach(() => {
  localStorage.clear()
})

afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
})

/* ------------------------- 2D 仿射：CSS / SVG transform ------------------------- */

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
const apply = (m: M, [x, y]: [number, number]): [number, number] => [m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]]
const translate = (x: number, y: number): M => [1, 0, 0, 1, x, y]
const rotate = (deg: number): M => {
  const r = (deg * Math.PI) / 180
  const c = Math.round(Math.cos(r) * 1e12) / 1e12
  const s = Math.round(Math.sin(r) * 1e12) / 1e12
  return [c, s, -s, c, 0, 0]
}
/** CSS 与 SVG 的 transform 列表：从左往右右乘，即从右往左作用到点上 */
function parse(src: string | null | undefined): M {
  let m = I
  for (const [, fn, raw] of (src ?? '').matchAll(/(\w+)\(([^)]*)\)/g)) {
    const a = raw.split(/[\s,]+/).filter(Boolean).map((v) => parseFloat(v))
    if (fn === 'translate') m = mul(m, translate(a[0], a[1] ?? 0))
    else if (fn === 'scale') m = mul(m, [a[0], 0, 0, a[1] ?? a[0], 0, 0])
    else if (fn === 'rotate')
      m = a.length === 3 ? mul(m, mul(translate(a[1], a[2]), mul(rotate(a[0]), translate(-a[1], -a[2])))) : mul(m, rotate(a[0]))
    else throw new Error(`没见过的变换：${fn}`)
  }
  return m
}
const bboxOf = (pts: [number, number][]) => {
  const xs = pts.map((p) => p[0])
  const ys = pts.map((p) => p[1])
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)]
}
const corners = (x: number, y: number, w: number, h: number): [number, number][] => [
  [x, y],
  [x + w, y],
  [x, y + h],
  [x + w, y + h],
]

/** 画布上真正显示着的刻度文字框（归一到面板包围盒）：读 PanelView 内容层的 DOM 与 CSS transform */
function displayedTicks(): number[] {
  // 面板的显示层：`[data-display]` 下第一层就是内容层（尺寸、居中偏移、翻转 / 旋转的 CSS transform 都在它上面）
  const content = container.querySelector<HTMLElement>('[data-display]')!.firstElementChild as HTMLElement
  const px = (v: string) => parseFloat(v) || 0
  const cw = px(content.style.width)
  const ch = px(content.style.height)
  const left = px(content.style.left)
  const top = px(content.style.top)
  const boxW = 2 * left + cw
  const boxH = 2 * top + ch
  // 没有裁剪：整图正好铺满内容层（PanelView 的 `layout` 在不裁剪时就是内容层本身）
  const [lx, ly, lw, lh] = [0, 0, cw, ch]
  // transform-origin 缺省 = 内容层中心
  const m = mul(translate(cw / 2, ch / 2), mul(parse(content.style.transform), translate(-cw / 2, -ch / 2)))
  const [u, v, w, h] = TICKS
  const pts = corners(lx + u * lw, ly + v * lh, w * lw, h * lh).map((p) => {
    const [x, y] = apply(m, p)
    return [(x + left) / boxW, (y + top) / boxH] as [number, number]
  })
  return bboxOf(pts)
}

/** 问题面板指着那一行时，覆盖层画出来的轮廓（归一到面板包围盒，去掉 2px 外扩） */
function hoverOutline(panel: PanelObject): { box: number[]; tol: number } {
  const rect = container.querySelector<SVGRectElement>('[data-issue-hover-gid]')!
  expect(rect, '轮廓描的是元素（带 gid），不是退回整张图').not.toBeNull()
  const t = useViewportStore.getState()
  const vx = mmToViewX(panel.x, t)
  const vy = mmToViewY(panel.y, t)
  const vw = mmToPx(panel.w, t)
  const vh = mmToPx(panel.h, t)
  const n = (k: string) => Number(rect.getAttribute(k))
  const pad = 2
  const m = parse(rect.getAttribute('transform'))
  const pts = corners(n('x') + pad, n('y') + pad, n('width') - 2 * pad, n('height') - 2 * pad).map((p) => {
    const [x, y] = apply(m, p)
    return [(x - vx) / vw, (y - vy) / vh] as [number, number]
  })
  return { box: bboxOf(pts), tol: 1e-6 }
}

async function hoverRow() {
  const row = container.querySelector<HTMLElement>('[data-issue-row][data-issue-object="p1"]')!
  expect(row).not.toBeNull()
  await act(async () => {
    row.dispatchEvent(new PointerEvent('pointerover', { bubbles: true }))
  })
  expect(useUiStore.getState().issueHover).toEqual({ objectId: 'p1', gid: 'axes_0.xticks' })
}

describe('问题悬停轮廓与画布上显示着的元素重合（130%，#832 评审）', () => {
  const cases: [string, Partial<PanelObject>][] = [
    ['不翻转', {}],
    ['水平翻转', { flipH: true }],
    ['垂直翻转', { flipV: true }],
    ['水平 + 垂直翻转', { flipH: true, flipV: true }],
    ['水平翻转 + 旋转 90°', { flipH: true, rotation: 90, w: 30, h: 40 }],
    ['垂直翻转 + 旋转 270°', { flipV: true, rotation: 270, w: 30, h: 40 }],
  ]
  for (const [name, over] of cases) {
    it(name, async () => {
      const panel = await setup(over)
      await hoverRow()
      // 先确认被比的对象在：有翻转 / 旋转的用例里，面板内容层真的挂着那个变换
      const content = container.querySelector<HTMLElement>('[data-display]')!.firstElementChild as HTMLElement
      if (Object.keys(over).length) expect(content.style.transform).toMatch(/scale|rotate/)
      const shown = displayedTicks()
      const { box, tol } = hoverOutline(panel)
      box.forEach((v, i) => expect(Math.abs(v - shown[i]), `${name} 第 ${i} 个边`).toBeLessThan(tol))
    })
  }
})
