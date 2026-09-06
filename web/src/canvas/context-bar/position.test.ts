/**
 * 浮动工具条落位的纯函数：上方 / 下方、左右不越界、避让侧栏、宽窄档、
 * 联合选区的屏幕换算与 OverlaySvg 同源。jsdom 没有真实布局，所以位置规则在这里量。
 */
import { describe, expect, it } from 'vitest'
import { RAIL_W } from '@/store/uiStore'
import { mmToWorld } from '@/store/viewportStore'
import {
  COVER_SENSITIVE_ROLES,
  FULL_BAR_MIN_WIDTH,
  MARGIN,
  TOP_SAFE,
  barVariant,
  bboxToScreen,
  elementObstacles,
  freeWidthOf,
  placeToolbar,
  placeToolbarAvoiding,
  selectionScreenRect,
  sidebarInsets,
} from './position'

const vp = { width: 1200, height: 800 }
const size = { w: 300, h: 32 }
const none = { left: 0, right: 0 }

describe('placeToolbar', () => {
  it('默认贴在锚点上方、水平居中', () => {
    const p = placeToolbar({ left: 400, top: 300, width: 200, height: 100 }, size, vp, none)
    expect(p).toEqual({ x: 400 + 100 - 150, y: 300 - 32 - MARGIN, placement: 'above' })
  })

  it('顶部安全区放不下就翻到下方', () => {
    const p = placeToolbar({ left: 400, top: TOP_SAFE + 10, width: 200, height: 100 }, size, vp, none)
    expect(p.placement).toBe('below')
    expect(p.y).toBe(TOP_SAFE + 10 + 100 + MARGIN)
  })

  it('刚好放得下就仍在上方（边界取 TOP_SAFE 本身）', () => {
    const top = TOP_SAFE + size.h + MARGIN
    expect(placeToolbar({ left: 0, top, width: 10, height: 10 }, size, vp, none).placement).toBe('above')
    expect(placeToolbar({ left: 0, top: top - 1, width: 10, height: 10 }, size, vp, none).placement).toBe('below')
  })

  it('下方也放不下时贴窗口底边', () => {
    const p = placeToolbar({ left: 0, top: 40, width: 10, height: 900 }, size, vp, none)
    expect(p.placement).toBe('below')
    expect(p.y).toBe(vp.height - size.h - MARGIN)
  })

  it('左右不越界：靠左贴 MARGIN、靠右贴右边缘', () => {
    expect(placeToolbar({ left: -500, top: 300, width: 10, height: 10 }, size, vp, none).x).toBe(MARGIN)
    expect(placeToolbar({ left: 5000, top: 300, width: 10, height: 10 }, size, vp, none).x).toBe(
      vp.width - size.w - MARGIN,
    )
  })

  it('避让停靠的侧栏：左边界 = 左栏右沿 + MARGIN，右边界 = 右栏左沿 − 宽 − MARGIN', () => {
    const insets = { left: RAIL_W + 300, right: 360 }
    const left = placeToolbar({ left: -500, top: 300, width: 10, height: 10 }, size, vp, insets)
    expect(left.x).toBe(RAIL_W + 300 + MARGIN)
    const right = placeToolbar({ left: 5000, top: 300, width: 10, height: 10 }, size, vp, insets)
    expect(right.x).toBe(vp.width - 360 - size.w - MARGIN)
  })

  it('两侧之间比工具条还窄时贴左栏内侧，不撕成两半', () => {
    const p = placeToolbar({ left: 500, top: 300, width: 10, height: 10 }, { w: 2000, h: 32 }, vp, {
      left: 100,
      right: 100,
    })
    expect(p.x).toBe(100 + MARGIN)
  })
})

describe('sidebarInsets', () => {
  const base = { leftOpen: true, leftWidth: 300, rightOpen: true, rightWidth: 360 }
  it('停靠布局下开着的侧栏占位（左栏含图标轨道）', () => {
    expect(sidebarInsets({ layout: 'wide', ...base })).toEqual({ left: RAIL_W + 300, right: 360 })
    expect(sidebarInsets({ layout: 'medium', ...base, rightOpen: false })).toEqual({
      left: RAIL_W + 300,
      right: 0,
    })
  })
  it('narrow 断点的侧栏是覆盖层，不占位（那时工具条整个让位）', () => {
    expect(sidebarInsets({ layout: 'narrow', ...base })).toEqual({ left: 0, right: 0 })
  })
  it('关着的不占位', () => {
    expect(sidebarInsets({ layout: 'wide', ...base, leftOpen: false, rightOpen: false })).toEqual(none)
  })
})

describe('barVariant', () => {
  it('侧栏之间够宽是完整栏，不够就压缩', () => {
    expect(barVariant(FULL_BAR_MIN_WIDTH)).toBe('full')
    expect(barVariant(FULL_BAR_MIN_WIDTH - 1)).toBe('compact')
  })
  it('可用宽度 = 窗口宽 − 两侧占位', () => {
    expect(freeWidthOf(1200, { left: 344, right: 360 })).toBe(496)
    expect(barVariant(freeWidthOf(1200, { left: 344, right: 360 }))).toBe('compact')
    expect(barVariant(freeWidthOf(1440, { left: 344, right: 360 }))).toBe('full')
  })
})

describe('selectionScreenRect', () => {
  it('mm → 窗口坐标：原点 + 平移 + 世界像素 × 缩放；尺寸只乘缩放', () => {
    const t = { zoom: 2, panX: 10, panY: 20, originX: 100, originY: 50 }
    const r = selectionScreenRect({ x: 10, y: 20, w: 30, h: 40 }, t)
    expect(r.left).toBeCloseTo(100 + 10 + mmToWorld(10) * 2)
    expect(r.top).toBeCloseTo(50 + 20 + mmToWorld(20) * 2)
    expect(r.width).toBeCloseTo(mmToWorld(30) * 2)
    expect(r.height).toBeCloseTo(mmToWorld(40) * 2)
  })
  it('缩放翻倍，尺寸翻倍、离原点的距离翻倍', () => {
    const a = selectionScreenRect({ x: 10, y: 10, w: 10, h: 10 }, { zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0 })
    const b = selectionScreenRect({ x: 10, y: 10, w: 10, h: 10 }, { zoom: 2, panX: 0, panY: 0, originX: 0, originY: 0 })
    expect(b.left).toBeCloseTo(a.left * 2)
    expect(b.width).toBeCloseTo(a.width * 2)
  })
})

/* ============================== 避让别的文字 ============================== */

describe('placeToolbarAvoiding：不盖住同一张图里别的文字', () => {
  // 一张图：容器 (100,200) 400×300；标题在顶部，图例项在标题正下方
  const zone = { left: 100, top: 200, width: 400, height: 300 }
  const title = { left: 200, top: 210, width: 200, height: 24 }
  const entry = { left: 220, top: 250, width: 120, height: 16 }
  const nextEntry = { left: 220, top: 272, width: 120, height: 16 }

  it('没有障碍物时与 placeToolbar 一字不差', () => {
    const anchor = { left: 400, top: 300, width: 200, height: 100 }
    expect(placeToolbarAvoiding(anchor, size, vp, none, { obstacles: [] })).toEqual(
      placeToolbar(anchor, size, vp, none),
    )
    const low = { left: 400, top: TOP_SAFE + 10, width: 200, height: 100 }
    expect(placeToolbarAvoiding(low, size, vp, none, { obstacles: [] })).toEqual(
      placeToolbar(low, size, vp, none),
    )
  })

  it('审计 21-legend-entry 的形状：贴上方会压住标题 → 贴下方', () => {
    const p = placeToolbarAvoiding(entry, size, vp, none, { obstacles: [title], zone })
    expect(p.placement).toBe('below')
    expect(p.y).toBe(entry.top + entry.height + MARGIN)
  })

  it('上方压标题、下方压下一条图例项 → 退到整张图的上方', () => {
    const p = placeToolbarAvoiding(entry, size, vp, none, { obstacles: [title, nextEntry], zone })
    expect(p.placement).toBe('above')
    expect(p.y).toBe(zone.top - size.h - MARGIN)
    // 落下来的矩形与两个障碍物都不相交
    const rect = { left: p.x, top: p.y, width: size.w, height: size.h }
    for (const o of [title, nextEntry]) {
      const overlapX = rect.left < o.left + o.width && rect.left + rect.width > o.left
      const overlapY = rect.top < o.top + o.height && rect.top + rect.height > o.top
      expect(overlapX && overlapY).toBe(false)
    }
  })

  it('图的上方进了顶部安全区就退到图的下方', () => {
    const highZone = { ...zone, top: TOP_SAFE + 10 }
    const t = { ...title, top: highZone.top + 10 }
    const e = { ...entry, top: highZone.top + 50 }
    const n = { ...nextEntry, top: highZone.top + 72 }
    const p = placeToolbarAvoiding(e, size, vp, none, { obstacles: [t, n], zone: highZone })
    expect(p.placement).toBe('below')
    expect(p.y).toBe(highZone.top + highZone.height + MARGIN)
  })

  it('四个候选位都盖到东西时取重叠面积最小的那个，而不是第一个', () => {
    // 障碍物把锚点上下、图的上下全占满，只有图的下方只被削掉一小条
    const wall = (top: number, height: number) => ({ left: 0, top, width: vp.width, height })
    const aboveAnchor = wall(entry.top - size.h - MARGIN, size.h)
    const belowAnchor = wall(entry.top + entry.height + MARGIN, size.h)
    const aboveZone = wall(zone.top - size.h - MARGIN, size.h)
    const belowZoneSliver = wall(zone.top + zone.height + MARGIN, 4)
    const p = placeToolbarAvoiding(entry, size, vp, none, {
      obstacles: [aboveAnchor, belowAnchor, aboveZone, belowZoneSliver],
      zone,
    })
    expect(p.y).toBe(zone.top + zone.height + MARGIN)
  })

  it('一个候选位都放不下（窗口太矮）就退回 placeToolbar 的夹边逻辑', () => {
    const tiny = { width: 1200, height: TOP_SAFE + 20 }
    const anchor = { left: 100, top: TOP_SAFE + 2, width: 50, height: 10 }
    expect(placeToolbarAvoiding(anchor, size, tiny, none, { obstacles: [title], zone })).toEqual(
      placeToolbar(anchor, size, tiny, none),
    )
  })

  it('水平仍居中于锚点并避让侧栏（与 placeToolbar 同一条规则）', () => {
    const insets = { left: RAIL_W + 300, right: 360 }
    const far = { left: 5000, top: 400, width: 10, height: 10 }
    expect(placeToolbarAvoiding(far, size, vp, insets, { obstacles: [] }).x).toBe(
      vp.width - 360 - size.w - MARGIN,
    )
  })
})

describe('elementObstacles：哪些元素算障碍物', () => {
  const host = { left: 100, top: 200, width: 400, height: 300 }
  const el = (gid: string, role: string, bbox: [number, number, number, number]) => ({ gid, role, bbox })

  it('bbox 是占整图的分数、y 向下，映到容器上', () => {
    expect(bboxToScreen([0.25, 0.1, 0.5, 0.2], host)).toEqual({
      left: 200,
      top: 230,
      width: 200,
      height: 60,
    })
  })

  it('只有文字类角色算；锚点自己与隐藏的不算；曲线 / 子图这种大块不算', () => {
    const elements = [
      el('axes_0', 'axes', [0, 0, 1, 1]),
      el('axes_0.title', 'title', [0.3, 0.02, 0.4, 0.08]),
      el('axes_0.legend.texts_0', 'legend_text', [0.3, 0.2, 0.3, 0.05]),
      el('axes_0.legend.texts_1', 'legend_text', [0.3, 0.26, 0.3, 0.05]),
      el('axes_0.lines_0', 'line', [0.1, 0.1, 0.8, 0.8]),
      el('axes_0.xlabel', 'axis_label', [0.4, 0.9, 0.2, 0.06]),
    ]
    const out = elementObstacles(
      elements,
      host,
      'axes_0.legend.texts_0',
      (e) => e.gid === 'axes_0.xlabel',
    )
    expect(out).toEqual([
      bboxToScreen([0.3, 0.02, 0.4, 0.08], host),
      bboxToScreen([0.3, 0.26, 0.3, 0.05], host),
    ])
  })

  it('障碍物角色表覆盖文字 / 图例 / 刻度 / 色条，不含曲线与子图', () => {
    for (const r of ['title', 'text', 'axis_label', 'legend_text', 'legend', 'ticks', 'ticklabel', 'colorbar']) {
      expect(COVER_SENSITIVE_ROLES.has(r), r).toBe(true)
    }
    for (const r of ['line', 'axes', 'axes3d', 'scatter', 'fill', 'image', 'figure']) {
      expect(COVER_SENSITIVE_ROLES.has(r), r).toBe(false)
    }
  })
})
