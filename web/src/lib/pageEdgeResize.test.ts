import { describe, expect, it } from 'vitest'
import { pageColumns } from './pageColumns'
import { PAGE_EDGE_MIN_MM, resizePageEdge } from './pageEdgeResize'

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
