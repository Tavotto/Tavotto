/**
 * 改图助手的期刊上下文（2026-11-11「期刊贯通」）：项目按期刊检查时，发给 `/api/ai/run` 的请求带一份期刊要求的**结构化摘要**，
 * 后端 `ai_prompt_pack.journal_context` 把它写成提示词里的一句（文字只在 Prompt Pack 里，这里只给数）。
 *
 * 只读文档绑定里存的精简出处（ADR 0155 §4）：字号上下限 / 字体 / 线宽下限沿用「按期刊调整样式」同一份取数（`journalStyleTargets`），
 * 图宽只给期刊**规定了**的 exact 栏位，分辨率只给折进预检的下限。期刊没写的项不出现；什么都没有 = 不带这一段。
 */
import { journalStyleTargets } from './journalStyle'
import type { JournalFieldSource } from './journals'
import type { PageColumnSpec } from './profile'

export interface JournalAiContext {
  label: string
  min_font_pt?: number
  max_font_pt?: number
  fonts?: string[]
  min_line_pt?: number
  widths_mm?: number[]
  min_dpi?: number
}

type Binding = { journalRef?: { label: string }; journalSources?: Record<string, JournalFieldSource> } | null | undefined

const live = (s: JournalFieldSource | undefined) => (s && !s.unset && s.affects === 'preflight' ? s : undefined)

function journalWidths(src: Record<string, JournalFieldSource>): number[] {
  const cols = live(src.page_columns)?.value
  if (Array.isArray(cols)) {
    return (cols as PageColumnSpec[])
      .filter((c) => (c.qualifier ?? 'exact') === 'exact' && typeof c.width_mm === 'number' && c.width_mm > 0)
      .map((c) => c.width_mm)
  }
  return ['widths_mm.single', 'widths_mm.double']
    .map((k) => live(src[k])?.value)
    .filter((v): v is number => typeof v === 'number' && Number.isFinite(v) && v > 0)
}

export function journalAiContext(binding: Binding): JournalAiContext | null {
  if (!binding?.journalRef || !binding.journalSources) return null
  const t = journalStyleTargets(binding)
  const src = binding.journalSources
  const out: JournalAiContext = { label: binding.journalRef.label }
  if (t?.minPt != null) out.min_font_pt = t.minPt
  if (t?.maxPt != null) out.max_font_pt = t.maxPt
  if (t?.latin?.length) out.fonts = t.latin.slice(0, 12)
  if (t?.minLinePt != null) out.min_line_pt = t.minLinePt
  const widths = journalWidths(src).slice(0, 12)
  if (widths.length) out.widths_mm = widths
  const dpi = live(src.min_raster_dpi)?.value
  if (typeof dpi === 'number' && Number.isFinite(dpi) && dpi > 0) out.min_dpi = dpi
  return Object.keys(out).length > 1 ? out : null
}
