/**
 * 自由路径 `path`（ADR 0185）在前端的消费者，与「转为路径」。
 *
 * 1. **转前后画面逐点不动**：每种形状 / 箭头（含圆角、无边框、旋转）在页面 mm 里沿画布画的那条轮廓采样，
 *    与转出来的路径（`pathGeometry`）互相的最远距离 < 0.01 mm（双向 Hausdorff）。源一侧按 SVG 原语的真实几何采样
 *    （`<rect rx>` 的圆弧、`<ellipse>`、大括号的二次贝塞尔），不经 `convertToPath` 的任何一行。
 * 2. `convertToPath`：一次 commit、不换 id、数组位置不动、撤销逐字段回去；不能转的不动。
 * 3. 右键菜单只在选区全能转时摆「转为路径」；画布 `PathView` 的命中层按 fill-rule、缩略图 / 导出预览 / 裁切 / 写回 / EPS 判定都认 `path`。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { literal } from '@/i18n'
import { PathView } from '@/canvas/PathView'
import { QuickEdit } from '@/canvas/QuickEdit'
import { useQuickEdit } from '@/canvas/quickEditStore'
import { CanvasThumb } from '@/components/CanvasThumb'
import { ThumbShape } from '@/components/ThumbShape'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { convertToPath } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useViewportStore } from '@/store/viewportStore'
import {
  arrowHeads,
  emptyProject,
  lineEndpoints,
  type ArrowObject,
  type CanvasObject,
  type PathObject,
  type ShapeObject,
  type TextObject,
} from '@/types/document'
import { canConvertToPath, toPathObject } from './convertToPath'
import { visibleContentBounds } from './exportCrop'
import { isEpsTranslucent } from './epsOpacity'
import { toExportObjects } from './exportPayload'
import { pathGeometry, pathHitTest, rotateAbout, type PathSeg } from './pathGeom'
import { arcPoints, arrowGeometry, braceParts, cornerRadius, shapeInset, shapeOutline, type ArrowHeadShape } from './shapeGeometry'
import { MM_PER_PT } from './units'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

type XY = [number, number]

/* ------------------------------ 采样 ------------------------------------- */

const lerp = (a: XY, b: XY, n = 60): XY[] =>
  Array.from({ length: n + 1 }, (_, i) => [a[0] + ((b[0] - a[0]) * i) / n, a[1] + ((b[1] - a[1]) * i) / n])
const polyline = (pts: XY[], closed: boolean): XY[] => {
  const out: XY[] = []
  for (let i = 1; i < pts.length; i++) out.push(...lerp(pts[i - 1], pts[i]))
  if (closed) out.push(...lerp(pts[pts.length - 1], pts[0]))
  return out
}
const arc = (c: XY, rx: number, ry: number, a0: number, a1: number, n = 120): XY[] =>
  Array.from({ length: n + 1 }, (_, i) => {
    const a = a0 + ((a1 - a0) * i) / n
    return [c[0] + rx * Math.cos(a), c[1] + ry * Math.sin(a)]
  })
const quad = (p0: XY, c: XY, p1: XY, n = 120): XY[] =>
  Array.from({ length: n + 1 }, (_, i) => {
    const t = i / n
    const u = 1 - t
    return [u * u * p0[0] + 2 * u * t * c[0] + t * t * p1[0], u * u * p0[1] + 2 * u * t * c[1] + t * t * p1[1]]
  })

/** 路径段 → 稠密采样（每段三次贝塞尔 200 步） */
function sampleSegs(segs: readonly PathSeg[]): XY[] {
  const out: XY[] = []
  let cur: XY = [0, 0]
  let start: XY = [0, 0]
  for (const s of segs) {
    if (s[0] === 'M') {
      cur = [s[1], s[2]]
      start = cur
      out.push(cur)
    } else if (s[0] === 'L') {
      out.push(...lerp(cur, [s[1], s[2]]))
      cur = [s[1], s[2]]
    } else if (s[0] === 'C') {
      const [, ax, ay, bx, by, ex, ey] = s
      for (let i = 0; i <= 200; i++) {
        const t = i / 200
        const u = 1 - t
        out.push([
          u * u * u * cur[0] + 3 * u * u * t * ax + 3 * u * t * t * bx + t * t * t * ex,
          u * u * u * cur[1] + 3 * u * u * t * ay + 3 * u * t * t * by + t * t * t * ey,
        ])
      }
      cur = [ex, ey]
    } else {
      out.push(...lerp(cur, start))
      cur = start
    }
  }
  return out
}

const headPoints = (h: ArrowHeadShape): XY[] => {
  if (h.kind === 'dot') return arc([h.cx, h.cy], h.r, h.r, 0, 2 * Math.PI)
  const raw = 'd' in h ? h.d.replace(/[ML]/g, ' ') : h.points
  const pts = raw.trim().split(/\s+/).map((p: string) => p.split(',').map(Number) as XY)
  return polyline(pts, !('d' in h))
}

/** 对象自己的框空间（mm）→ 页面 mm（含绕框中心的旋转） */
const toPage = (o: CanvasObject, pts: XY[]): XY[] =>
  pts.map((p) => rotateAbout([o.x + p[0], o.y + p[1]], [o.x + o.w / 2, o.y + o.h / 2], o.rotationDeg ?? 0))

/** 源对象：沿画布画的那条轮廓采样（SVG 原语的真实几何；与 ShapeView / ArrowView 同一组取值） */
function sourceSamples(o: ShapeObject | ArrowObject): XY[] {
  const sw = Math.max(o.strokePt, 0.05) * MM_PER_PT
  const { w, h } = o
  if (o.type === 'arrow') {
    const g = arrowGeometry(o, w, h, sw)
    return toPage(o, [
      ...lerp([g.p1.x, g.p1.y], [g.p2.x, g.p2.y]),
      ...(g.endHead ? headPoints(g.endHead) : []),
      ...(g.startHead ? headPoints(g.startHead) : []),
    ])
  }
  const inset = shapeInset(o, sw)
  switch (o.shape) {
    case 'rect': {
      const rw = w - 2 * inset
      const rh = h - 2 * inset
      const r = o.cornerRadius ? cornerRadius(o.cornerRadius, rw, rh) : 0
      const [x0, y0, x1, y1] = [inset, inset, inset + rw, inset + rh]
      if (!r) return toPage(o, polyline([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], true))
      return toPage(o, [
        ...lerp([x0 + r, y0], [x1 - r, y0]),
        ...arc([x1 - r, y0 + r], r, r, -Math.PI / 2, 0),
        ...lerp([x1, y0 + r], [x1, y1 - r]),
        ...arc([x1 - r, y1 - r], r, r, 0, Math.PI / 2),
        ...lerp([x1 - r, y1], [x0 + r, y1]),
        ...arc([x0 + r, y1 - r], r, r, Math.PI / 2, Math.PI),
        ...lerp([x0, y1 - r], [x0, y0 + r]),
        ...arc([x0 + r, y0 + r], r, r, Math.PI, 1.5 * Math.PI),
      ])
    }
    case 'ellipse':
      return toPage(o, arc([w / 2, h / 2], w / 2 - inset, h / 2 - inset, 0, 2 * Math.PI, 720))
    case 'line': {
      const { start, end } = lineEndpoints(o)
      return toPage(o, lerp([start.rx * w, start.ry * h], [end.rx * w, end.ry * h]))
    }
    case 'brace': {
      const { start, parts } = braceParts(w, h, inset)
      const out: XY[] = []
      let cur = start
      for (const p of parts) {
        out.push(...(p.kind === 'Q' ? quad(cur, p.c, p.p) : lerp(cur, p.p)))
        cur = p.p
      }
      return toPage(o, out)
    }
    case 'arc': {
      // 弧线本来就是一段三次贝塞尔：按参数方程采样（与 SVG 的 C 同一条曲线）
      const [p0, c1, c2, p3] = arcPoints(w, h, inset)
      const out: XY[] = []
      for (let i = 0; i <= 200; i++) {
        const t = i / 200
        const u = 1 - t
        out.push([
          u * u * u * p0[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t * t * t * p3[0],
          u * u * u * p0[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t * t * t * p3[1],
        ])
      }
      return toPage(o, out)
    }
    default: {
      const ol = shapeOutline(o.shape, w, h, inset, o.sides, o.starRatio)
      if (ol?.kind !== 'poly') throw new Error('不是多边形')
      return toPage(o, polyline(ol.points, true))
    }
  }
}

/** 转出来的路径：描边（有端型时回缩过）+ 端型，页面 mm */
function pathSamples(p: PathObject): XY[] {
  const g = pathGeometry(p, p.w, p.h, Math.max(p.strokePt, 0.05) * MM_PER_PT)
  return toPage(p, [...sampleSegs(g.stroke), ...g.heads.flatMap(headPoints)])
}

/** 点到折线（相邻采样点连成的线段）的最近距离：比的是曲线，不是两组离散点 */
function nearest(p: XY, line: XY[]): number {
  let best = Infinity
  for (let i = 1; i < line.length; i++) {
    const a = line[i - 1]
    const b = line[i]
    const dx = b[0] - a[0]
    const dy = b[1] - a[1]
    const len2 = dx * dx + dy * dy
    let t = len2 ? ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len2 : 0
    t = Math.max(0, Math.min(1, t))
    best = Math.min(best, Math.hypot(p[0] - (a[0] + dx * t), p[1] - (a[1] + dy * t)))
  }
  return best
}
const hausdorff = (a: XY[], b: XY[]) => Math.max(Math.max(...a.map((p) => nearest(p, b))), Math.max(...b.map((p) => nearest(p, a))))

/* ------------------------------ 夹具 ------------------------------------- */

const shape = (shapeKind: ShapeObject['shape'], over: Partial<ShapeObject> = {}): ShapeObject => ({
  id: `s-${shapeKind}`,
  type: 'shape',
  shape: shapeKind,
  x: 20,
  y: 30,
  w: 40,
  h: 25,
  strokePt: 2,
  color: '#224466',
  fill: '#ffcc00',
  ...over,
})
const arrow = (over: Partial<ArrowObject> = {}): ArrowObject => ({
  id: 'a1',
  type: 'arrow',
  x: 10,
  y: 20,
  w: 40,
  h: 18,
  start: { rx: 0, ry: 1 },
  end: { rx: 1, ry: 0 },
  strokePt: 1.5,
  color: '#111111',
  head: 'end',
  ...over,
})

const CASES: [string, ShapeObject | ArrowObject][] = [
  ['矩形', shape('rect')],
  ['圆角矩形', shape('rect', { cornerRadius: 4 })],
  ['无边框圆角矩形', shape('rect', { cornerRadius: 4, stroke: false })],
  ['椭圆', shape('ellipse')],
  ['三角形', shape('triangle')],
  ['菱形', shape('diamond')],
  ['七边形', shape('polygon', { sides: 7 })],
  ['星形（缺省五角）', shape('star')],
  ['七角星、内径比 0.6、旋转 20°', shape('star', { sides: 7, starRatio: 0.6, rotationDeg: 20 })],
  ['弧线', shape('arc', { fill: null })],
  ['旋转 -45° 的弧线', shape('arc', { fill: null, rotationDeg: -45 })],
  ['大括号', shape('brace', { fill: null })],
  ['斜直线', shape('line', { start: { rx: 0, ry: 0 }, end: { rx: 1, ry: 1 } })],
  ['旧直线（无端点字段）', shape('line', { h: 0.01 })],
  ['旋转 37° 的多边形', shape('polygon', { sides: 5, rotationDeg: 37 })],
  ['旋转 -60° 的圆角矩形', shape('rect', { cornerRadius: 3, rotationDeg: -60 })],
  ['旋转 120° 的大括号', shape('brace', { rotationDeg: 120, fill: null })],
  ['旧箭头（head: end）', arrow()],
  ['两端新端型 + 大小档位', arrow({ headStart: 'dot', headEnd: 'stealth', headSizeEnd: 'large' })],
  ['开口 / 短线端型 + 旋转', arrow({ headStart: 'bar', headEnd: 'open', rotationDeg: 25 })],
  ['水平箭头（框高 0.01）', arrow({ h: 0.01, start: { rx: 0, ry: 0.5 }, end: { rx: 1, ry: 0.5 }, head: 'both' })],
]

describe('转为路径：画面逐点不动（双向 Hausdorff < 0.01 mm）', () => {
  it.each(CASES)('%s', (_name, src) => {
    const p = toPathObject(src)!
    expect(p).not.toBeNull()
    expect(p.id).toBe(src.id)
    expect(p.type).toBe('path')
    expect(hausdorff(sourceSamples(src), pathSamples(p))).toBeLessThan(0.01)
  })

  it('包围盒收到曲线实际范围（ADR 0185 §二）：描边内缩的矩形转完框缩一个线宽，比例坐标落在 [0,1]', () => {
    const src = shape('rect')
    const p = toPathObject(src)!
    const sw = 2 * MM_PER_PT
    expect(p.w).toBeCloseTo(src.w - sw, 9)
    expect(p.h).toBeCloseTo(src.h - sw, 9)
    for (const n of p.subpaths[0].nodes) for (const v of n.p) expect(v >= -1e-12 && v <= 1 + 1e-12).toBe(true)
  })

  it('自定义虚线与斜接限制带到路径上（Codex 线程 4614448143）', () => {
    const p = toPathObject(shape('rect', { dashPattern: [4, 2], miterLimit: 2 }))!
    expect(p.dashPattern).toEqual([4, 2])
    expect(p.miterLimit).toBe(2)
  })

  it('样式字段照搬、缺省不写；直线 / 大括号写明原来的尖角', () => {
    const r = toPathObject(shape('rect', { dash: 'dashdot', strokeOpacity: 0.4, lineCap: 'square', fillOpacity: 0.3, name: '框', groupId: 'g1', locked: true }))!
    expect(r).toMatchObject({ dash: 'dashdot', strokeOpacity: 0.4, lineCap: 'square', fillOpacity: 0.3, name: '框', groupId: 'g1', locked: true, fill: '#ffcc00' })
    expect('lineJoin' in r).toBe(false)
    expect('stroke' in r).toBe(false)
    expect('fillRule' in r).toBe(false)
    expect(toPathObject(shape('rect', { stroke: false }))!.stroke).toBe(false)
    expect(toPathObject(shape('brace'))!).toMatchObject({ lineJoin: 'miter', fill: null })
    expect(toPathObject(shape('line'))!.lineJoin).toBe('miter')
    const a = toPathObject(arrow({ headStart: 'dot', headEnd: 'none', headSizeStart: 'small' }))!
    expect(a).toMatchObject({ headStart: 'dot', headSizeStart: 'small', fill: null })
    expect('headEnd' in a).toBe(false)
    // 旧 head 字段按 `arrowHeads` 读成新端型
    expect(toPathObject(arrow({ head: 'both' }))!).toMatchObject({ headStart: 'triangle', headEnd: 'triangle' })
    expect(arrowHeads(arrow({ head: 'both' }))).toEqual({ start: 'triangle', end: 'triangle' })
  })

  it('剪切蒙版暂时关掉（clipOff）：转成路径后仍是关着的（Codex 线程 4614452868）', () => {
    const p = toPathObject(shape('rect', { groupId: 'g1', clipMask: true, clipOff: true, hidden: true }))!
    expect(p).toMatchObject({ clipMask: true, clipOff: true, hidden: true })
    expect('clipOff' in toPathObject(shape('rect', { groupId: 'g1', clipMask: true }))!).toBe(false)
  })

  it('文字 / 面板 / 已经是路径的不能转', () => {
    const t = { id: 't', type: 'text' } as TextObject
    expect(canConvertToPath(t)).toBe(false)
    expect(canConvertToPath(toPathObject(shape('rect'))!)).toBe(false)
    expect(canConvertToPath(shape('ellipse'))).toBe(true)
    expect(canConvertToPath(arrow())).toBe(true)
  })
})

/* ------------------------------ 动作 ------------------------------------- */

async function seed(items: CanvasObject[]) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_topath_' + Math.random())
  useDocumentStore.getState().commit(literal('放对象'), (d) => {
    d.objects.push(...items)
  })
  useDocumentStore.setState({ past: [], future: [] })
}
const objs = () => useDocumentStore.getState().doc.objects

describe('convertToPath：一次 commit、不换 id、数组位置不动', () => {
  it('就地替换：顺序、id、其它对象一个字不动；一条历史；撤销逐字段回去', async () => {
    const t: TextObject = { id: 't', type: 'text', text: 'x', x: 0, y: 0, w: 10, h: 5, sizePt: 8, bold: false, color: '#000', align: 'left' }
    const s = shape('ellipse', { id: 'e' })
    const a = arrow()
    // 文字夹在中间：转换的对象要留在原位（push 到末尾会把它们提到文字上面）
    await seed([s, t, a])
    const before = JSON.parse(JSON.stringify(objs()))
    convertToPath(['e', 'a1', 't'])
    expect(objs().map((o) => `${o.id}:${o.type}`)).toEqual(['e:path', 't:text', 'a1:path'])
    expect(objs()[1]).toEqual(before[1])
    expect(useDocumentStore.getState().past).toHaveLength(1)
    useDocumentStore.getState().undo()
    expect(JSON.parse(JSON.stringify(objs()))).toEqual(before)
  })

  it('一个都转不了：不进历史', async () => {
    const t: TextObject = { id: 't', type: 'text', text: 'x', x: 0, y: 0, w: 10, h: 5, sizePt: 8, bold: false, color: '#000', align: 'left' }
    await seed([t])
    convertToPath(['t'])
    expect(useDocumentStore.getState().past).toHaveLength(0)
  })
})

/* ------------------------------ 菜单 ------------------------------------- */

describe('右键菜单「转为路径」', () => {
  let root: Root
  let el: HTMLDivElement
  beforeEach(() => {
    if (!('ResizeObserver' in globalThis)) {
      Object.defineProperty(globalThis, 'ResizeObserver', {
        value: class {
          observe() {}
          unobserve() {}
          disconnect() {}
        },
        configurable: true,
        writable: true,
      })
    }
    el = document.createElement('div')
    document.body.appendChild(el)
    root = createRoot(el)
  })
  afterEach(async () => {
    await act(async () => {
      useQuickEdit.getState().close()
      root.unmount()
    })
    document.body.innerHTML = ''
    useSelectionStore.getState().clear()
  })
  const open = async (id: string, ids: string[]) => {
    await act(async () => {
      root.render(
        <TooltipProvider>
          <QuickEdit />
        </TooltipProvider>,
      )
    })
    await act(async () => {
      useSelectionStore.getState().set(ids)
      useQuickEdit.getState().open({ kind: 'object', id }, 120, 80)
    })
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0))
    })
  }
  const item = () => document.querySelector<HTMLElement>('[data-quick-item="convert-to-path"]')

  it('形状上有、点了就转；已经是路径的没有', async () => {
    await seed([shape('rect', { id: 'r' })])
    await open('r', ['r'])
    expect(item()?.textContent).toContain('转为路径')
    await act(async () => item()!.click())
    expect(objs()[0].type).toBe('path')
    expect(objs()[0].id).toBe('r')
    await open('r', ['r'])
    expect(item()).toBeNull()
  })

  it('多选里混了文字：不摆（选区全能转才摆）', async () => {
    const t: TextObject = { id: 't', type: 'text', text: 'x', x: 0, y: 0, w: 10, h: 5, sizePt: 8, bold: false, color: '#000', align: 'left' }
    await seed([shape('rect', { id: 'r' }), t])
    await open('r', ['r', 't'])
    expect(document.querySelector('[data-quick-menu="multi"]')).not.toBeNull()
    expect(item()).toBeNull()
  })
})

/* ------------------------------ 画布与消费者 ------------------------------ */

const ring = (over: Partial<PathObject> = {}): PathObject => ({
  id: 'p',
  type: 'path',
  x: 10,
  y: 10,
  w: 40,
  h: 40,
  strokePt: 1,
  color: '#000000',
  fill: '#3366aa',
  fillRule: 'evenodd',
  subpaths: [
    { closed: true, nodes: [{ p: [0, 0] }, { p: [1, 0] }, { p: [1, 1] }, { p: [0, 1] }] },
    { closed: true, nodes: [{ p: [0.25, 0.25] }, { p: [0.75, 0.25] }, { p: [0.75, 0.75] }, { p: [0.25, 0.75] }] },
  ],
  ...over,
})

describe('PathView / 缩略图 / 导出侧消费者', () => {
  beforeEach(() => {
    useViewportStore.setState({ zoom: 1 })
  })

  it('画布：命中层沿真实轮廓、填充区域按 fill-rule；空心的只认描边', () => {
    const html = renderToStaticMarkup(<PathView obj={ring()} hit="stroke" />)
    const doc = new DOMParser().parseFromString(html, 'image/svg+xml')
    const hit = doc.querySelector('[data-hit-path]')!
    expect(hit.getAttribute('fill-rule')).toBe('evenodd')
    expect(hit.getAttribute('style')).toContain('pointer-events:all')
    const hollow = new DOMParser().parseFromString(renderToStaticMarkup(<PathView obj={ring({ fill: null })} hit="stroke" />), 'image/svg+xml')
    expect(hollow.querySelector('[data-hit-path]')!.getAttribute('style')).toContain('pointer-events:stroke')
    // 锁定 / 绘制工具时不出命中层（与形状同一条）
    expect(renderToStaticMarkup(<PathView obj={ring()} hit="none" />)).not.toContain('data-hit-path')
  })

  it('命中的纯函数：evenodd 的洞里点不中、nonzero 点得中；描边按 mm 距离', () => {
    const o = ring()
    const sw = MM_PER_PT
    expect(pathHitTest(o, 5, 5, 0.5, sw)).toBe(true) // 外环与内环之间
    expect(pathHitTest(o, 20, 20, 0.5, sw)).toBe(false) // 洞里
    expect(pathHitTest({ ...o, fillRule: undefined }, 20, 20, 0.5, sw)).toBe(true) // 同向嵌套 nonzero 是实心
    const hollow = ring({ fill: null })
    expect(pathHitTest(hollow, 5, 5, 0.5, sw)).toBe(false)
    expect(pathHitTest(hollow, 0.3, 20, 0.5, sw)).toBe(true) // 离外环 0.3 mm
    expect(pathHitTest(hollow, 1, 20, 0.5, sw)).toBe(false)
  })

  it('有端型时：填充一条、描边 + 端型在一个 <g opacity> 里（与导出的透明组同构）', () => {
    const o: PathObject = { ...ring({ fill: '#ff0000', fillRule: undefined, strokeOpacity: 0.5, headEnd: 'triangle' }), subpaths: [{ closed: false, nodes: [{ p: [0, 0] }, { p: [1, 1] }] }] }
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<svg><ThumbShape obj={o} /></svg>), 'image/svg+xml')
    const g = doc.querySelector('[data-thumb-shape="path"] g g[opacity]')!
    expect(g.getAttribute('opacity')).toBe('0.5')
    expect(g.querySelectorAll('polygon')).toHaveLength(1)
    expect(g.querySelector('path')!.getAttribute('stroke-opacity')).toBeNull()
  })

  it('导出预览 ThumbShape 与画布同一组图元；缩略图草图带子路径时沿轮廓画', () => {
    const html = renderToStaticMarkup(<svg><ThumbShape obj={ring({ rotationDeg: 30 })} /></svg>)
    expect(html).toContain('data-thumb-shape="path"')
    expect(html).toContain('rotate(30 30 30)')
    expect(html).toContain('fill-rule="evenodd"')
    const sketch = renderToStaticMarkup(
      <CanvasThumb page={{ w: 100, h: 80 }} objects={[{ type: 'path', x: 10, y: 10, w: 40, h: 40, subpaths: ring().subpaths }]} />,
    )
    expect(sketch).toMatch(/<path[^>]* d="M 0 0 L 40 0 L 40 40 L 0 40 L 0 0 Z/)
  })

  it('裁切 / EPS 判定 / 写回缩放都认 path', () => {
    const b = visibleContentBounds([ring()])!
    const half = MM_PER_PT / 2
    expect(b.x).toBeCloseTo(10 - half, 9)
    expect(b.w).toBeCloseTo(40 + 2 * half, 9)
    const [payload] = toExportObjects([ring({ fillOpacity: 0.5 })])
    expect(isEpsTranslucent(payload)).toBe(true)
    expect(isEpsTranslucent(toExportObjects([ring()])[0])).toBe(false)
  })
})
