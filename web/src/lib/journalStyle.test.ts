/**
 * 「按期刊调整样式」的判据（`lib/journalStyle`，用户 2026-11-11 拍板：字号抬下限、压上限、字体换期刊接受的、线宽抬下限）。
 */
import { describe, expect, it } from 'vitest'
import { journalStyleEdits, journalStyleTargets, type JournalStyleInput, type JournalStyleTargets } from './journalStyle'
import type { JournalFieldSource } from './journals'

const src = (value: unknown, extra: Partial<JournalFieldSource> = {}) => ({ value, unset: false, ...extra }) as JournalFieldSource
const T = (over: Partial<JournalStyleTargets> = {}): JournalStyleTargets => ({
  journal: 'J',
  minPt: null,
  maxPt: null,
  latin: null,
  minLinePt: null,
  ...over,
})
const input = (over: Partial<JournalStyleInput> = {}): JournalStyleInput => ({
  style: null,
  observed: {},
  annotationSizes: [],
  renderable: ['DejaVu Sans', 'Arial', 'Times New Roman'],
  ...over,
})

describe('journalStyleTargets', () => {
  it('从绑定里的出处取数：推荐 / 区间也算，unset 不算；没有可调的 = null', () => {
    const t = journalStyleTargets({
      journalRef: { label: 'Acta Materialia' },
      journalSources: {
        min_effective_font_size_pt: src(7, { affects: 'advice', qualifier: 'typical' }),
        'font_family.latin_accepted': src(['Arial', 'Helvetica']),
        'info.min_line_width_pt': src(0.5, { unset: true }),
      },
    })!
    expect(t).toEqual({ journal: 'Acta Materialia', minPt: 7, maxPt: null, latin: ['Arial', 'Helvetica'], minLinePt: null })
    expect(journalStyleTargets({ journalRef: { label: 'x' }, journalSources: {} })).toBeNull()
    expect(journalStyleTargets(null)).toBeNull()
  })

  it('上下限写反了：字号不调', () => {
    const t = journalStyleTargets({
      journalRef: { label: 'x' },
      journalSources: { min_effective_font_size_pt: src(9), max_font_size_pt: src(7), 'info.min_line_width_pt': src(0.5) },
    })!
    expect(t.minPt).toBeNull()
    expect(t.maxPt).toBeNull()
  })
})

describe('journalStyleEdits', () => {
  it('字号：图上最常见的值低于下限 → 抬到下限；已在范围内的角色不动', () => {
    const edits = journalStyleEdits(
      input({ observed: { ticks: { fontsize: [6, 6, 6] }, axis_label: { fontsize: [9, 9] } } }),
      T({ minPt: 7 }),
    )
    expect(edits).toEqual([{ kind: 'element', role: 'ticks', prop: 'fontsize', value: 7, from: 6 }])
  })

  it('字号：样式里设了就以样式为准；高于上限的压下来（不压低更大的那一边以外的角色）', () => {
    const edits = journalStyleEdits(
      input({
        style: { element: { title: { fontsize: 12 }, legend: { fontsize: 7 } } },
        observed: { title: { fontsize: [9] }, legend: { fontsize: [5] } },
      }),
      T({ minPt: 5, maxPt: 7 }),
    )
    expect(edits).toEqual([{ kind: 'element', role: 'title', prop: 'fontsize', value: 7, from: 12 }])
  })

  it('画布标注只抬下限，不压上限', () => {
    const edits = journalStyleEdits(input({ annotationSizes: [6, 6, 14] }), T({ minPt: 7, maxPt: 8 }))
    expect(edits).toEqual([{ kind: 'annotation', prop: 'sizePt', value: 7, from: 6 }])
  })

  it('线宽：只抬不压；0 宽不算违规', () => {
    const edits = journalStyleEdits(
      input({ observed: { line: { linewidth: [0.3, 0.3] }, axes: { spine_linewidth: [0, 0] }, ticks: { width: [1] } } }),
      T({ minLinePt: 0.5 }),
    )
    expect(edits).toEqual([{ kind: 'element', role: 'line', prop: 'linewidth', value: 0.5, from: 0.3 }])
  })

  it('字体：不在期刊列表里的换成列表里第一个本机画得出的；大小写不敏感', () => {
    const edits = journalStyleEdits(
      input({ observed: { ticks: { fontfamily: ['DejaVu Sans'] }, title: { fontfamily: [['arial', 'sans-serif']] } } }),
      T({ latin: ['Helvetica', 'Arial'] }),
    )
    expect(edits).toEqual([{ kind: 'element', role: 'ticks', prop: 'fontfamily', value: 'Arial', from: 'DejaVu Sans' }])
  })

  it('字体：期刊列的一个都画不出 → 不换', () => {
    const edits = journalStyleEdits(
      input({ observed: { ticks: { fontfamily: ['DejaVu Sans'] } }, renderable: ['DejaVu Sans'] }),
      T({ latin: ['Helvetica'] }),
    )
    expect(edits).toEqual([])
  })

  it('没有期刊目标 = 空', () => {
    expect(journalStyleEdits(input({ observed: { ticks: { fontsize: [4] } } }), null)).toEqual([])
  })
})
