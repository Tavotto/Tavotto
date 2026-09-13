import { describe, expect, it } from 'vitest'
import { emptyStateAnchor } from './emptyStateAnchor'

const VIEW = { viewW: 1000, viewH: 700 }

describe('空画布起步提示的落点', () => {
  it('纸面整个在视野里：就是纸面中心', () => {
    const p = emptyStateAnchor({ ...VIEW, paper: { x: 200, y: 100, w: 600, h: 400 } })
    expect(p).toEqual({ x: 500, y: 300 })
  })

  it('纸面被挤出右缘（审计 B01 的 194%）：落在可见那一半的中心，不在视野外', () => {
    // 纸面 1100 宽，从 x=300 起——右边 400px 在视口外
    const p = emptyStateAnchor({ ...VIEW, paper: { x: 300, y: -50, w: 1100, h: 800 } })
    expect(p.x).toBe((300 + 1000) / 2)
    expect(p.y).toBe((0 + 700) / 2)
    expect(p.x + 150).toBeLessThanOrEqual(VIEW.viewW)
  })

  it('纸面只露出一条窄边：钳进视口，整盒可见', () => {
    const p = emptyStateAnchor({ ...VIEW, paper: { x: 960, y: 0, w: 800, h: 700 } })
    // 可见条带中心在 980，钳到 1000 - 150
    expect(p.x).toBe(850)
    expect(p.y).toBe(350)
  })

  it('纸面完全不在视野里：退到视口中心', () => {
    const p = emptyStateAnchor({ ...VIEW, paper: { x: 2000, y: 2000, w: 100, h: 100 } })
    expect(p).toEqual({ x: 500, y: 350 })
  })

  it('视口比盒子还小：直接居中，不钳出负数', () => {
    const p = emptyStateAnchor({ viewW: 200, viewH: 120, paper: { x: 0, y: 0, w: 900, h: 900 } })
    expect(p).toEqual({ x: 100, y: 60 })
  })
})
