/**
 * 浮动工具条落位的纯函数：上方 / 下方、左右不越界、避让侧栏、宽窄档、
 * 联合选区的屏幕换算与 OverlaySvg 同源。jsdom 没有真实布局，所以位置规则在这里量。
 */
import { describe, expect, it } from 'vitest'
import { RAIL_W } from '@/store/uiStore'
import { mmToWorld } from '@/store/viewportStore'
import {
  FULL_BAR_MIN_WIDTH,
  MARGIN,
  TOP_SAFE,
  barVariant,
  freeWidthOf,
  placeToolbar,
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
