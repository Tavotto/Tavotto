import { describe, expect, it } from 'vitest'
import {
  computeManuscriptLayout,
  PX_PER_MM,
  SINGLE_SLOT_VARIANTS,
  SPAN_SLOT_VARIANTS,
} from './manuscriptPreview'
import { loadProfile, mergeJournalInto } from './profile'

describe('computeManuscriptLayout', () => {
  it('课题组规范（80 / 150 mm）下的 80 × 60 mm 单图：默认落右上单栏，并支持四档期刊常见单栏版位轮换', () => {
    const profile = loadProfile('lab-publication-v1')
    const layout0 = computeManuscriptLayout([80, 60], profile, 0)

    expect(PX_PER_MM).toBeCloseTo(96 / 25.4, 6)
    expect(layout0.sheetW).toBe(210)
    expect(layout0.sheetH).toBe(297)
    expect(layout0.marginX).toBe(30)
    expect(layout0.textBlockW).toBe(150)
    expect(layout0.placement).toBe('single')
    expect(layout0.slotVariant).toBe('top-right')
    expect(layout0.targetColumn.id).toBe('single')
    expect(layout0.slotW).toBe(80)
    expect(layout0.oppositeColW).toBe(64)
    expect(layout0.hasExplicitColumns).toBe(true)
    expect(layout0.showCaption).toBe(true)

    const cycled = SINGLE_SLOT_VARIANTS.map(
      (_, i) => computeManuscriptLayout([80, 60], profile, i).slotVariant,
    )
    expect(cycled).toEqual(['top-right', 'mid-left', 'bottom-right', 'top-left'])
    expect(computeManuscriptLayout([80, 60], profile, 1).leadLines).toBeGreaterThanOrEqual(4)
  })

  it('课题组规范下的 150 × 100 mm 通栏图：支持顶置 / 版中嵌排 / 底置三档通栏版位轮换', () => {
    const profile = loadProfile('lab-publication-v1')
    const variants = SPAN_SLOT_VARIANTS.map(
      (_, i) => computeManuscriptLayout([150, 100], profile, i).slotVariant,
    )
    expect(variants).toEqual(['top-span', 'mid-span', 'bottom-span'])
    const mid = computeManuscriptLayout([150, 100], profile, 1)
    expect(mid.placement).toBe('span')
    expect(mid.leadLines).toBeGreaterThanOrEqual(4)
  })

  it('介于单栏与双栏之间的宽图（115 × 80 mm）：在纯白通栏版芯中水平居中，不附加灰块', () => {
    const profile = loadProfile('lab-publication-v1')
    const layout = computeManuscriptLayout([115, 80], profile)

    expect(layout.placement).toBe('span')
    expect(layout.slotW).toBe(150)
    expect(layout.figX).toBeCloseTo(30 + (150 - 115) / 2, 5)
  })

  it('Nature 双栏规格（89 / 183 mm）放入 80 × 60 mm 图：保持 89 mm 栏宽与 5 mm 栏距', () => {
    const nature = mergeJournalInto(loadProfile('journal-base-v1'), {
      page_columns: [
        { id: 'single', label: { zh: '单栏', en: 'Single column' }, width_mm: 89, qualifier: 'max' },
        { id: 'double', label: { zh: '通栏', en: 'Double column' }, width_mm: 183, qualifier: 'max' },
      ],
    })
    const layout = computeManuscriptLayout([80, 60], nature, 0)

    expect(layout.placement).toBe('single')
    expect(layout.marginX).toBeCloseTo(13.5, 5)
    expect(layout.slotW).toBe(89)
    expect(layout.gutterMm).toBeCloseTo(5, 5)
    expect(layout.oppositeColW).toBeCloseTo(89, 5)
    expect(layout.hasExplicitColumns).toBe(true)
  })

  it('期刊未规定栏宽（journal-base-v1）时 hasExplicitColumns 为 false，不冒充期刊自有栏宽', () => {
    const base = loadProfile('journal-base-v1')
    const layout = computeManuscriptLayout([80, 60], base)

    expect(layout.hasExplicitColumns).toBe(false)
    expect(layout.placement).toBe('single')
  })

  it('超出版芯甚至超出 A4 纸宽的大图（250 × 160 mm）：标记 overflow 且外扩取景框使越界边缘完整可见', () => {
    const profile = loadProfile('lab-publication-v1')
    const layout = computeManuscriptLayout([250, 160], profile)

    expect(layout.placement).toBe('overflow')
    expect(layout.slotVariant).toBe('overflow')
    expect(layout.widthOverflow).toBe(true)
    expect(layout.sheetW).toBe(210)
    expect(layout.viewportW).toBeGreaterThan(250)
    expect(layout.sheetOffsetX + layout.figX).toBeGreaterThanOrEqual(0)
    expect(layout.sheetOffsetX + layout.figX + layout.figW).toBeLessThanOrEqual(layout.viewportW)
  })
})
