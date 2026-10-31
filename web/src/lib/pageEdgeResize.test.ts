import { describe, expect, it } from 'vitest'
import { pageColumns } from './pageColumns'
import { FIGURE_EDGE_MIN_MM, PAGE_EDGE_MIN_MM, planFigureFrame, resizeFigureEdge, resizePageEdge } from './pageEdgeResize'

const columns = pageColumns({ single: 89, double: 183 })
const start = { w: 180, h: 120 }

describe('resizePageEdge', () => {
  it('右边往外拖变宽，下边往下拖变高，原点不动', () => {
    expect(resizePageEdge({ edge: 'r', start, delta: 20, columns: [] })).toMatchObject({ w: 200, h: 120, shiftX: 0, shiftY: 0 })
    expect(resizePageEdge({ edge: 'b', start, delta: 15, columns: [] })).toMatchObject({ w: 180, h: 135, shiftX: 0, shiftY: 0 })
  })

  it('左 / 上边往外拖变大，原点平移同样的量（对象页面坐标要反向补偿）', () => {
    expect(resizePageEdge({ edge: 'l', start, delta: -25, columns: [] })).toMatchObject({ w: 205, shiftX: 25, shiftY: 0 })
    expect(resizePageEdge({ edge: 't', start, delta: -10, columns: [] })).toMatchObject({ h: 130, shiftX: 0, shiftY: 10 })
    // 往里拖变小：位移为负
    expect(resizePageEdge({ edge: 'l', start, delta: 30, columns: [] })).toMatchObject({ w: 150, shiftX: -30 })
  })

  it('宽度离栏宽 ±2 mm 吸过去并报出是哪一档；超出范围不吸', () => {
    const near = resizePageEdge({ edge: 'r', start, delta: 4, columns }) // 184 → 183
    expect(near.w).toBe(183)
    expect(near.snap?.id).toBe('double')
    expect(resizePageEdge({ edge: 'r', start, delta: 5.5, columns }).snap).toBeNull() // 185.5 离 183 超过 2
    expect(resizePageEdge({ edge: 'r', start, delta: -90, columns }).w).toBe(89) // 90 离单栏 1 mm
  })

  it('拖左边吸附时位移按吸附后的宽度算（图不动）', () => {
    const r = resizePageEdge({ edge: 'l', start, delta: -2.5, columns }) // 182.5 → 183
    expect(r).toMatchObject({ w: 183, shiftX: 3 })
  })

  it('按住 ⌥（noSnap）不吸', () => {
    const r = resizePageEdge({ edge: 'r', start, delta: 4, columns, noSnap: true })
    expect(r).toMatchObject({ w: 184, snap: null })
  })

  it('高度没有规范数，不吸', () => {
    expect(resizePageEdge({ edge: 'b', start: { w: 100, h: 88 }, delta: 1, columns }).snap).toBeNull()
  })

  it(`最小 ${PAGE_EDGE_MIN_MM} mm，最大 1000 mm`, () => {
    expect(resizePageEdge({ edge: 'r', start, delta: -500, columns }).w).toBe(PAGE_EDGE_MIN_MM)
    expect(resizePageEdge({ edge: 'b', start, delta: -500, columns }).h).toBe(PAGE_EDGE_MIN_MM)
    // 拖左边缩到最小：原点平移的是夹完之后的量
    expect(resizePageEdge({ edge: 'l', start, delta: 500, columns })).toMatchObject({ w: PAGE_EDGE_MIN_MM, shiftX: PAGE_EDGE_MIN_MM - 180 })
    expect(resizePageEdge({ edge: 'r', start, delta: 5000, columns }).w).toBe(1000)
  })

  it('读数落在 0.1 mm 一格上', () => {
    expect(resizePageEdge({ edge: 'b', start, delta: 3.14159, columns }).h).toBe(123.1)
  })
})

describe('resizeFigureEdge（单图编辑拖图的边）', () => {
  const fig = { x: 20, y: 30, w: 150, h: 102.3 }

  it('右 / 下边：x / y 不动，宽高按位移变（0.1 mm 一格）', () => {
    expect(resizeFigureEdge({ edge: 'r', start: fig, delta: 12.34, columns: [] })).toMatchObject({ x: 20, y: 30, w: 162.3, h: 102.3 })
    expect(resizeFigureEdge({ edge: 'b', start: fig, delta: -2.3, columns: [] })).toMatchObject({ x: 20, y: 30, w: 150, h: 100 })
  })

  it('左 / 上边：对侧边不动（x / y 反向补偿）', () => {
    const l = resizeFigureEdge({ edge: 'l', start: fig, delta: -10, columns: [] })
    expect(l).toMatchObject({ w: 160, h: 102.3, y: 30 })
    expect(l.x + l.w).toBeCloseTo(fig.x + fig.w, 6)
    const t = resizeFigureEdge({ edge: 't', start: fig, delta: 5, columns: [] })
    expect(t.h).toBeCloseTo(97.3, 6)
    expect(t.y + t.h).toBeCloseTo(fig.y + fig.h, 6)
    expect(t.x).toBe(20)
  })

  it('宽度吸栏宽、⌥ 关；左边吸附后对侧边仍不动；高度不吸', () => {
    const r = resizeFigureEdge({ edge: 'r', start: { ...fig, w: 180 }, delta: 4, columns })
    expect(r.w).toBe(183)
    expect(r.snap?.id).toBe('double')
    expect(resizeFigureEdge({ edge: 'r', start: { ...fig, w: 180 }, delta: 4, columns, noSnap: true })).toMatchObject({ w: 184, snap: null })
    const l = resizeFigureEdge({ edge: 'l', start: { ...fig, w: 180 }, delta: -4, columns })
    expect(l.w).toBe(183)
    expect(l.x + l.w).toBeCloseTo(fig.x + 180, 6)
    expect(resizeFigureEdge({ edge: 'b', start: { ...fig, h: 180 }, delta: 4, columns }).snap).toBeNull()
  })

  it('最小 10 mm；比 10 mm 还小的起点不会在第一帧被抬高', () => {
    expect(resizeFigureEdge({ edge: 'r', start: fig, delta: -500, columns: [] }).w).toBe(FIGURE_EDGE_MIN_MM)
    expect(resizeFigureEdge({ edge: 'r', start: { ...fig, w: 8 }, delta: 0, columns: [] }).w).toBe(8)
    const small = { x: 0, y: 0, w: 20, h: 20 }
    expect(resizeFigureEdge({ edge: 'r', start: small, delta: 0, columns: [] }).w).toBe(20)
    expect(resizeFigureEdge({ edge: 'r', start: small, delta: -5, columns: [] }).w).toBe(15)
  })
})

describe('planFigureFrame（拖边 → 图幅）', () => {
  const start = { x: 7, y: 18, w: 80, h: 55 }
  it('缩放比 1：图幅 = 页面框；右边不挪 x，左边按对侧不动挪 x', () => {
    expect(planFigureFrame({ edge: 'r', start, sizeMm: [80, 55], delta: 12, columns: [] })).toMatchObject({
      sizeMm: [92, 55],
      pos: { x: 7, y: 18 },
      box: { x: 7, y: 18, w: 92, h: 55 },
    })
    const l = planFigureFrame({ edge: 'l', start, sizeMm: [80, 55], delta: -15, columns: [] })
    expect(l.sizeMm).toEqual([95, 55])
    expect(l.pos.x + l.box.w).toBeCloseTo(87, 6)
  })
  it('缩放比 2：位移按页面 mm，图幅是页面的一半；预览框用取整后的图幅反算', () => {
    const p = planFigureFrame({ edge: 'r', start: { x: 0, y: 0, w: 160, h: 110 }, sizeMm: [80, 55], delta: 20.3, columns: [] })
    expect(p.sizeMm).toEqual([90.15, 55])
    expect(p.box.w).toBeCloseTo(180.3, 6)
  })
  it('图幅夹在引擎的界里；高度不吸栏宽', () => {
    expect(planFigureFrame({ edge: 'r', start, sizeMm: [80, 55], delta: 999, columns: [], limits: { max: 120 } }).sizeMm[0]).toBe(120)
    expect(planFigureFrame({ edge: 'b', start: { ...start, h: 180 }, sizeMm: [80, 180], delta: 4, columns }).snap).toBeNull()
  })
})
