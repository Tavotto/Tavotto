import { describe, expect, it } from 'vitest'
import { isLocationFailure, largestDpiWithinLimit } from './exportRecovery'

const C = [300, 600, 900, 1200]

describe('largestDpiWithinLimit：不超上限的最大一档', () => {
  it('600 dpi 时 20000×20000 超 1 亿像素：300 dpi 刚好够（像素数随 dpi 平方缩）', () => {
    expect(largestDpiWithinLimit({ width: 20000, height: 20000, max_pixels: 100_000_000 }, 600, C)).toBe(300)
  })
  it('1200 dpi 时取比当前小的最大合法档，不取当前或更高档', () => {
    // 1200 → 900：宽高各 ×0.75，仍 ≤ 上限
    expect(largestDpiWithinLimit({ width: 12000, height: 8000, max_pixels: 70_000_000 }, 1200, C)).toBe(900)
  })
  it('最低一档也放不下：null（调用方退回「发送问题反馈」）', () => {
    expect(largestDpiWithinLimit({ width: 60000, height: 60000, max_pixels: 100_000_000 }, 600, C)).toBeNull()
  })
  it('已经是最低一档：没有更低的档，null', () => {
    expect(largestDpiWithinLimit({ width: 20000, height: 20000, max_pixels: 1 }, 300, C)).toBeNull()
  })
  it('参数缺 / 不是正整数 / 当前 dpi 不明：null，不捏造', () => {
    expect(largestDpiWithinLimit({ width: 20000, max_pixels: 1e8 }, 600, C)).toBeNull()
    expect(largestDpiWithinLimit({ width: 0, height: 5, max_pixels: 1e8 }, 600, C)).toBeNull()
    expect(largestDpiWithinLimit({ width: 9, height: 9, max_pixels: 1e8 }, NaN, C)).toBeNull()
    expect(largestDpiWithinLimit(undefined, 600, C)).toBeNull()
  })
  it('宽高向上取整：临界处宁可降一档也不让再失败一次', () => {
    // 300→ 宽高刚好够；给上限少 1 个像素就必须不选 300
    expect(largestDpiWithinLimit({ width: 20000, height: 20000, max_pixels: 100_000_000 }, 600, C)).toBe(300)
    expect(largestDpiWithinLimit({ width: 20000, height: 20000, max_pixels: 99_999_999 }, 600, C)).toBeNull()
  })
})

describe('isLocationFailure', () => {
  it('只有目录不可写 / 临时目录建不了两种', () => {
    expect(isLocationFailure('export_dir_unwritable')).toBe(true)
    expect(isLocationFailure('tmp_dir_failed')).toBe(true)
    expect(isLocationFailure('format_failed')).toBe(false)
    expect(isLocationFailure(undefined)).toBe(false)
  })
})
