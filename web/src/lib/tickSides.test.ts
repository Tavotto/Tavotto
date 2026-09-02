/**
 * 坐标轴边框的语义命中区与四边刻度模型（Prompt 16）。
 *
 * 几何：inner / outer / neutral 三带按**屏幕像素**定宽——zoom 变了带不变；
 * 角落并列时结果确定；偏出去的边框命中区跟着线走；没有可见目标的边不设命中区。
 * 状态：per-side 的 inward / outward 由「边显不显示 × 轴的方向」派生；一次
 * 点击的计划必须把 matplotlib「方向是整条轴的」这条边界说出来（coupled）。
 */
import { describe, expect, it } from 'vitest'

import type { Manifest, ManifestElement, SpineGeom, SpineSide } from './api'
import {
  ZONE_PX,
  ZONE_PX_TOUCH,
  axisChoice,
  axisChoicePlan,
  pickSpineZone,
  readAxesTickModel,
  sideVisiblePlan,
  spineZoneAt,
  toggleSidePlan,
  zoneRectFrac,
  zoneWidthsFor,
  type AxesTickModel,
} from './tickSides'

/** 100×80 mm 的图在 zoom=1 时的屏幕像素（与 PanelView 的 layout × zoom 同口径） */
const SCALE = { pxPerFracX: 400, pxPerFracY: 320 }
const px = (n: number, axis: 'x' | 'y') => n / (axis === 'x' ? SCALE.pxPerFracX : SCALE.pxPerFracY)

/** 框 [0.1, 0.1, 0.8, 0.8]，四条边都在框上 */
const box = (over: Partial<Record<SpineSide, Partial<SpineGeom>>> = {}) => {
  const base: Record<SpineSide, SpineGeom> = {
    bottom: { visible: true, ticks: true, from: [0.1, 0.9], to: [0.9, 0.9] },
    top: { visible: true, ticks: false, from: [0.1, 0.1], to: [0.9, 0.1] },
    left: { visible: true, ticks: true, from: [0.1, 0.9], to: [0.1, 0.1] },
    right: { visible: true, ticks: false, from: [0.9, 0.9], to: [0.9, 0.1] },
  }
  for (const k of Object.keys(over) as SpineSide[]) base[k] = { ...base[k], ...over[k] }
  return base
}

describe('三带分类：每条边的 inner / outer / neutral', () => {
  const spines = box()
  const cases: [SpineSide, number, number, string][] = [
    // 下边 y=0.9：线上方（y 更小）是框里
    ['bottom', 0.5, 0.9 - px(5, 'y'), 'inner'],
    ['bottom', 0.5, 0.9 + px(5, 'y'), 'outer'],
    ['bottom', 0.5, 0.9 + px(1, 'y'), 'neutral'],
    // 上边 y=0.1：线下方是框里
    ['top', 0.5, 0.1 + px(5, 'y'), 'inner'],
    ['top', 0.5, 0.1 - px(5, 'y'), 'outer'],
    // 左边 x=0.1：线右方是框里
    ['left', 0.1 + px(5, 'x'), 0.5, 'inner'],
    ['left', 0.1 - px(5, 'x'), 0.5, 'outer'],
    // 右边 x=0.9：线左方是框里
    ['right', 0.9 - px(5, 'x'), 0.5, 'inner'],
    ['right', 0.9 + px(5, 'x'), 0.5, 'outer'],
    ['right', 0.9, 0.5, 'neutral'],
  ]
  it.each(cases)('%s 边 (%f, %f) → %s', (side, fx, fy, zone) => {
    const hit = spineZoneAt(spines, fx, fy, SCALE)
    expect(hit?.side).toBe(side)
    expect(hit?.zone).toBe(zone)
  })

  it('离线超过带宽就不命中；带外一点也不命中（不会吃掉整张图的点击）', () => {
    expect(spineZoneAt(spines, 0.5, 0.9 - px(ZONE_PX.band + 1, 'y'), SCALE)).toBeNull()
    expect(spineZoneAt(spines, 0.5, 0.5, SCALE)).toBeNull()
    // 沿线方向出了端点 + 带宽也不命中
    expect(spineZoneAt(spines, 0.9 + px(ZONE_PX.band + 1, 'x'), 0.9 + px(5, 'y'), SCALE)).toBeNull()
  })
})

describe('带宽按屏幕像素稳定：zoom 变了带不变', () => {
  const spines = box()
  it.each([0.25, 0.5, 1, 3, 8])('zoom=%f：离线 5 屏幕像素总在带里、25 像素总在带外', (zoom) => {
    const scale = { pxPerFracX: 400 * zoom, pxPerFracY: 320 * zoom }
    const inside = spineZoneAt(spines, 0.5, 0.9 - 5 / scale.pxPerFracY, scale)
    expect(inside?.zone).toBe('inner')
    const outside = spineZoneAt(spines, 0.5, 0.9 - 25 / scale.pxPerFracY, scale)
    expect(outside).toBeNull()
    // 高亮条与命中带同一把尺：厚度换算回屏幕像素恒等于 band - neutral
    const r = zoneRectFrac('bottom', spines.bottom, 'inner', scale)
    expect(r.h * scale.pxPerFracY).toBeCloseTo(ZONE_PX.band - ZONE_PX.neutral, 6)
    expect(r.y + r.h).toBeCloseTo(0.9 - ZONE_PX.neutral / scale.pxPerFracY, 9)
  })

  it('触控的带更宽，鼠标 / 触控笔用窄带', () => {
    expect(zoneWidthsFor('touch')).toBe(ZONE_PX_TOUCH)
    expect(zoneWidthsFor('mouse')).toBe(ZONE_PX)
    expect(zoneWidthsFor('pen')).toBe(ZONE_PX)
    expect(zoneWidthsFor(undefined)).toBe(ZONE_PX)
    const far = spineZoneAt(spines, 0.5, 0.9 + px(14, 'y'), SCALE, ZONE_PX_TOUCH)
    expect(far?.zone).toBe('outer')
    expect(spineZoneAt(spines, 0.5, 0.9 + px(14, 'y'), SCALE, ZONE_PX)).toBeNull()
  })
})

describe('角落：两条边并列时结果确定', () => {
  const spines = box()
  it('离哪条边更近就是哪条', () => {
    // 左下角内侧：离下边 3px、离左边 6px → 下边
    const a = spineZoneAt(spines, 0.1 + px(6, 'x'), 0.9 - px(3, 'y'), SCALE)
    expect(a).toMatchObject({ side: 'bottom', zone: 'inner' })
    const b = spineZoneAt(spines, 0.1 + px(3, 'x'), 0.9 - px(6, 'y'), SCALE)
    expect(b).toMatchObject({ side: 'left', zone: 'inner' })
  })
  it('等距时先取画着刻度的那条，再按固定次序', () => {
    // 右上角外侧、等距：上边与右边都没有刻度 → 固定次序里 top 在 right 前
    const c = spineZoneAt(spines, 0.9 + px(5, 'x'), 0.1 - px(5, 'y'), SCALE)
    expect(c?.side).toBe('top')
    // 右边开了刻度 → 右边赢
    const d = spineZoneAt(box({ right: { ticks: true } }), 0.9 + px(5, 'x'), 0.1 - px(5, 'y'), SCALE)
    expect(d?.side).toBe('right')
  })
})

describe('几何边界', () => {
  it('偏出去的边框：命中区跟着线走，不留在框上', () => {
    const spines = box({ left: { from: [0.05, 0.9], to: [0.05, 0.1] } })
    expect(spineZoneAt(spines, 0.05 - px(5, 'x'), 0.5, SCALE)).toMatchObject({ side: 'left', zone: 'outer' })
    expect(spineZoneAt(spines, 0.05 + px(5, 'x'), 0.5, SCALE)).toMatchObject({ side: 'left', zone: 'inner' })
    // 框的左沿（0.1）此刻是空白：离偏出去的线 20px，不命中
    expect(spineZoneAt(spines, 0.1 + px(1, 'x'), 0.5, SCALE)).toBeNull()
  })
  it('隐藏的边框但有刻度：仍可点（用户看得见那排刻度）；两者都没有：不设命中区', () => {
    const withTicks = box({ top: { visible: false, ticks: true } })
    expect(spineZoneAt(withTicks, 0.5, 0.1 - px(5, 'y'), SCALE)?.side).toBe('top')
    const nothing = box({ top: { visible: false, ticks: false } })
    expect(spineZoneAt(nothing, 0.5, 0.1 - px(5, 'y'), SCALE)).toBeNull()
  })
  it('没有 spines（极坐标 / 3D）就没有命中', () => {
    expect(spineZoneAt(undefined, 0.5, 0.9, SCALE)).toBeNull()
  })
  it('端点顺序任意（左边是从下往上给的）', () => {
    const spines = box({ left: { from: [0.1, 0.1], to: [0.1, 0.9] } })
    expect(spineZoneAt(spines, 0.1 + px(5, 'x'), 0.3, SCALE)?.zone).toBe('inner')
  })
})

/* ------------------------------------------------------------------------ */

const f = (prop: string, value: unknown, extra: Record<string, unknown> = {}) =>
  ({ prop, type: typeof value === 'boolean' ? 'bool' : 'enum', value, ...extra }) as never

function manifestWith(opts: {
  sides?: Partial<Record<SpineSide, boolean>>
  xdir?: string
  ydir?: string
  noYTicks?: boolean
  spines?: Partial<Record<SpineSide, SpineGeom>>
  extra?: ManifestElement[]
}): Manifest {
  const sides = { bottom: true, top: false, left: true, right: false, ...(opts.sides ?? {}) }
  const axes = {
    gid: 'axes_0',
    role: 'axes',
    label: '子图',
    bbox: [0.1, 0.1, 0.8, 0.8],
    draggable: false,
    editable: (Object.keys(sides) as SpineSide[]).map((s) => f(`ticks_${s}`, sides[s])),
    spines: opts.spines ?? box({ top: { ticks: sides.top }, right: { ticks: sides.right } }),
  } as unknown as ManifestElement
  const xt = {
    gid: 'axes_0.xticks',
    role: 'ticks',
    label: 'X',
    bbox: [0.1, 0.92, 0.8, 0.05],
    draggable: false,
    editable: [f('direction', opts.xdir ?? 'out', { options: ['out', 'in', 'inout'] })],
  } as unknown as ManifestElement
  const yt = {
    gid: 'axes_0.yticks',
    role: 'ticks',
    label: 'Y',
    bbox: [0.03, 0.1, 0.05, 0.8],
    draggable: false,
    editable: [f('direction', opts.ydir ?? 'out', { options: ['out', 'in', 'inout'] })],
  } as unknown as ManifestElement
  return {
    size_mm: [100, 80],
    elements: [
      { gid: 'figure', role: 'figure', bbox: [0, 0, 1, 1], editable: [], draggable: false },
      axes,
      xt,
      ...(opts.noYTicks ? [] : [yt]),
      ...(opts.extra ?? []),
    ],
  } as unknown as Manifest
}

const model = (m: Manifest, overrides: { gid: string; prop: string; value: unknown }[] = []) =>
  readAxesTickModel(m, overrides, 'axes_0') as AxesTickModel

describe('四边刻度模型：inward / outward 由「边 × 轴方向」派生', () => {
  it('默认：下 / 左朝外；上 / 右隐藏', () => {
    const m = model(manifestWith({}))
    expect(m.sides.bottom).toMatchObject({ visible: true, direction: 'out', inward: false, outward: true })
    expect(m.sides.top).toMatchObject({ visible: false, inward: false, outward: false })
    expect(m.sides.left).toMatchObject({ axis: 'y', outward: true })
  })
  it('override 优先于 manifest 当前值', () => {
    const m = model(manifestWith({}), [
      { gid: 'axes_0.xticks', prop: 'direction', value: 'in' },
      { gid: 'axes_0', prop: 'ticks_top', value: true },
    ])
    expect(m.sides.bottom).toMatchObject({ inward: true, outward: false })
    expect(m.sides.top).toMatchObject({ visible: true, inward: true, outward: false })
    expect(m.sides.left).toMatchObject({ direction: 'out' }) // Y 没动
  })
  it('引擎没发那条轴的刻度元素：那两条边不进模型（方向未知，不摆假开关）', () => {
    const m = model(manifestWith({ noYTicks: true }))
    expect(m.sides.left).toBeUndefined()
    expect(m.sides.right).toBeUndefined()
    expect(m.sides.bottom).toBeDefined()
    expect(m.tickGid.y).toBeUndefined()
  })
  it('不是子图 / 没有四边字段 → null', () => {
    expect(readAxesTickModel(manifestWith({}), [], 'axes_0.xticks')).toBeNull()
    expect(readAxesTickModel(null, [], 'axes_0')).toBeNull()
  })
})

describe('一次点击的计划：in / out / inout / hidden 映射', () => {
  it('朝外的下边：点框里 = 加向内 → inout；再点框外 = 去掉向外 → in；再点框里 = 隐藏这一边', () => {
    let m = model(manifestWith({}))
    const p1 = toggleSidePlan(m, 'bottom', 'inner')!
    expect(p1.set).toEqual([{ gid: 'axes_0.xticks', prop: 'direction', value: 'inout' }])
    expect(p1.effect).toMatchObject({ dir: 'in', on: true, hides: false, shows: false, coupled: [] })

    m = model(manifestWith({ xdir: 'inout' }))
    const p2 = toggleSidePlan(m, 'bottom', 'outer')!
    expect(p2.set).toEqual([{ gid: 'axes_0.xticks', prop: 'direction', value: 'in' }])

    m = model(manifestWith({ xdir: 'in' }))
    const p3 = toggleSidePlan(m, 'bottom', 'inner')!
    expect(p3.set).toEqual([{ gid: 'axes_0', prop: 'ticks_bottom', value: false }])
    expect(p3.effect).toMatchObject({ on: false, hides: true, direction: undefined })
  })

  it('隐藏的上边：点框外 = 打开这一边（轴已朝外，方向不动）', () => {
    const p = toggleSidePlan(model(manifestWith({})), 'top', 'outer')!
    expect(p.set).toEqual([{ gid: 'axes_0', prop: 'ticks_top', value: true }])
    expect(p.effect).toMatchObject({ shows: true, on: true, coupled: [] })
  })

  it('隐藏的上边：点框里 = 打开 + 方向加向内（inout）——下边可见，连带说出来', () => {
    const p = toggleSidePlan(model(manifestWith({})), 'top', 'inner')!
    expect(p.set).toEqual([
      { gid: 'axes_0', prop: 'ticks_top', value: true },
      { gid: 'axes_0.xticks', prop: 'direction', value: 'inout' },
    ])
    expect(p.effect.coupled).toEqual(['bottom'])
  })

  it('同轴另一边不可见时不算连带', () => {
    const p = toggleSidePlan(model(manifestWith({ sides: { bottom: false } })), 'top', 'inner')!
    expect(p.effect.coupled).toEqual([])
  })

  it('不在模型里的边 → null', () => {
    expect(toggleSidePlan(model(manifestWith({ noYTicks: true })), 'left', 'inner')).toBeNull()
  })

  it('每一边此刻的形态与计划自洽：任何状态点任一带，再按结果重算都是「那一方向翻转」', () => {
    for (const xdir of ['in', 'out', 'inout'] as const) {
      for (const bottom of [true, false]) {
        for (const top of [true, false]) {
          for (const side of ['bottom', 'top'] as const) {
            for (const zone of ['inner', 'outer'] as const) {
              const m0 = model(manifestWith({ xdir, sides: { bottom, top } }))
              const before = m0.sides[side]!
              const plan = toggleSidePlan(m0, side, zone)!
              const m1 = model(manifestWith({ xdir, sides: { bottom, top } }), plan.set)
              const after = m1.sides[side]!
              const key = zone === 'inner' ? 'inward' : 'outward'
              const other = zone === 'inner' ? 'outward' : 'inward'
              expect(after[key], `${xdir}/${bottom}/${top}/${side}/${zone}`).toBe(!before[key])
              // 另一方向：关掉这一边会把它一起带走；打开这一边会把轴上已有的
              // 那个方向一起露出来（方向是轴的，边只是显不显示）；其余不动
              if (plan.effect.hides) expect(after[other]).toBe(false)
              else if (plan.effect.shows) {
                const otherDir = zone === 'inner' ? 'out' : 'in'
                expect(after[other]).toBe(xdir === 'inout' || xdir === otherDir)
              } else expect(after[other]).toBe(before[other])
            }
          }
        }
      }
    }
  })
})

describe('属性页的四档与显示边', () => {
  it('两边都不显示 = 「隐藏」（派生态）', () => {
    expect(axisChoice(model(manifestWith({})), 'x')).toBe('out')
    expect(axisChoice(model(manifestWith({ sides: { bottom: false } })), 'x')).toBe('hidden')
    expect(axisChoice(model(manifestWith({ noYTicks: true })), 'y')).toBeNull()
  })
  it('选「隐藏」：两边都写 false，方向不动', () => {
    const p = axisChoicePlan(model(manifestWith({ sides: { top: true } })), 'x', 'hidden')!
    expect(p.set).toEqual([
      { gid: 'axes_0', prop: 'ticks_bottom', value: false },
      { gid: 'axes_0', prop: 'ticks_top', value: false },
    ])
    expect(p.remove).toEqual([])
  })
  it('从「隐藏」选回方向：写方向 + 删两边的 override（回到脚本的边）', () => {
    const p = axisChoicePlan(model(manifestWith({ sides: { bottom: false } })), 'x', 'in')!
    expect(p.set).toEqual([{ gid: 'axes_0.xticks', prop: 'direction', value: 'in' }])
    expect(p.remove).toEqual([
      { gid: 'axes_0', prop: 'ticks_bottom' },
      { gid: 'axes_0', prop: 'ticks_top' },
    ])
  })
  it('有边可见时选方向：只写方向；选当前方向 → null（不产生空历史）', () => {
    const p = axisChoicePlan(model(manifestWith({})), 'x', 'inout')!
    expect(p.set).toEqual([{ gid: 'axes_0.xticks', prop: 'direction', value: 'inout' }])
    expect(p.remove).toEqual([])
    expect(axisChoicePlan(model(manifestWith({})), 'x', 'out')).toBeNull()
  })
  it('显示边开关只写 ticks_<side>；已是目标值 → null', () => {
    expect(sideVisiblePlan(model(manifestWith({})), 'top', true)!.set).toEqual([
      { gid: 'axes_0', prop: 'ticks_top', value: true },
    ])
    expect(sideVisiblePlan(model(manifestWith({})), 'top', false)).toBeNull()
  })
})

describe('整张图里挑边：优先级与孪生轴', () => {
  it('pickElement 命中了别的元素时让路；命中 figure 或这条边的子图时才算', () => {
    const m = manifestWith({})
    const at = (allow: (gid: string) => boolean) =>
      pickSpineZone(m, 0.5, 0.9 + px(5, 'y'), SCALE, ZONE_PX, allow)
    expect(at(() => true)?.hit.side).toBe('bottom')
    expect(at(() => false)).toBeNull()
  })
  it('twinx：两条重合的右边，取此刻画着刻度的那一条', () => {
    const twin = {
      gid: 'axes_1',
      role: 'axes',
      label: '孪生',
      bbox: [0.1, 0.1, 0.8, 0.8],
      draggable: false,
      editable: [f('ticks_left', false), f('ticks_right', true)],
      spines: {
        left: { visible: true, ticks: false, from: [0.1, 0.9], to: [0.1, 0.1] },
        right: { visible: true, ticks: true, from: [0.9, 0.9], to: [0.9, 0.1] },
      },
    } as unknown as ManifestElement
    const m = manifestWith({ extra: [twin] })
    const right = pickSpineZone(m, 0.9 + px(5, 'x'), 0.5, SCALE, ZONE_PX, () => true)!
    expect(right.gid).toBe('axes_1')
    const left = pickSpineZone(m, 0.1 - px(5, 'x'), 0.5, SCALE, ZONE_PX, () => true)!
    expect(left.gid).toBe('axes_0')
  })
})
