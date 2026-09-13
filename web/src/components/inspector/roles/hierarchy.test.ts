/**
 * 元素归属的判据（元素树建树与属性页面包屑共用这一份）。
 */
import { describe, expect, it } from 'vitest'
import { containerGid, parentGid } from './hierarchy'

const known = new Set([
  'figure',
  'axes_0',
  'axes_0.xticks',
  'axes_0.legend',
  'axes_0.legend.texts_0',
  'axes_0.bars_0',
  'axes_0.bars_0.bar_2',
  'axes_0.title',
])
const has = (g: string) => known.has(g)
const isAxes = (g: string) => g === 'axes_0'

describe('parentGid', () => {
  it('刻度文字归到同轴的刻度组，不按段收缩到子图', () => {
    expect(parentGid('axes_0.xticklabels_3', has)).toBe('axes_0.xticks')
  })
  it('没有刻度组时按段收缩', () => {
    expect(parentGid('axes_0.yticklabels_3', has)).toBe('axes_0')
  })
  it('按段收缩到最近的已知祖先；根是 figure', () => {
    expect(parentGid('axes_0.legend.texts_0', has)).toBe('axes_0.legend')
    expect(parentGid('axes_0.bars_0.bar_2', has)).toBe('axes_0.bars_0')
    expect(parentGid('axes_0.title', has)).toBe('axes_0')
    expect(parentGid('axes_0', has)).toBe('figure')
    expect(parentGid('figure', has)).toBeNull()
  })
})

describe('containerGid：面包屑里子图与元素之间那一级', () => {
  it('图例项 → 图例，刻度文字 → 刻度组，柱 → 柱形系列', () => {
    expect(containerGid('axes_0.legend.texts_0', has, isAxes)).toBe('axes_0.legend')
    expect(containerGid('axes_0.xticklabels_3', has, isAxes)).toBe('axes_0.xticks')
    expect(containerGid('axes_0.bars_0.bar_2', has, isAxes)).toBe('axes_0.bars_0')
  })
  it('子图直属元素与子图本身没有这一级', () => {
    expect(containerGid('axes_0.title', has, isAxes)).toBeNull()
    expect(containerGid('axes_0', has, isAxes)).toBeNull()
    expect(containerGid('figure', has, isAxes)).toBeNull()
  })
})
