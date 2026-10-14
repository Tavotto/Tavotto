/**
 * 图内编辑跨图对齐的候选与配对（ADR 0128 §二，PR2）。
 *
 * 钉住的事实：别的图每种元素只出表里那几条边（子图 / 色条 / 图例四边，轴标题 / 图标题中线，
 * 普通文字左边与中线）；隐藏 / 旋转 / 翻转 / 无权威 manifest / 视口外 / 裁剪窗外 / 正在编辑的
 * 面板与元素不出线；配对要 kind 匹配且同一条边，x / y 轴标题不互配，色条只配子图的上下边。
 */
import { describe, expect, it } from 'vitest'

import type { Manifest, ManifestElement } from '@/lib/api'
import type { CanvasObject, PanelObject } from '@/types/document'
import { visibleRectMm } from '@/store/viewportStore'
import { crossFigurePair, crossFigureSnapCandidates } from './elementGeom'
import type { SnapLine } from './geometry'

const panelAt = (id: string, x: number, extra: Partial<PanelObject> = {}): PanelObject =>
  ({
    id,
    type: 'panel',
    x,
    y: 0,
    w: 100,
    h: 100,
    fileId: `${id}.pdf`,
    fileKind: 'pdf',
    nativeW: 100,
    nativeH: 100,
    script: 'fig.py',
    overrides: [],
    ...extra,
  }) as unknown as PanelObject

const axes = (gid: string, pos: [number, number, number, number], extra = {}): ManifestElement => ({
  gid,
  role: 'axes',
  label: gid,
  bbox: [pos[0], 1 - pos[1] - pos[3], pos[2], pos[3]],
  editable: [{ prop: 'position', type: 'rect', value: pos }],
  draggable: false,
  resizable: true,
  ...extra,
})

const textLike = (
  gid: string,
  role: string,
  bbox: [number, number, number, number],
  extra = {},
): ManifestElement => ({
  gid,
  role,
  label: gid,
  bbox,
  editable: [],
  draggable: true,
  anchor: [bbox[0], bbox[1] + bbox[3]],
  drag_prop: 'pos_frac',
  ...extra,
})

// B 面板在页面 x = 200..300。元素页面 mm = 200 + 分数 × 100
const manifestB = (): Manifest => ({
  stem: 'B',
  size_mm: [100, 100],
  elements: [
    { gid: 'figure', role: 'figure', label: '整图', bbox: [0, 0, 1, 1], editable: [], draggable: false },
    axes('axes_0', [0.1, 0.2, 0.5, 0.6]),
    axes('axes_1', [0.7, 0.2, 0.05, 0.6], { is_colorbar: true }),
    textLike('axes_0.legend', 'legend', [0.62, 0.1, 0.2, 0.1]),
    textLike('axes_0.xlabel', 'text', [0.3, 0.9, 0.2, 0.05]),
    textLike('axes_0.ylabel', 'text', [0.02, 0.4, 0.04, 0.2]),
    textLike('axes_0.title', 'title', [0.3, 0.02, 0.2, 0.05]),
    textLike('axes_0.texts_0', 'text', [0.15, 0.3, 0.1, 0.03]),
    // 不参与：刻度标签
    { gid: 'axes_0.xtick_0', role: 'tick', label: '刻度', bbox: [0.1, 0.8, 0.02, 0.02], editable: [], draggable: false },
  ],
})

const A = panelAt('a', 0)
const B = panelAt('b', 200)
const exact = (m: Manifest | null) => () => m
const cross = (
  objects: CanvasObject[],
  getManifest: (p: PanelObject) => Manifest | null = exact(manifestB()),
  visible: { x: number; y: number; w: number; h: number } | null = null,
) => crossFigureSnapCandidates(objects, getManifest, 'a', visible)

const linesOf = (ls: SnapLine[], gid: string) =>
  ls.filter((l) => l.gid === gid).map((l) => [l.edge, Math.round(l.v * 1e6) / 1e6])

describe('crossFigureSnapCandidates：每种元素出哪几条边', () => {
  it('子图 / 色条 / 图例：四条边（左右、上下）；页面 mm、带 panelId / gid / rect / kind', () => {
    const c = cross([A, B])
    for (const gid of ['axes_0', 'axes_1', 'axes_0.legend']) {
      expect(linesOf(c.xs, gid).map((l) => l[0])).toEqual([0, 2])
      expect(linesOf(c.ys, gid).map((l) => l[0])).toEqual([0, 2])
    }
    expect(linesOf(c.xs, 'axes_0')).toEqual([
      [0, 210],
      [2, 260],
    ])
    expect(linesOf(c.ys, 'axes_0')).toEqual([
      [0, 20],
      [2, 80],
    ])
    const l = c.xs.find((x) => x.gid === 'axes_1')!
    expect(l).toMatchObject({ kind: 'colorbar', panelId: 'b' })
    expect(l.rect!.x).toBeCloseTo(270)
    expect(l.rect!.y).toBeCloseTo(20)
    expect(l.rect!.w).toBeCloseTo(5)
    expect(l.rect!.h).toBeCloseTo(60)
    expect(c.xs.find((x) => x.gid === 'axes_0.legend')?.kind).toBe('legend')
  })

  it('x / y 轴标题、图标题：只出中线（x 与 y 两个方向）', () => {
    const c = cross([A, B])
    for (const [gid, kind] of [
      ['axes_0.xlabel', 'xlabel'],
      ['axes_0.ylabel', 'ylabel'],
      ['axes_0.title', 'title'],
    ] as const) {
      expect(linesOf(c.xs, gid).map((l) => l[0])).toEqual([1])
      expect(linesOf(c.ys, gid).map((l) => l[0])).toEqual([1])
      expect(c.xs.find((x) => x.gid === gid)?.kind).toBe(kind)
    }
    expect(linesOf(c.xs, 'axes_0.xlabel')[0][1]).toBeCloseTo(240)
  })

  it('普通文字：左边与中线（x），中线（y）', () => {
    const c = cross([A, B])
    expect(linesOf(c.xs, 'axes_0.texts_0').map((l) => l[0])).toEqual([0, 1])
    expect(linesOf(c.ys, 'axes_0.texts_0').map((l) => l[0])).toEqual([1])
  })

  it('刻度与整图不出线', () => {
    const c = cross([A, B])
    const gids = new Set([...c.xs, ...c.ys].map((l) => l.gid))
    expect(gids.has('axes_0.xtick_0')).toBe(false)
    expect(gids.has('figure')).toBe(false)
  })

  it('隐藏的元素、位图代理、独立箭头不出线', () => {
    const m = manifestB()
    m.elements.push(
      axes('axes_9', [0.1, 0.1, 0.1, 0.1], { editable: [{ prop: 'visible', type: 'bool', value: false }, { prop: 'position', type: 'rect', value: [0.1, 0.1, 0.1, 0.1] }] }),
      axes('im_0', [0.1, 0.1, 0.1, 0.1], { geom_gid: 'axes_0' }),
      textLike('arrow_0', 'patch', [0.1, 0.1, 0.1, 0.1], { arrow_endpoints: [[0, 0], [1, 1]] }),
    )
    const c = cross([A, B], exact(m))
    const gids = new Set(c.xs.map((l) => l.gid))
    for (const g of ['axes_9', 'im_0', 'arrow_0']) expect(gids.has(g)).toBe(false)
  })

  it('裁剪窗外的元素（中心不在自己面板里）不出线', () => {
    const m = manifestB()
    m.elements.push(textLike('axes_0.texts_far', 'text', [1.2, 0.3, 0.1, 0.03]))
    const c = cross([A, B], exact(m))
    expect(c.xs.some((l) => l.gid === 'axes_0.texts_far')).toBe(false)
  })
})

describe('crossFigureSnapCandidates：哪些面板参与', () => {
  const has = (c: ReturnType<typeof cross>) => c.xs.length + c.ys.length > 0

  it('正在编辑的面板自己不出线', () => {
    expect(has(cross([A]))).toBe(false)
  })
  it('隐藏 / 旋转 / 翻转的面板不参与', () => {
    expect(has(cross([A, panelAt('b', 200, { hidden: true })]))).toBe(false)
    expect(has(cross([A, panelAt('b', 200, { rotation: 90 } as Partial<PanelObject>)]))).toBe(false)
    expect(has(cross([A, panelAt('b', 200, { flipH: true })]))).toBe(false)
    expect(has(cross([A, panelAt('b', 200, { flipV: true })]))).toBe(false)
  })
  it('拿不到权威 manifest 的面板不参与', () => {
    expect(has(cross([A, B], exact(null)))).toBe(false)
  })
  it('非面板对象不参与', () => {
    const t = { id: 't', type: 'text', x: 200, y: 0, w: 10, h: 10 } as unknown as CanvasObject
    expect(has(cross([A, t]))).toBe(false)
  })
  it('外框与视口可见矩形不相交的面板不参与；相交即参与；视口未知（null）不剔', () => {
    expect(has(cross([A, B], exact(manifestB()), { x: 0, y: 0, w: 150, h: 100 }))).toBe(false)
    expect(has(cross([A, B], exact(manifestB()), { x: 250, y: 0, w: 150, h: 100 }))).toBe(true)
    expect(has(cross([A, B], exact(manifestB()), null))).toBe(true)
  })
})

describe('visibleRectMm：视口 → 页面 mm', () => {
  it('缩放与平移换算；视口尺寸未知回 null', () => {
    // BASE_PX_PER_MM 换算在 viewportStore 内；zoom 2、pan 为负 = 画面向左下平移
    const r = visibleRectMm({ zoom: 1, panX: 0, panY: 0 }, 800, 600)!
    const r2 = visibleRectMm({ zoom: 2, panX: 0, panY: 0 }, 800, 600)!
    expect(r2.w).toBeCloseTo(r.w / 2)
    expect(r2.h).toBeCloseTo(r.h / 2)
    const shifted = visibleRectMm({ zoom: 1, panX: -100, panY: -50 }, 800, 600)!
    expect(shifted.x).toBeGreaterThan(r.x)
    expect(shifted.y).toBeGreaterThan(r.y)
    expect(shifted.w).toBeCloseTo(r.w)
    expect(visibleRectMm({ zoom: 1, panX: 0, panY: 0 }, 0, 0)).toBeNull()
  })
})

describe('crossFigurePair：配对过滤', () => {
  const mk = (kind: SnapLine['kind'], edge: SnapLine['edge'], panelId?: string): SnapLine => ({
    v: 0,
    kind,
    edge,
    panelId,
  })
  const xLine = mk('axes', 0, 'b')
  const yLine = mk('axes', 0, 'b')
  const pair = (selfKind: Parameters<typeof crossFigurePair>[0], xs: SnapLine[] = [xLine]) =>
    crossFigurePair(selfKind, 'a', xs)

  it('本图的线与参考线全收（边、类型都不论）', () => {
    const p = pair('legend')
    expect(p(1, mk('text', 2, 'a'))).toBe(true)
    expect(p(0, mk('guide', null))).toBe(true)
    expect(p(2, mk('figure', 1))).toBe(true)
  })
  it('别的图：kind 不同不配', () => {
    expect(pair('legend')(0, mk('axes', 0, 'b'))).toBe(false)
    expect(pair('axes')(0, mk('legend', 0, 'b'))).toBe(false)
    expect(pair('text')(1, mk('title', 1, 'b'))).toBe(false)
  })
  it('别的图：同 kind 但边不同不配，同边才配', () => {
    expect(pair('legend')(0, mk('legend', 2, 'b'))).toBe(false)
    expect(pair('legend')(2, mk('legend', 2, 'b'))).toBe(true)
    expect(pair('text')(0, mk('text', 1, 'b'))).toBe(false)
  })
  it('x 轴标题与 y 轴标题、图标题互不相配', () => {
    expect(pair('xlabel')(1, mk('ylabel', 1, 'b'))).toBe(false)
    expect(pair('ylabel')(1, mk('xlabel', 1, 'b'))).toBe(false)
    expect(pair('xlabel')(1, mk('title', 1, 'b'))).toBe(false)
    expect(pair('xlabel')(1, mk('xlabel', 1, 'b'))).toBe(true)
  })
  it('色条：配色条的四条边；配子图只配上下边（y），不配左右（x）', () => {
    const p = pair('colorbar', [xLine])
    expect(p(0, mk('colorbar', 0, 'b'))).toBe(true)
    expect(p(0, yLine)).toBe(true)
    expect(p(0, xLine)).toBe(false)
    // 子图不能反过来配色条
    expect(pair('axes')(0, mk('colorbar', 0, 'b'))).toBe(false)
  })
})
