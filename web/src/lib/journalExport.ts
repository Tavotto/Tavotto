/**
 * 按期刊检查的项目，导出对话框的**初值**跟着期刊走（用户 2026-11-11 拍板）：
 *
 * - 分辨率取期刊**最高一档**（`info.dpi_by_kind` 各图类与 `min_raster_dpi` 里最大的那个）：软件分不出这张图是
 *   线条图还是照片，取最高档哪类都满足；**只升不降**——用户自己的默认值更高就用他的。
 * - 格式：期刊列了接受的格式（`info.formats_raw`）时，默认勾着、但期刊没列出的那一种换成期刊列出的同类格式
 *   （位图换位图、矢量换矢量；PDF + PNG → PDF + TIFF）。没列出的格式照样能勾，气泡里说期刊没列出它。
 *
 * 只读文档绑定里存的精简出处（`DocumentProfile.journalSources`，ADR 0155 §4），不查目录：已定稿的项目离线也一样。
 * 只是初值，判据不在这里——预检照旧只走 `preflight.runSpec` 那一条链。
 */
import type { JournalFieldSource } from './journals'
import { PPI_MAX, RASTER_FORMATS, type ExportFormat } from './exportRequest'

export interface JournalExportAdvice {
  /** 期刊名（气泡里说「X 未列出 PNG」） */
  journal: string
  /** 期刊里最高的分辨率要求（已夹到导出上限）；期刊没写 = null */
  dpi: number | null
  /** 分辨率的出处，给气泡：按图类 `[[图类, dpi], …]`；只有一个下限时是 `[['', dpi]]` */
  dpiParts: [string, number][]
  /** 期刊列出的、本应用导得出的格式；期刊没列格式（或列的一种都导不出）= null，格式不跟 */
  listed: ReadonlySet<ExportFormat> | null
}

/** 期刊原文里的格式名 → 导出格式。JPEG / DOC 之类本应用不导，不进表 */
const FORMAT_NAMES: Record<string, ExportFormat> = {
  PDF: 'pdf',
  EPS: 'eps',
  TIF: 'tiff',
  TIFF: 'tiff',
  PNG: 'png',
  SVG: 'svg',
}

const FORMAT_VALUES = Object.values(FORMAT_NAMES)

function formatOf(name: string): ExportFormat | null {
  // 「eps（可编辑原始矢量图）」「TIFF files」：取开头那个词
  const head = /^[A-Za-z]+/.exec(name.trim())?.[0]?.toUpperCase()
  return head ? (FORMAT_NAMES[head] ?? null) : null
}

const live = (s: JournalFieldSource | undefined) => (s && !s.unset ? s : undefined)

export function journalExportAdvice(
  binding: { journalRef?: { label: string }; journalSources?: Record<string, JournalFieldSource> } | null | undefined,
): JournalExportAdvice | null {
  if (!binding?.journalRef || !binding.journalSources) return null
  const src = binding.journalSources

  const dpiParts: [string, number][] = []
  const byKind = live(src['info.dpi_by_kind'])?.value
  if (byKind && typeof byKind === 'object' && !Array.isArray(byKind)) {
    for (const [kind, v] of Object.entries(byKind as Record<string, unknown>)) {
      if (typeof v === 'number' && Number.isFinite(v) && v > 0) dpiParts.push([kind, v])
    }
  }
  const floor = live(src.min_raster_dpi)?.value
  if (!dpiParts.length && typeof floor === 'number' && Number.isFinite(floor) && floor > 0) dpiParts.push(['', floor])
  const top = Math.max(0, ...dpiParts.map(([, v]) => v), typeof floor === 'number' ? floor : 0)
  const dpi = top > 0 ? Math.min(top, PPI_MAX) : null

  let listed: Set<ExportFormat> | null = null
  const raw = live(src['info.formats_raw'])?.value as { preferred?: unknown; accepted?: unknown } | undefined
  if (raw && typeof raw === 'object') {
    const names = [raw.preferred, raw.accepted].flatMap((x) => (Array.isArray(x) ? x.map(String) : []))
    const set = new Set(names.map(formatOf).filter((f): f is ExportFormat => f !== null))
    if (set.size) listed = set
  }

  if (dpi === null && listed === null) return null
  return { journal: binding.journalRef.label, dpi, dpiParts, listed }
}

/** 替换候选：同类里期刊列出的，按这个顺序挑；EPS 不当候选（它能不能导要看这张图有没有脚本） */
const RASTER_ORDER: ExportFormat[] = ['tiff', 'png']
const VECTOR_ORDER: ExportFormat[] = ['pdf', 'svg']

/**
 * 把一组初值按期刊调一遍（纯函数、幂等）。格式只换不删：同类里期刊一个都没列（或只列了 EPS）就原样留着，
 * 由气泡说明——不让一次「跟期刊」把格式清空。
 */
export function applyJournalExport(
  base: { formats: readonly string[]; dpi: string },
  advice: JournalExportAdvice | null,
): { formats: string[]; dpi: string } {
  if (!advice) return { formats: [...base.formats], dpi: base.dpi }
  const cur = Number(base.dpi)
  const dpi = advice.dpi !== null && (!Number.isFinite(cur) || advice.dpi > cur) ? String(advice.dpi) : base.dpi
  const listed = advice.listed
  if (!listed) return { formats: [...base.formats], dpi }
  const out: string[] = []
  for (const f of base.formats) {
    if (listed.has(f as ExportFormat) || !(FORMAT_VALUES as readonly string[]).includes(f)) {
      if (!out.includes(f)) out.push(f)
      continue
    }
    const order = RASTER_FORMATS.includes(f as ExportFormat) ? RASTER_ORDER : VECTOR_ORDER
    // 同类里期刊列出的那一种（已经勾着也算：PDF + TIFF + PNG → PDF + TIFF）；同类一个都没列就留着原来的
    const pick = order.find((g) => listed.has(g)) ?? f
    if (!out.includes(pick)) out.push(pick)
  }
  return { formats: out, dpi }
}
