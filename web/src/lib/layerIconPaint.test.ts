import { describe, expect, it } from 'vitest'
import { contrastOf, ICON_NEUTRAL, layerIconPaint } from './layerIconPaint'

const sat = (hex: string) => {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16))
  return Math.max(r, g, b) - Math.min(r, g, b)
}
const SURFACE = { light: '#ffffff', dark: '#23221f' } as const

describe('实心图形：保持原色 + 同色系细外框（浅深同一条规则）', () => {
  it('白底：浅色下仍是白，外框是浅灰；深色下仍是白', () => {
    const l = layerIconPaint({ fill: '#ffffff', color: '#dddddd' }, 'light')!
    expect(l.fill).toBe('#ffffff')
    expect(contrastOf(l.stroke, '#ffffff')).toBeGreaterThan(1.6) // 外框看得见
    expect(contrastOf(l.stroke, '#ffffff')).toBeLessThan(4) // 但不比描边类图标重
    expect(layerIconPaint({ fill: '#ffffff' }, 'dark')!.fill).toBe('#ffffff')
  })

  it('黑块：深色下仍是黑（不改成灰），外框比填充亮一档', () => {
    const d = layerIconPaint({ fill: '#000000' }, 'dark')!
    expect(d.fill).toBe('#000000')
    expect(contrastOf(d.stroke, '#000000')).toBeGreaterThan(1.5)
    expect(layerIconPaint({ fill: '#000000' }, 'light')!.fill).toBe('#000000')
  })

  it('亮黄 / 橙：浅色下保留原色相与亮度，外框同色系更深；不靠压暗填充', () => {
    for (const c of ['#F0E442', '#E69F00']) {
      const l = layerIconPaint({ fill: c }, 'light')!
      expect(l.fill).toBe(c.toLowerCase())
      expect(contrastOf(l.stroke, c)).toBeGreaterThan(1.4)
      expect(l.stroke).not.toBe(l.fill)
    }
  })

  it('每个实心图形都带外框：与形状、主题无关', () => {
    for (const theme of ['light', 'dark'] as const)
      for (const c of ['#ffffff', '#000000', '#009E73', '#56B4E9', '#CC79A7']) {
        const p = layerIconPaint({ fill: c }, theme)!
        expect(p.stroke).not.toBe(p.fill)
      }
  })
})

describe('线型 / 文字：对比度夹在 [2.6, 6.5]，有色相的保留色相', () => {
  it.each(['light', 'dark'] as const)('近黑文字 / 白字在 %s 下都落进可读区间', (theme) => {
    for (const c of ['#111111', '#ffffff', '#000000']) {
      const p = layerIconPaint({ color: c }, theme)!
      const cr = contrastOf(p.stroke, SURFACE[theme])
      expect(cr).toBeGreaterThanOrEqual(2.55)
      expect(cr).toBeLessThanOrEqual(6.55)
    }
  })

  it('深色下的黑字、浅色下的白字回落到暖中性灰', () => {
    expect(layerIconPaint({ color: '#111111' }, 'dark')!.stroke).toBe(ICON_NEUTRAL.dark)
    expect(layerIconPaint({ color: '#ffffff' }, 'light')!.stroke).toBe(ICON_NEUTRAL.light)
  })

  it('已经合适的颜色原样留着；红色在深浅两套下色相不变', () => {
    expect(layerIconPaint({ color: '#C0392B' }, 'light')!.stroke).toBe('#c0392b')
    const d = layerIconPaint({ color: '#7a0000' }, 'dark')!.stroke
    expect(contrastOf(d, SURFACE.dark)).toBeGreaterThanOrEqual(2.55)
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(d.slice(i, i + 2), 16))
    expect(r).toBeGreaterThan(g)
    expect(r).toBeGreaterThan(b)
  })

  it('浅色下的亮黄线条：压到可读，仍是黄系', () => {
    const p = layerIconPaint({ color: '#F0E442' }, 'light')!.stroke
    expect(contrastOf(p, '#ffffff')).toBeGreaterThanOrEqual(2.55)
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(p.slice(i, i + 2), 16))
    expect(r).toBeGreaterThan(b)
    expect(g).toBeGreaterThan(b)
  })
})

describe('隐藏的对象：去饱和', () => {
  it('填充与外框 / 线型的饱和度都降下来；中性灰只会更灰', () => {
    const on = layerIconPaint({ fill: '#e76f51' }, 'light')!
    const off = layerIconPaint({ fill: '#e76f51' }, 'light', true)!
    expect(sat(off.fill!)).toBeLessThan(sat(on.fill!) * 0.7)
    expect(sat(off.stroke)).toBeLessThan(sat(on.stroke))
    const line = layerIconPaint({ color: '#0072B2' }, 'dark', true)!
    expect(sat(line.stroke)).toBeLessThan(sat(layerIconPaint({ color: '#0072B2' }, 'dark')!.stroke))
    expect(sat(layerIconPaint({ color: '#ffffff' }, 'light', true)!.stroke)).toBeLessThanOrEqual(sat(ICON_NEUTRAL.light))
  })
})

describe('没有可用颜色', () => {
  it('解析不了 / 没给 = null（调用方用默认灰线条图标）', () => {
    expect(layerIconPaint({}, 'light')).toBeNull()
    expect(layerIconPaint({ color: 'currentColor', fill: 'rgb(1,2,3)' }, 'dark')).toBeNull()
  })
})
