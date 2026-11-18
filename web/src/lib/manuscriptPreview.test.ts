import { describe, expect, it } from 'vitest'
import { computeManuscriptLayout } from './manuscriptPreview'
import { loadProfile, mergeJournalInto } from './profile'

describe('computeManuscriptLayout', () => {
  it('课题组规范（80 / 150 mm）下的 80 × 60 mm 单图：排入左上单栏，右栏从顶流式绕排', () => {
    const profile = loadProfile('lab-publication-v1')
    const layout = computeManuscriptLayout([80, 60], profile)

    expect(layout.sheetW).toBe(210)
    expect(layout.sheetH).toBe(297)
    expect(layout.viewportW).toBe(210)
    expect(layout.viewportH).toBe(297)
    expect(layout.marginX).toBe(30)
    expect(layout.textBlockW).toBe(150)
    expect(layout.placement).toBe('single')
    expect(layout.targetColumn.id).toBe('single')
    expect(layout.targetColumn.w).toBe(80)
    expect(layout.figX).toBe(30)
    expect(layout.figW).toBe(80)
    expect(layout.figH).toBe(60)
    expect(layout.showSlotGuide).toBe(false)
    expect(layout.hasExplicitColumns).toBe(true)
    expect(layout.caption).not.toBeNull()

    // 右栏从版芯顶部起步绕排，左栏在图注之后才开始
    const rightTopLines = layout.bodyLines.filter(
      (l) => l.x > layout.slotX + layout.slotW && l.y < layout.figY + layout.figH,
    )
    const leftOverlappingLines = layout.bodyLines.filter(
      (l) => l.x < layout.slotX + layout.slotW && l.y < layout.figY + layout.figH,
    )
    expect(rightTopLines.length).toBeGreaterThan(5)
    expect(leftOverlappingLines).toHaveLength(0)
  })

  it('课题组规范下的 150 × 100 mm 通栏图：跨双栏版芯顶置，下方双栏正文并排', () => {
    const profile = loadProfile('lab-publication-v1')
    const layout = computeManuscriptLayout([150, 100], profile)

    expect(layout.placement).toBe('span')
    expect(layout.targetColumn.id).toBe('double')
    expect(layout.figX).toBe(30)
    expect(layout.figW).toBe(150)
    expect(layout.showSlotGuide).toBe(false)
    expect(layout.bodyLines.every((l) => l.y > layout.figY + layout.figH)).toBe(true)
  })

  it('介于单栏与双栏之间的尴尬图幅（115 × 80 mm）：跨栏居中并亮出栏位参考底衬', () => {
    const profile = loadProfile('lab-publication-v1')
    const layout = computeManuscriptLayout([115, 80], profile)

    expect(layout.placement).toBe('span')
    expect(layout.slotW).toBe(150)
    expect(layout.figX).toBeCloseTo(30 + (150 - 115) / 2, 5)
    expect(layout.showSlotGuide).toBe(true)
  })

  it('Nature 双栏规格（89 / 183 mm）放入 80 × 60 mm 图：居中在 89 mm 左栏槽内并保持 5 mm 栏距', () => {
    const nature = mergeJournalInto(loadProfile('journal-base-v1'), {
      page_columns: [
        { id: 'single', label: { zh: '单栏', en: 'Single column' }, width_mm: 89, qualifier: 'max' },
        { id: 'double', label: { zh: '通栏', en: 'Double column' }, width_mm: 183, qualifier: 'max' },
      ],
    })
    const layout = computeManuscriptLayout([80, 60], nature)

    expect(layout.placement).toBe('single')
    expect(layout.marginX).toBeCloseTo(13.5, 5)
    expect(layout.slotW).toBe(89)
    expect(layout.figX).toBeCloseTo(13.5 + (89 - 80) / 2, 5)
    expect(layout.showSlotGuide).toBe(true)
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
    expect(layout.widthOverflow).toBe(true)
    expect(layout.sheetW).toBe(210)
    expect(layout.viewportW).toBeGreaterThan(250)
    expect(layout.sheetOffsetX + layout.figX).toBeGreaterThanOrEqual(0)
    expect(layout.sheetOffsetX + layout.figX + layout.figW).toBeLessThanOrEqual(layout.viewportW)
  })
})
