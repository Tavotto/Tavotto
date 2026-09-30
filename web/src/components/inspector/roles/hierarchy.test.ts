/**
 * 元素归属的判据（元素树建树与属性页面包屑共用这一份）。
 */
import { describe, expect, it } from 'vitest'
import { ancestorsOf, containerGid, parentGid, structuralParent } from './hierarchy'

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

describe('structuralParent：显式父级优先，路径回退', () => {
  const el = (gid: string, parent_gid?: string) =>
    ({ gid, role: 'axes', label: gid, bbox: [0, 0, 1, 1], editable: [], draggable: false, parent_gid }) as never
  const manifest = {
    stem: 's',
    size_mm: [10, 10],
    elements: [
      el('figure'),
      el('axes_0'),
      el('axes_1', 'group:axes_3'),
      el('axes_1.images_0'),
      el('axes_2', 'group:axes_3'),
      el('axes_3', 'group:axes_3'),
      el('axes_3.colorbar'),
      el('axes_4', 'axes_0'),
      // 显式父级指向这份 manifest 里没有的节点：回退到路径，不挂到虚空里
      el('axes_5', 'group:gone'),
      // 两个显式父级互相指：祖先链截断，不死循环
      el('axes_6', 'axes_7'),
      el('axes_7', 'axes_6'),
    ],
    groups: [{ gid: 'group:axes_3', members: ['axes_1', 'axes_2', 'axes_3'] }],
  } as never
  const parentOf = structuralParent(manifest)

  it('组挂在整张图下；成员挂在组下；成员的后代仍按路径挂在成员下', () => {
    expect(parentOf('group:axes_3')).toBe('figure')
    expect(parentOf('axes_1')).toBe('group:axes_3')
    expect(parentOf('axes_1.images_0')).toBe('axes_1')
    expect(parentOf('axes_3.colorbar')).toBe('axes_3')
    expect(ancestorsOf(parentOf, 'axes_3.colorbar')).toEqual(['figure', 'group:axes_3', 'axes_3'])
  })

  it('单宿主色条的色条轴挂回子图；指向不存在节点的显式父级回退到路径', () => {
    expect(parentOf('axes_4')).toBe('axes_0')
    expect(parentOf('axes_5')).toBe('figure')
  })

  it('显式父级成环时祖先链截断', () => {
    expect(ancestorsOf(parentOf, 'axes_6')).toEqual(['axes_7'])
  })
})
