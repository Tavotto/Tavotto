/**
 * 子图簇的判据（问题面板「按图」）。夹具照 Figure 2（PRB 单栏三联图）的真实结构：
 * (a) 场分布 + 它自己的色条轴，(b)(c) 两张频谱图 + 一根共用色条轴。
 */
import { describe, expect, it } from 'vitest'
import type { Manifest, ManifestElement } from './api'
import { subplotLookup } from './subplotParts'

const el = (gid: string, bbox: [number, number, number, number], extra: Partial<ManifestElement> = {}) =>
  ({ gid, role: 'axes', label: gid, bbox, editable: [], draggable: false, ...extra }) as ManifestElement

const text = (gid: string, value: string, bbox: [number, number, number, number]) =>
  el(gid, bbox, { role: 'text', label: `文字 “${value}”`, editable: [{ prop: 'text', type: 'text', value }] })

/** 版面：(a) 占上半、(b)(c) 左右并排在下半；标签写在各自坐标系里（`ax.text(0, 1.025, "(a)")`） */
function figure2(overrides: { tags?: boolean; order?: 'bca' } = {}): Manifest {
  const tags = overrides.tags ?? true
  const elements: ManifestElement[] = [
    el('axes_0', [0.11, 0.05, 0.77, 0.42], { label: '子图 1', follow_gids: ['axes_1'] }),
    el('axes_1', [0.9, 0.05, 0.03, 0.42], { label: '色条轴', is_colorbar: true, colorbar_gid: 'axes_1.colorbar' }),
    el('axes_1.colorbar', [0.9, 0.05, 0.03, 0.42], { role: 'colorbar', host_gid: 'axes_0' }),
    el('axes_2', [0.11, 0.55, 0.36, 0.35], { label: '子图 2' }),
    el('axes_3', [0.52, 0.55, 0.36, 0.35], { label: '子图 3' }),
    // 共用色条：宿主记在它服务的第一张图上（引擎 `host_gid`），不在任何人的 follow_gids 里
    el('axes_4', [0.9, 0.57, 0.03, 0.33], { label: '色条轴', is_colorbar: true, colorbar_gid: 'axes_4.colorbar' }),
    el('axes_4.colorbar', [0.9, 0.57, 0.03, 0.33], { role: 'colorbar', host_gid: 'axes_2' }),
    text('axes_0.texts_0', 'Vacuum', [0.13, 0.3, 0.1, 0.03]),
    el('axes_2.ylabel', [0.02, 0.6, 0.03, 0.3], { role: 'axis_label' }),
    el('axes_3.legend', [0.6, 0.57, 0.25, 0.1], { role: 'legend' }),
    el('suptitle', [0.3, 0.0, 0.4, 0.03], { role: 'title' }),
  ]
  if (tags) {
    elements.push(
      text('axes_0.texts_2', '(a)', [0.1, 0.01, 0.02, 0.03]),
      text('axes_2.texts_0', '(b)', [0.1, 0.51, 0.02, 0.03]),
      text('axes_3.texts_0', '(c)', [0.51, 0.51, 0.02, 0.03]),
    )
  }
  return { stem: 'Figure2', size_mm: [86, 90], elements }
}

describe('子图簇', () => {
  it('色条轴归它服务的那张图：follow_gids 优先，否则认色条的 host_gid', () => {
    const at = subplotLookup(figure2())
    expect(at('axes_1.colorbar')?.key).toBe('axes_0')
    expect(at('axes_1.yticklabels_0')?.key).toBe('axes_0')
    expect(at('axes_4.texts_0')?.key).toBe('axes_2')
    expect(at('axes_3.legend.texts_1')?.key).toBe('axes_3')
  })

  it('图里写着的面板标签就是名字；顺序按标签字母', () => {
    const at = subplotLookup(figure2())
    expect(at('axes_0.texts_0')).toMatchObject({ tag: '(a)', order: 0 })
    expect(at('axes_2.ylabel')).toMatchObject({ tag: '(b)', order: 1 })
    expect(at('axes_3')).toMatchObject({ tag: '(c)', order: 2 })
    // 普通文字（Vacuum）不是标签：它和 (a) 在同一个坐标系里，被认成标签的必须是 (a)
    expect(at('axes_0')?.tag).toBe('(a)')
  })

  it('缩略图的框是宿主自己的：共用色条挂在 (b) 名下，但不把 (b) 的框撑到 (c) 那边', () => {
    const at = subplotLookup(figure2())
    expect(at('axes_2')!.bbox).toEqual([0.11, 0.55, 0.36, 0.35])
    expect(at('axes_0')!.bbox).toEqual([0.11, 0.05, 0.77, 0.42])
  })

  it('没写标签：退回引擎的「子图 N」，顺序按版面先行后列', () => {
    const at = subplotLookup(figure2({ tags: false }))
    expect(at('axes_0')).toMatchObject({ tag: null, label: '子图 1', order: 0 })
    expect(at('axes_2')?.order).toBe(1)
    expect(at('axes_3')?.order).toBe(2)
  })

  it('不在任何坐标系里的元素（suptitle）不归子图', () => {
    expect(subplotLookup(figure2())('suptitle')).toBeNull()
  })

  it('只有一个簇（单子图 + 色条）拆不出东西：整张图，一律 null', () => {
    const one: Manifest = {
      stem: 'one',
      size_mm: [80, 60],
      elements: [
        el('axes_0', [0.1, 0.1, 0.7, 0.8], { follow_gids: ['axes_1'] }),
        el('axes_1', [0.85, 0.1, 0.03, 0.8], { is_colorbar: true }),
        text('axes_0.texts_0', '(a)', [0.1, 0.05, 0.02, 0.03]),
      ],
    }
    const at = subplotLookup(one)
    expect(at('axes_0.texts_0')).toBeNull()
    expect(at('axes_1')).toBeNull()
  })

  it('同一个 manifest 只建一次表', () => {
    const m = figure2()
    expect(subplotLookup(m)).toBe(subplotLookup(m))
  })
})
