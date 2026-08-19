/**
 * 图内元素真实路径上的几何运算（命中 / 框选 / 描边 / 乐观位移）。
 *
 * 这里的每一条都对着一个具体的误操作：斜曲线的 bbox 空白角不该命中、
 * U 形曲线中间那块空白不该被框选圈中、带孔洞的填充洞里不算内部、
 * 裁剪框外的墨迹图上根本不存在所以也不该命中。
 */
import { describe, expect, it } from 'vitest'

import type { ElementGeometry } from './api'
import {
  geomAreaFrac,
  geomContains,
  geomDistMm,
  geomHitsRect,
  geomInkAreaFrac,
  geomPathD,
  translateGeom,
} from './pathGeom'

/** 100mm × 100mm 的图：分数 0.01 = 1mm，距离换算一眼看得出来 */
const SIZE: [number, number] = [100, 100]

/** 从 (0.1,0.1) 到 (0.9,0.9) 的对角线：bbox 是一大块 0.8×0.8 的方 */
const diagonal: ElementGeometry = {
  kind: 'polyline',
  paths: [{ points: [[0.1, 0.1], [0.9, 0.9]], closed: false }],
  fill: false,
  stroke: true,
}

/** 闭合三角形（填充） */
const triangle: ElementGeometry = {
  kind: 'path',
  paths: [{ points: [[0.2, 0.2], [0.8, 0.2], [0.5, 0.8]], closed: true }],
  fill: true,
  stroke: true,
}

/** 外环 + 内环（even-odd → 中间是洞） */
/** 内外环**同向**。matplotlib 用 nonzero，中间那块是**实心**的（下方有实测账） */
const nestedSameDir: ElementGeometry = {
  kind: 'multi_path',
  paths: [
    { points: [[0, 0], [1, 0], [1, 1], [0, 1]], closed: true },
    { points: [[0.4, 0.4], [0.6, 0.4], [0.6, 0.6], [0.4, 0.6]], closed: true },
  ],
  fill: true,
  stroke: false,
}

/** 内环**反向**：nonzero 下这才是洞 */
const ringWithHole: ElementGeometry = {
  ...nestedSameDir,
  paths: [
    { points: [[0, 0], [1, 0], [1, 1], [0, 1]], closed: true },
    { points: [[0.4, 0.6], [0.6, 0.6], [0.6, 0.4], [0.4, 0.4]], closed: true },
  ],
}

describe('geomDistMm：点到路径的距离按 mm 算', () => {
  it('线上的点距离为 0', () => {
    expect(geomDistMm(diagonal, SIZE, 0.5, 0.5)).toBeCloseTo(0, 6)
  })

  it('bbox 空白角离线很远——这正是「不要用 bbox 命中」的那个数', () => {
    // (0.9,0.1) 是对角线 bbox 的右上角，到线的垂距 = 0.8/√2 × 100mm ≈ 56.6mm
    expect(geomDistMm(diagonal, SIZE, 0.9, 0.1)).toBeGreaterThan(50)
  })

  it('x/y 分别乘图宽图高：扁图上横向与纵向容差不能差好几倍', () => {
    const flat: [number, number] = [200, 20]
    const horiz: ElementGeometry = {
      kind: 'polyline',
      paths: [{ points: [[0, 0.5], [1, 0.5]], closed: false }],
      fill: false,
      stroke: true,
    }
    // 分数系里 0.05 的纵向偏移，在 20mm 高的图上就是 1mm
    expect(geomDistMm(horiz, flat, 0.5, 0.55)).toBeCloseTo(1, 6)
  })

  it('裁剪框外一律不命中（那儿的墨迹被 matplotlib 裁掉了）', () => {
    const clipped: ElementGeometry = { ...diagonal, clip: [0.1, 0.1, 0.3, 0.3] }
    expect(geomDistMm(clipped, SIZE, 0.2, 0.2)).toBeCloseTo(0, 6)
    expect(geomDistMm(clipped, SIZE, 0.8, 0.8)).toBe(Infinity)
  })
})

describe('geomContains：填充内部算命中', () => {
  it('三角形内部命中、外部不命中', () => {
    expect(geomContains(triangle, 0.5, 0.4)).toBe(true)
    expect(geomContains(triangle, 0.25, 0.7)).toBe(false)
  })

  it('空心路径没有内部（只有描边附近才命中）', () => {
    expect(geomContains({ ...triangle, fill: false }, 0.5, 0.4)).toBe(false)
  })

  /**
   * 判据跟渲染器走，不跟直觉走。实测（matplotlib 3.10.8，Agg，
   * `PathPatch(facecolor="black")` 后读像素）：
   *   同向嵌套 → 中心像素 (0,0,0)   ← 实心
   *   反向嵌套 → 中心像素 (255,255,255) ← 洞
   * 也就是 SVG/PDF/Agg 一致的 **nonzero**。旧实现按 even-odd 逐次翻转，
   * 于是第一种情况下点在明明填了色的像素上却选不中。
   */
  it('nonzero：同向嵌套，中间那块是实心的', () => {
    expect(geomContains(nestedSameDir, 0.2, 0.2)).toBe(true)
    expect(geomContains(nestedSameDir, 0.5, 0.5)).toBe(true)
  })

  it('nonzero：反向嵌套才是洞', () => {
    expect(geomContains(ringWithHole, 0.2, 0.2)).toBe(true)
    expect(geomContains(ringWithHole, 0.5, 0.5)).toBe(false)
  })

  /**
   * 实测同一批：`Path` 只给 MOVETO/LINETO、没有 CLOSEPOLY，`PathPatch` 照样
   * 把它隐式闭合并填出来（中心像素 (0,0,0)）。`closed` 是 false，所以按
   * `closed` 过滤会把整块可见的填充区判成不存在。
   */
  it('填充路径没有 CLOSEPOLY 时也要算内部（matplotlib 隐式闭合）', () => {
    const openCoded: ElementGeometry = {
      kind: 'path',
      paths: [{ points: [[0.1, 0.1], [0.9, 0.1], [0.5, 0.9]], closed: false }],
      fill: true,
      stroke: false,
    }
    expect(geomContains(openCoded, 0.5, 0.3)).toBe(true)
    expect(geomContains(openCoded, 0.05, 0.05)).toBe(false)
  })
})

describe('geomHitsRect：框选按路径相交', () => {
  it('框穿过线算圈中', () => {
    expect(geomHitsRect(diagonal, { x: 0.45, y: 0.45, w: 0.1, h: 0.1 })).toBe(true)
  })

  it('框只碰到 bbox 空白角、没碰到线，不算圈中', () => {
    expect(geomHitsRect(diagonal, { x: 0.75, y: 0.15, w: 0.1, h: 0.1 })).toBe(false)
  })

  it('整条路径落进框里算圈中（端点在框内）', () => {
    expect(geomHitsRect(diagonal, { x: 0, y: 0, w: 1, h: 1 })).toBe(true)
  })

  it('框整个落在一大块填充内部**不**算圈中（框选是圈墨迹，不是戳进去）', () => {
    expect(geomHitsRect(nestedSameDir, { x: 0.45, y: 0.45, w: 0.02, h: 0.02 })).toBe(false)
  })

  it('裁剪框之外的框选不圈中', () => {
    const clipped: ElementGeometry = { ...diagonal, clip: [0, 0, 0.3, 0.3] }
    expect(geomHitsRect(clipped, { x: 0.8, y: 0.8, w: 0.1, h: 0.1 })).toBe(false)
  })
})

describe('命中评分：与 bbox 面积同一量纲', () => {
  it('闭合路径按真实面积（三角形是外接矩形的一半）', () => {
    // 底 0.6 高 0.6 的三角形，面积 = 0.18；它的 bbox 是 0.36
    expect(geomAreaFrac(triangle)).toBeCloseTo(0.18, 6)
  })

  it('一条线的墨迹面积远小于它的 bbox —— 所以曲线总能从子图手里拿回点击', () => {
    const ink = geomInkAreaFrac(diagonal, SIZE, 1.5)
    expect(ink).toBeLessThan(0.05)
    expect(ink).toBeGreaterThan(0)
  })
})

describe('translateGeom：拖动中的乐观位移', () => {
  it('每个点与裁剪框一起平移同一个量', () => {
    const moved = translateGeom({ ...diagonal, clip: [0, 0, 1, 1] }, 0.05, -0.02)
    expect(moved.paths[0].points[0][0]).toBeCloseTo(0.15, 9)
    expect(moved.paths[0].points[0][1]).toBeCloseTo(0.08, 9)
    expect(moved.clip).toEqual([0.05, -0.02, 1, 1])
  })

  it('零位移返回原对象（渲染路径上避免无谓的重建）', () => {
    expect(translateGeom(diagonal, 0, 0)).toBe(diagonal)
  })
})

describe('geomPathD：SVG 路径串', () => {
  const toPoint = (p: [number, number]) => ({ x: p[0] * 200, y: p[1] * 100 })

  it('折线用 M/L，闭合路径带 Z', () => {
    expect(geomPathD(diagonal, toPoint)).toBe('M20.00,10.00 L180.00,90.00')
    expect(geomPathD(triangle, toPoint)).toContain('Z')
  })

  it('多条子路径各自一个 M（断开的曲线不会被连起来）', () => {
    const d = geomPathD(nestedSameDir, toPoint)
    expect(d.match(/M/g)?.length).toBe(2)
  })
})
