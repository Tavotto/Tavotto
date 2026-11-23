/**
 * 铭牌刻名的量法：允许范围内（≤ 24 字）的名字完整落在牌面上——贴图（1020 宽 SVG）与 CSS 回退版都不裁、不溢出。
 * 主语：估宽 ≤ 可用宽度、字号不低于下限；贴图 SVG 里写进去的字号就是量出来的那个。
 */
import { describe, expect, it } from 'vitest'
import { PLATE_NAME_MAX } from '../FoundingPlate'
import { face } from './plateFaces'
import { NAME_AVAIL, NAME_SIZE, NAME_SIZE_MIN, nameCqw, nameEm, nameFit } from './plateName'

const CJK24 = '林'.repeat(PLATE_NAME_MAX)
const LATIN_WIDE = 'W'.repeat(PLATE_NAME_MAX)
const LATIN_UPPER = 'ABCDEFGHIJKLMNOPQRSTUVWX'
const LATIN_MIXED = 'Maximilian von Habsburg-Lo'.slice(0, PLATE_NAME_MAX)
const FULLWIDTH = 'ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸ'

describe('铭牌刻名：最长的名字也完整显示', () => {
  it('24 字上限与引擎一致', () => {
    expect(PLATE_NAME_MAX).toBe(24)
  })

  it.each([
    ['24 个汉字', CJK24],
    ['24 个 W（拉丁里最宽）', LATIN_WIDE],
    ['24 个大写字母', LATIN_UPPER],
    ['24 个全角字母', FULLWIDTH],
    ['中英混排的长名', LATIN_MIXED],
  ])('%s：估宽在可用宽度内、字号不低于下限、不需要 textLength 兜底', (_n, name) => {
    const fit = nameFit(name)
    expect([...name].length).toBeLessThanOrEqual(PLATE_NAME_MAX)
    expect(fit.width).toBeLessThanOrEqual(NAME_AVAIL)
    expect(fit.size).toBeGreaterThanOrEqual(NAME_SIZE_MIN)
    expect(fit.size).toBeLessThanOrEqual(NAME_SIZE)
    expect(fit.clamp).toBe(false)
  })

  it('短名字照旧 64 号、2.5 字距（与官网版面一致）', () => {
    expect(nameFit('林小雨')).toMatchObject({ size: NAME_SIZE, ls: 2.5, clamp: false })
    expect(nameFit('早期同行者')).toMatchObject({ size: NAME_SIZE })
    expect(nameFit('Ada Lovelace').size).toBe(NAME_SIZE)
  })

  it('超出允许范围的名字（不该出现）：缩到下限、收字距，估宽还放不下就交给 textLength 兜底', () => {
    const fit = nameFit('林'.repeat(60))
    expect(fit).toMatchObject({ size: NAME_SIZE_MIN, ls: 0, clamp: true })
  })

  it('贴图 SVG 里写进去的字号就是量出来的字号；兜底只在放不下时出现', () => {
    const ok = decodeURIComponent(face('front', { name: CJK24, label: 'L', serial: 'S', date: 'D' }, 'color'))
    const size = nameFit(CJK24).size
    expect(ok).toContain(`font-size="${size}"`)
    expect(ok).not.toContain('textLength')
    const over = decodeURIComponent(face('front', { name: '林'.repeat(60), label: 'L', serial: 'S', date: 'D' }, 'color'))
    expect(over).toContain(`textLength="${NAME_AVAIL}"`)
  })

  it('CSS 回退版：字号（cqw）× 估宽 ≤ 牌面文字区（84.8cqw），24 字不溢出', () => {
    for (const name of [CJK24, LATIN_WIDE, LATIN_UPPER, FULLWIDTH, LATIN_MIXED]) {
      const cqw = nameCqw(name)
      const n = [...name].length
      expect(cqw * (nameEm(name) + n * 0.04)).toBeLessThanOrEqual(84.8 + 0.01)
      expect(cqw).toBeGreaterThan(3) // 约 10px（320 宽的牌）：小但看得清
    }
    expect(nameCqw('林小雨')).toBe(6.27)
    expect(nameCqw('')).toBe(6.27)
  })
})
