import { describe, expect, it } from 'vitest'
import { formatAxisLimit, pow10Label } from './logPower'

describe('pow10Label', () => {
  it.each([
    [0.01, '10⁻²'],
    [0.1, '10⁻¹'],
    [1, '10⁰'],
    [10, '10¹'],
    [1000, '10³'],
    [1e-12, '10⁻¹²'],
    [1e6, '10⁶'],
  ])('%s → %s', (v, want) => expect(pow10Label(v)).toBe(want))

  it('不是 10 的整数次幂、非正数、非数字都不改写', () => {
    for (const v of [0.05, 2, 0.011, 1.05, 0, -0.01, NaN, Infinity, '0.01', null, undefined, 1e-300]) expect(pow10Label(v)).toBeNull()
  })

  it('浮点误差内的 10 的幂也认（0.1 + 0.9 之类算出来的）', () => {
    expect(pow10Label(0.1 * 10 * 0.01)).toBe('10⁻²')
  })
})

describe('formatAxisLimit', () => {
  it('对数轴：10 的幂用上标，别的数照旧', () => {
    expect(formatAxisLimit(0.01, true)).toBe('10⁻²')
    expect(formatAxisLimit(0.05, true)).toBe('0.05')
  })
  it('线性轴照旧显示小数', () => {
    expect(formatAxisLimit(0.01, false)).toBe('0.01')
    expect(formatAxisLimit(1, false)).toBe('1')
  })
})
