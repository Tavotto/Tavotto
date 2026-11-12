/**
 * 导出初值跟期刊（`lib/journalExport`，用户 2026-11-11 拍板）：分辨率取期刊最高一档、只升不降；
 * 期刊列了格式时没列出的那种换成同类里列出的，同类一个都没列就留着。
 */
import { describe, expect, it } from 'vitest'
import { applyJournalExport, journalExportAdvice } from './journalExport'
import type { JournalFieldSource } from './journals'

const src = (value: unknown, extra: Partial<JournalFieldSource> = {}) => ({ value, unset: false, ...extra }) as JournalFieldSource
const binding = (sources: Record<string, JournalFieldSource>) => ({
  journalRef: { label: 'Acta Materialia' },
  journalSources: sources,
})

const ELSEVIER = binding({
  'info.dpi_by_kind': src({ halftone: 300, 'combination art': 500, 'line art': 1000 }),
  'info.formats_raw': src({ preferred: ['EPS', 'PDF', 'TIFF', 'JPEG'], accepted: ['Microsoft Office (Word, Excel, PowerPoint)'] }),
})

describe('journalExportAdvice', () => {
  it('没按期刊选：null', () => {
    expect(journalExportAdvice(null)).toBeNull()
    expect(journalExportAdvice({ journalSources: {} })).toBeNull()
  })

  it('按图类分档取最高的一档；格式名折成导得出的那几种（JPEG / Office 不算）', () => {
    const a = journalExportAdvice(ELSEVIER)!
    expect(a.dpi).toBe(1000)
    expect(a.dpiParts).toEqual([
      ['halftone', 300],
      ['combination art', 500],
      ['line art', 1000],
    ])
    expect([...a.listed!].sort()).toEqual(['eps', 'pdf', 'tiff'])
  })

  it('只有硬性下限：取它；超过导出上限的夹到 1200', () => {
    expect(journalExportAdvice(binding({ min_raster_dpi: src(350) }))!.dpi).toBe(350)
    expect(journalExportAdvice(binding({ 'info.dpi_by_kind': src({ line: 2400 }) }))!.dpi).toBe(1200)
  })

  it('列的格式一种都导不出（只有 JPEG）：格式不跟；带说明的名字取开头那个词', () => {
    expect(journalExportAdvice(binding({ 'info.formats_raw': src({ preferred: ['JPEG'], accepted: [] }) }))).toBeNull()
    const a = journalExportAdvice(binding({ 'info.formats_raw': src({ preferred: [], accepted: ['eps（可编辑原始矢量图）'] }) }))!
    expect([...a.listed!]).toEqual(['eps'])
  })

  it('期刊写明「本刊不适用」（unset）的项不算', () => {
    expect(journalExportAdvice(binding({ min_raster_dpi: src(300, { unset: true }) }))).toBeNull()
  })
})

describe('applyJournalExport', () => {
  const a = journalExportAdvice(ELSEVIER)

  it('PDF + PNG → PDF + TIFF，600 → 1000', () => {
    expect(applyJournalExport({ formats: ['pdf', 'png'], dpi: '600' }, a)).toEqual({ formats: ['pdf', 'tiff'], dpi: '1000' })
  })

  it('只升不降：用户默认 1200 就用 1200', () => {
    expect(applyJournalExport({ formats: ['pdf'], dpi: '1200' }, a).dpi).toBe('1200')
  })

  it('同类里期刊列出的已经勾着：没列出的那种直接去掉，不重复', () => {
    expect(applyJournalExport({ formats: ['pdf', 'tiff', 'png'], dpi: '600' }, a).formats).toEqual(['pdf', 'tiff'])
  })

  it('同类一个都没列（期刊只列矢量）：位图留着，不把格式清空', () => {
    const vec = journalExportAdvice(binding({ 'info.formats_raw': src({ preferred: ['PDF'], accepted: [] }) }))
    expect(applyJournalExport({ formats: ['png'], dpi: '600' }, vec).formats).toEqual(['png'])
  })

  it('幂等；没有期刊建议时原样返回', () => {
    const once = applyJournalExport({ formats: ['pdf', 'png'], dpi: '600' }, a)
    expect(applyJournalExport(once, a)).toEqual(once)
    expect(applyJournalExport({ formats: ['svg'], dpi: '300' }, null)).toEqual({ formats: ['svg'], dpi: '300' })
  })
})
