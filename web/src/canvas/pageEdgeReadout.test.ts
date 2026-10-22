import { describe, expect, it } from 'vitest'
import { READOUT_H, READOUT_INSET, READOUT_W, readoutBox } from './pageEdgeReadout'

const view = { w: 1000, h: 700 }
const page = { left: 200, top: 100, w: 400, h: 300 }

describe('readoutBox：读数锚在被拖那条边的画布内侧', () => {
  it('左 / 右边贴边内侧、垂直居中；上 / 下边贴边内侧、水平居中', () => {
    expect(readoutBox('l', page, view)).toEqual({ left: 208, top: 240, align: 'left' })
    expect(readoutBox('r', page, view)).toEqual({ left: 600 - 8 - READOUT_W, top: 240, align: 'right' })
    expect(readoutBox('t', page, view)).toEqual({ left: 400 - READOUT_W / 2, top: 108, align: 'center' })
    expect(readoutBox('b', page, view)).toEqual({ left: 400 - READOUT_W / 2, top: 400 - 8 - READOUT_H, align: 'center' })
  })

  it('位置与文字无关：盒子固定宽高，只由页面矩形决定（逐帧数值位数变也不抖）', () => {
    const a = readoutBox('r', { ...page, w: 399.9 }, view)
    const b = readoutBox('r', { ...page, w: 400 }, view)
    expect(Math.abs(a.left - b.left)).toBeLessThan(0.2)
    expect(a.top).toBe(b.top)
  })

  it('页面比舞台大 / 被拖出舞台时夹在舞台内（舞台之外就是标尺）', () => {
    for (const edge of ['l', 'r', 't', 'b'] as const) {
      for (const p of [
        { left: -500, top: -400, w: 3000, h: 2500 },
        { left: 900, top: 650, w: 50, h: 40 },
      ]) {
        const r = readoutBox(edge, p, view)
        expect(r.left).toBeGreaterThanOrEqual(READOUT_INSET)
        expect(r.top).toBeGreaterThanOrEqual(READOUT_INSET)
        expect(r.left + READOUT_W).toBeLessThanOrEqual(view.w - READOUT_INSET)
        expect(r.top + READOUT_H).toBeLessThanOrEqual(view.h - READOUT_INSET)
      }
    }
  })
})
