import { describe, expect, it } from 'vitest'
import { readingOrder } from './geometry'

const p = (id: string, x: number, y: number, h?: number) => ({ id, x, y, h })
const ids = (xs: { id: string }[]) => xs.map((o) => o.id).join('')

describe('readingOrder：按行带分行，行内从左到右', () => {
  it('2×2 摆放：a b / c d，与输入顺序无关', () => {
    expect(ids(readingOrder([p('d', 100, 80, 60), p('b', 100, 0, 60), p('c', 0, 80, 60), p('a', 0, 0, 60)]))).toBe('abcd')
  })
  it('同一行里高矮不一（顶边差 > 5mm、中心仍同行）：不因顶边先后把右边的排到左边前面', () => {
    expect(ids(readingOrder([p('R', 100, 20, 40), p('L', 0, 0, 80)]))).toBe('LR')
  })
  it('上下叠放：下面一张整体在下一行，即使它比上一张更靠左', () => {
    expect(ids(readingOrder([p('below', 0, 70, 60), p('top', 50, 0, 60)]))).toBe('topbelow')
  })
  it('一行三张高低错落也稳定，再接下一行', () => {
    expect(ids(readingOrder([p('c', 200, 8, 60), p('a', 0, 0, 60), p('b', 100, 4, 60), p('z', 0, 90, 40)]))).toBe('abcz')
  })
  it('没有高度的元素按 5mm 带宽（与旧行为一致）', () => {
    expect(ids(readingOrder([p('b', 50, 3), p('a', 0, 0), p('c', 0, 20)]))).toBe('abc')
  })
})
