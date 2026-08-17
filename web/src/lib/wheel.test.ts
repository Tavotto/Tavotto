import { afterEach, describe, expect, it, vi } from 'vitest'
import { WHEEL_LINE_PX, WHEEL_PAGE_FALLBACK_PX, normalizeWheel } from './wheel'

const wheel = (deltaMode: number, deltaX: number, deltaY: number) => ({
  deltaMode,
  deltaX,
  deltaY,
})

describe('滚轮 deltaMode 归一化', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('deltaMode=0（像素）恒等返回：Chrome/Safari 手感逐位不变', () => {
    // 逐位相等，不是「约等于」——像素模式一旦被乘除，现有缩放步长就变了
    for (const [dx, dy] of [
      [0, 100],
      [0, -120],
      [-53, 7],
      [0, -0.30000000000000004], // 触控板惯性尾巴那种碎小值
      [0, 0],
    ]) {
      const out = normalizeWheel(wheel(0, dx, dy))
      expect(out.deltaX).toBe(dx)
      expect(out.deltaY).toBe(dy)
    }
  })

  it('deltaMode=1（行）放大 WHEEL_LINE_PX 倍', () => {
    expect(normalizeWheel(wheel(1, 0, 3))).toEqual({ deltaX: 0, deltaY: 3 * WHEEL_LINE_PX })
    expect(normalizeWheel(wheel(1, -2, 1))).toEqual({
      deltaX: -2 * WHEEL_LINE_PX,
      deltaY: WHEEL_LINE_PX,
    })
  })

  it('deltaMode=2（页）按视口尺寸折算', () => {
    expect(normalizeWheel(wheel(2, 1, -2), { w: 900, h: 640 })).toEqual({
      deltaX: 900,
      deltaY: -1280,
    })
  })

  it('deltaMode=2 视口尺寸缺失时退到窗口尺寸，再退到兜底常量', () => {
    // 视口还没上报（viewW/viewH=0）时退到窗口尺寸
    expect(normalizeWheel(wheel(2, 0, 1), { w: 0, h: 0 })).toEqual({
      deltaX: 0,
      deltaY: window.innerHeight,
    })
    vi.spyOn(window, 'innerHeight', 'get').mockReturnValue(0)
    expect(normalizeWheel(wheel(2, 0, 1))).toEqual({
      deltaX: 0,
      deltaY: WHEEL_PAGE_FALLBACK_PX,
    })
  })

  it('未知 deltaMode 按像素兜底，不放大', () => {
    expect(normalizeWheel(wheel(3, 0, 100))).toEqual({ deltaX: 0, deltaY: 100 })
  })

  it('归一化后 Chrome 一格与 Firefox 一格落在同一量级', () => {
    // Chrome 像素模式一格 100–120px；Firefox 行模式一格 3 行
    const chrome = normalizeWheel(wheel(0, 0, 100)).deltaY
    const firefox = normalizeWheel(wheel(1, 0, 3)).deltaY
    expect(firefox / chrome).toBeGreaterThan(0.5)
    expect(firefox / chrome).toBeLessThan(2)
  })
})
