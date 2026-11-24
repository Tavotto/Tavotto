/**
 * 图内副本的判据与换算（ADR 0207，`lib/figureElementCopy`）：
 * gid 闭集与引擎同源（共享向量 `tests/golden/figure_copy_source_vectors.json`，引擎一侧由 pytest 跑）、
 * 宿主 3D / 色条判据、样式抄送、落点（含面板旋转 / 翻转、收回图内、子图 position 换算）。
 */
import fs from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'
import type { Manifest, ManifestElement, ManifestTextBlock } from '@/lib/api'
import {
  carriedStyleOverrides,
  COPY_GID_RE,
  copyHostOf,
  copyPlacementOf,
  copyStyleOverrides,
  duplicationOrigin,
  isCopyGid,
  isCopySourceGid,
  isDuplicableElement,
  newCopyGid,
  pageOffsetInFigureFrac,
  retractIntoFigure,
  shownTextOf,
} from '@/lib/figureElementCopy'
import { panelFullRect } from '@/lib/elementGeom'
import { contentToPageVec, panelContentTransform } from '@/lib/panelTransform'
import { DUPLICATE_OFFSET_MM } from '@/lib/units'
import type { PanelObject } from '@/types/document'

const vectors = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, '../../../tests/golden/figure_copy_source_vectors.json'), 'utf8'),
) as { source: { gid: string; supported: boolean }[]; copy_gid: { gid: string; copy: boolean }[] }

describe('与引擎同源的 gid 判据（共享向量）', () => {
  it.each(vectors.source.map((c) => [c.gid, c.supported] as const))('源 %j → %s', (gid, supported) => {
    expect(isCopySourceGid(gid)).toBe(supported)
    expect(copyHostOf(gid) !== null).toBe(supported)
  })
  it.each(vectors.copy_gid.map((c) => [c.gid, c.copy] as const))('副本 gid %j → %s', (gid, copy) => {
    expect(COPY_GID_RE.test(gid)).toBe(copy)
    expect(isCopyGid(gid)).toBe(copy)
  })
  it('宿主取 gid 的第一段', () => {
    expect(copyHostOf('axes_3.ylabel')).toBe('axes_3')
    expect(copyHostOf('fig.texts_2')).toBe('fig')
  })
  it('newCopyGid：形状合引擎、宿主在前、撞了重抽', () => {
    const taken = new Set<string>()
    for (let i = 0; i < 50; i++) {
      const g = newCopyGid('axes_1', taken)
      expect(COPY_GID_RE.test(g)).toBe(true)
      expect(g.startsWith('axes_1.copy_')).toBe(true)
      expect(taken.has(g)).toBe(false)
      taken.add(g)
    }
    expect(COPY_GID_RE.test(newCopyGid('fig', taken))).toBe(true)
  })
})

const TB: ManifestTextBlock = {
  center: [0.5, 0.1], w: 0.2, h: 0.06, asc: 0.045, text: 'Reaction', math: false,
  size: 10, color: '#112233', weight: 'normal', style: 'normal', rotation: 0, ha: 'center',
}
const text = (gid: string, over: Partial<ManifestElement> = {}): ManifestElement =>
  ({
    gid, role: 'title', label: gid, bbox: [0.4, 0.05, 0.2, 0.06], anchor: [0.5, 0.11],
    editable: [{ prop: 'text', value: 'Reaction' }], draggable: true, text_block: TB, ...over,
  }) as unknown as ManifestElement
const axes = (gid = 'axes_0', over: Partial<ManifestElement> = {}): ManifestElement =>
  ({
    gid, role: 'axes', label: gid, bbox: [0.1, 0.1, 0.8, 0.8],
    editable: [{ prop: 'position', value: [0.1, 0.1, 0.8, 0.8] }], draggable: false, ...over,
  }) as unknown as ManifestElement
const manifest = (...elements: ManifestElement[]): Manifest => ({ stem: 'f', size_mm: [100, 75], elements })
const panel = (over: Partial<PanelObject> = {}): PanelObject =>
  ({
    id: 'p1', type: 'panel', x: 10, y: 20, w: 100, h: 75, fileId: 'f.pdf', fileKind: 'pdf',
    nativeW: 100, nativeH: 75, script: 'f.py', overrides: [], ...over,
  }) as unknown as PanelObject

describe('isDuplicableElement', () => {
  const p = panel()
  it('闭集内的文字、宿主是普通子图：可以', () => {
    const m = manifest(axes(), text('axes_0.title'))
    expect(isDuplicableElement(p, m, m.elements[1])).toBe(true)
  })
  it('整图的 fig.texts_N：不需要宿主元素', () => {
    const m = manifest(text('fig.texts_0'))
    expect(isDuplicableElement(p, m, m.elements[0])).toBe(true)
  })
  it('没有 text_block / 没有锚点 / gid 不在闭集：不行', () => {
    const m = manifest(axes(), text('axes_0.title', { text_block: undefined }), text('axes_0.title_x'), text('axes_0.xlabel', { anchor: undefined }))
    for (const e of m.elements.slice(1)) expect(isDuplicableElement(p, m, e)).toBe(false)
  })
  it('宿主是 3D 子图 / 色条轴 / 宿主不在清单里：不行', () => {
    const t = text('axes_0.title')
    expect(isDuplicableElement(p, manifest(axes('axes_0', { role: 'axes3d' }), t), t)).toBe(false)
    expect(isDuplicableElement(p, manifest(axes('axes_0', { is_colorbar: true }), t), t)).toBe(false)
    expect(isDuplicableElement(p, manifest(t), t)).toBe(false)
  })
  it('刻度数字 / 图例文字这类闭集外的 gid 即使有 text_block 也不行', () => {
    const m = manifest(axes(), text('axes_0.xaxis.ticklabels_0'), text('axes_0.legend.texts_0'))
    for (const e of m.elements.slice(1)) expect(isDuplicableElement(p, m, e)).toBe(false)
  })
  it('选中的是副本：按它的 copy_of.src 算出处，副本可以再复制；copy_of 没了就不行', () => {
    const cg = 'axes_0.copy_abcd1234'
    const withCopy = panel({ overrides: [{ gid: cg, prop: 'copy_of', value: { src: 'axes_0.title', text: 'x', at: [0.5, 0.5] } }] })
    const m = manifest(axes(), text('axes_0.title'), text(cg))
    expect(duplicationOrigin(withCopy, m.elements[2])).toEqual({ host: 'axes_0', src: 'axes_0.title', styleFrom: cg })
    expect(isDuplicableElement(withCopy, m, m.elements[2])).toBe(true)
    expect(isDuplicableElement(panel(), m, m.elements[2])).toBe(false)
    // 宿主对不上的脏数据
    const bad = panel({ overrides: [{ gid: cg, prop: 'copy_of', value: { src: 'axes_1.title', text: 'x', at: [0, 0] } }] })
    expect(isDuplicableElement(bad, m, m.elements[2])).toBe(false)
  })
})

describe('样式抄送', () => {
  it('抄字体 / 字号 / 颜色 / 转角 / 底框等，不抄 text / pos_frac / visible / copy_of / identity，重复取最后一条', () => {
    const p = panel({
      overrides: [
        { gid: 'axes_0.title', prop: 'fontsize', value: 12, identity: 'l1:abc' },
        { gid: 'axes_0.title', prop: 'text', value: 'Hello' },
        { gid: 'axes_0.title', prop: 'pos_frac', value: [0.1, 0.1] },
        { gid: 'axes_0.title', prop: 'visible', value: false },
        { gid: 'axes_0.title', prop: 'color', value: '#ff0000' },
        { gid: 'axes_0.title', prop: 'fontsize', value: 14 },
        { gid: 'axes_0.xlabel', prop: 'fontsize', value: 99 },
      ],
    })
    const got = carriedStyleOverrides(p, 'axes_0.title', 'axes_0.copy_abcd1234')
    expect(got).toEqual([
      { gid: 'axes_0.copy_abcd1234', prop: 'color', value: '#ff0000' },
      { gid: 'axes_0.copy_abcd1234', prop: 'fontsize', value: 14 },
    ])
  })
  it('值是深拷贝（后改源不牵动副本）', () => {
    const v = { a: 1 }
    const p = panel({ overrides: [{ gid: 'axes_0.title', prop: 'bbox', value: v }] })
    const got = carriedStyleOverrides(p, 'axes_0.title', 'axes_0.copy_abcd1234')
    expect(got[0].value).toEqual(v)
    expect(got[0].value).not.toBe(v)
  })
  it('copyStyleOverrides：副本 gid 上除 copy_of 之外的修改', () => {
    const cg = 'fig.copy_abcd1234'
    const ov = [
      { gid: cg, prop: 'copy_of', value: {} },
      { gid: cg, prop: 'color', value: '#000' },
      { gid: 'fig.texts_0', prop: 'color', value: '#111' },
    ]
    expect(copyStyleOverrides(ov, cg).map((o) => o.prop)).toEqual(['color'])
  })
  it('shownTextOf：override > 可编辑值 > text_block', () => {
    const el = text('axes_0.title')
    expect(shownTextOf(panel(), el)).toBe('Reaction')
    expect(shownTextOf(panel(), { ...el, editable: [] } as ManifestElement)).toBe('Reaction')
    expect(shownTextOf(panel({ overrides: [{ gid: 'axes_0.title', prop: 'text', value: 'New' }] }), el)).toBe('New')
  })
})

describe('落点', () => {
  const m = manifest(axes(), text('axes_0.title'), text('fig.texts_0'))
  const title = m.elements[1]

  it('子图：锚点右下错开 4 mm，换成 axes 分数（position 是 figure 分数、y 向上）', () => {
    const got = copyPlacementOf(panel(), m, title)!
    // 100 × 75 mm 的图：4 mm = 0.04 × 0.05333
    expect(got.host).toBe('axes_0')
    expect(got.src).toBe('axes_0.title')
    expect(got.text).toBe('Reaction')
    expect(got.at[0]).toBeCloseTo((0.5 + 0.04 - 0.1) / 0.8, 9)
    expect(got.at[1]).toBeCloseTo((1 - (0.11 + 4 / 75) - 0.1) / 0.8, 9)
  })

  it('整图文字：figure 分数，y 翻成向上', () => {
    const got = copyPlacementOf(panel(), m, m.elements[2])!
    expect(got.host).toBe('fig')
    expect(got.at[0]).toBeCloseTo(0.54, 9)
    expect(got.at[1]).toBeCloseTo(1 - (0.11 + 4 / 75), 9)
  })

  it('位移换算：页面右下 4 mm，面板转 90° / 180° / 270° / 翻转都按页面上的右下', () => {
    for (const over of [{}, { rotation: 90 }, { rotation: 180 }, { rotation: 270 }, { flipH: true }, { flipV: true }, { rotation: 90, flipH: true }]) {
      const p = panel(over as Partial<PanelObject>)
      const d = pageOffsetInFigureFrac(p)!
      const full = panelFullRect(p)
      const page = contentToPageVec(panelContentTransform(p), d[0] * full.w, d[1] * full.h)
      expect(page[0]).toBeCloseTo(DUPLICATE_OFFSET_MM, 9)
      expect(page[1]).toBeCloseTo(DUPLICATE_OFFSET_MM, 9)
    }
    // 转 90° 时内容里的「页面右下」是内容的右上：x 正、y 负
    const d90 = pageOffsetInFigureFrac(panel({ rotation: 90 } as Partial<PanelObject>))!
    expect(d90[0]).toBeGreaterThan(0)
    expect(d90[1]).toBeLessThan(0)
  })

  it('错出图外就收回图内：贴着右下角的字不会被推出图', () => {
    const edge = text('axes_0.xlabel', { bbox: [0.8, 0.95, 0.2, 0.05], anchor: [0.9, 0.97] })
    const got = copyPlacementOf(panel(), manifest(axes(), edge), edge)!
    // 右下都已贴边：不再往外走，落在源的锚点上
    expect(got.at[0]).toBeCloseTo((0.9 - 0.1) / 0.8, 9)
    expect(got.at[1]).toBeCloseTo((1 - 0.97 - 0.1) / 0.8, 9)
    expect(retractIntoFigure([0.8, 0.5, 0.15, 0.1], [0.04, 0.05])).toEqual([0.04, 0.05])
    expect(retractIntoFigure([0.9, 0.5, 0.15, 0.1], [0.04, 0.05])[0]).toBe(0) // 本来就探出右边：不再往外
    expect(retractIntoFigure([0.2, 0.2, 0.1, 0.1], [-0.3, 0])[0]).toBeCloseTo(-0.2, 12) // 往回走也不越过左边
  })

  it('选中的是副本：落点按副本此刻画出来的位置，src 仍是原来的源，样式从副本 gid 抄', () => {
    const cg = 'axes_0.copy_abcd1234'
    const p = panel({ overrides: [{ gid: cg, prop: 'copy_of', value: { src: 'axes_0.title', text: 'Reaction', at: [0.5, 0.5] } }] })
    const copy = text(cg, { anchor: [0.6, 0.2], bbox: [0.5, 0.15, 0.2, 0.06] })
    const got = copyPlacementOf(p, manifest(axes(), title, copy), copy)!
    expect(got.src).toBe('axes_0.title')
    expect(got.styleFrom).toBe(cg)
    expect(got.at[0]).toBeCloseTo((0.6 + 0.04 - 0.1) / 0.8, 9)
  })

  it('算不出来（没有文字、宿主没有 position、面板尺寸为 0）→ null', () => {
    expect(copyPlacementOf(panel(), manifest(axes('axes_0', { editable: [] }), title), title)).toBeNull()
    expect(copyPlacementOf(panel({ w: 0, h: 0, nativeW: 0, nativeH: 0 } as Partial<PanelObject>), m, title)).toBeNull()
  })
})
