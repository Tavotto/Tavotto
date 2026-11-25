/**
 * 与参考实现 colorspacious 对拍 `tests/golden/palette_cvd_vectors.json`
 * （`scripts/dev/gen_palette_cvd_vectors.py` 生成）：每个颜色在四种视觉下的 CAM02-UCS 坐标，
 * 加几组配色的最小色差（ADR 0210 §五 写的那几个数就是这里核的）。
 */
import { describe, expect, it } from 'vitest'
import vectors from '../../../../tests/golden/palette_cvd_vectors.json'
import { VISIONS, closestPair, contrastRatio, deltaE, hexToRgb01, toUcs } from './cvd'

describe('CAM02-UCS 与色觉模拟对拍 colorspacious', () => {
  it.each(vectors.colors.map((c) => [c.hex, c] as const))('%s', (hex, c) => {
    const rgb = hexToRgb01(hex)!
    for (const v of VISIONS) {
      const got = toUcs(rgb, v)
      const want = c[v]
      for (let k = 0; k < 3; k++) expect(got[k]).toBeCloseTo(want[k], 6)
    }
  })

  it.each(vectors.sets.map((s) => [s.name, s] as const))('最小色差：%s', (_name, s) => {
    for (const v of VISIONS) {
      let min = Infinity
      for (let i = 0; i < s.colors.length; i++)
        for (let j = i + 1; j < s.colors.length; j++) min = Math.min(min, deltaE(s.colors[i], s.colors[j], v))
      expect(min).toBeCloseTo(s.min_delta_e[v as keyof typeof s.min_delta_e], 6)
    }
  })

  it('closestPair 给出四种视觉里最近的那一对', () => {
    const tab10 = ['#1F77B4', '#FF7F0E', '#2CA02C', '#D62728']
    const p = closestPair(tab10)!
    expect([p.i, p.j, p.vision]).toEqual([1, 2, 'protanomaly'])
    expect(p.deltaE).toBeCloseTo(1.6, 1)
    expect(closestPair(['#000000'])).toBeNull()
  })

  it('认不出的色值不算撞色，对比度按 WCAG', () => {
    expect(deltaE('red', '#000000')).toBe(Number.POSITIVE_INFINITY)
    expect(contrastRatio('#000000')).toBeCloseTo(21, 6)
    expect(contrastRatio('#FFFFFF')).toBeCloseTo(1, 6)
    expect(contrastRatio('#767676')).toBeGreaterThan(4.5)
  })
})
