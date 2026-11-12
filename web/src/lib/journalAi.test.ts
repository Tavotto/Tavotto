/**
 * 改图助手的期刊上下文（`lib/journalAi`）：只给期刊写了的项；图宽只给 exact 栏位；分辨率只给折进预检的下限。
 */
import { describe, expect, it } from 'vitest'
import { journalAiContext } from './journalAi'
import type { JournalFieldSource } from './journals'

const src = (value: unknown, extra: Partial<JournalFieldSource> = {}) =>
  ({ value, unset: false, affects: 'preflight', qualifier: 'exact', ...extra }) as JournalFieldSource

describe('journalAiContext', () => {
  it('没按期刊：null；期刊什么都没写：null', () => {
    expect(journalAiContext(null)).toBeNull()
    expect(journalAiContext({ journalRef: { label: 'J' }, journalSources: {} })).toBeNull()
  })

  it('字号 / 字体 / 线宽 / exact 栏位 / 预检分辨率', () => {
    const ctx = journalAiContext({
      journalRef: { label: 'Acta Materialia' },
      journalSources: {
        min_effective_font_size_pt: src(7, { affects: 'advice', qualifier: 'typical' }),
        'font_family.latin_accepted': src(['Arial', 'Helvetica'], { affects: 'info' }),
        'info.min_line_width_pt': src(0.5, { affects: 'info' }),
        page_columns: src([
          { id: 'a', label: { zh: '1', en: '1' }, width_mm: 90, qualifier: 'exact' },
          { id: 'b', label: { zh: '1.5', en: '1.5' }, width_mm: 140, qualifier: 'typical' },
          { id: 'c', label: { zh: '2', en: '2' }, width_mm: 190 },
        ]),
        min_raster_dpi: src(300),
      },
    })
    expect(ctx).toEqual({
      label: 'Acta Materialia',
      min_font_pt: 7,
      fonts: ['Arial', 'Helvetica'],
      min_line_pt: 0.5,
      widths_mm: [90, 190],
      min_dpi: 300,
    })
  })

  it('没有栏位表时用期刊规定的单 / 双栏；只进 info 的分辨率不给', () => {
    const ctx = journalAiContext({
      journalRef: { label: 'J' },
      journalSources: {
        'widths_mm.single': src(89),
        'widths_mm.double': src(183),
        min_raster_dpi: src(300, { affects: 'info' }),
      },
    })
    expect(ctx).toEqual({ label: 'J', widths_mm: [89, 183] })
  })
})
