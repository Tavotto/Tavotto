import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { AiDataOverview } from '@/lib/api'
import { cn } from '@/lib/utils'
import { TablePreview } from './TablePreview'

/** 数据条：占内容区全宽、贴在候选下面。默认露表头 + 3 行，可展开（20 行）/ 收起；原文行视图（没认准 / 要点表头行）自动展开 */
export function DataStrip({
  ov,
  guessRow,
  loading,
  forceOpen,
  onHeaderRow,
}: {
  ov: AiDataOverview | null
  /** 这次出候选实际用的表头行（响应的 `header_row`）；没有就用概览里猜的那一行 */
  guessRow?: number | null
  loading: boolean
  /** 没认准的、全是文字的：数据本身就是要看的东西，自动展开 */
  forceOpen: boolean
  onHeaderRow: (line: number) => void
}) {
  const { t } = useTranslation('ai')
  const [open, setOpen] = useState(false)
  const expanded = open || forceOpen
  const raw = ov != null && (ov.status === 'unsure' || ov.status === 'unreadable') && ov.raw_lines.length > 0
  const rows = expanded ? 20 : 3
  const shown = ov ? Math.min(rows, ov.preview_rows.length) : 0
  let info: string | null = null
  if (ov && raw) {
    const n = guessRow ?? ov.header_row
    info = n != null ? t('dataFigureV2.draft.guessRow', { n }) : t('dataFigureV2.preview.pickHint')
  } else if (ov?.status === 'ok' && ov.rows != null) {
    info = t('dataFigureV2.draft.stripInfo', { shown, total: ov.rows, columns: ov.columns ?? ov.fields.length })
  }
  return (
    <section
      data-data-strip
      data-open={expanded || undefined}
      className={cn('flex shrink-0 flex-col border-t border-border transition-[max-height] duration-base ease-standard', expanded ? 'max-h-[52%]' : 'max-h-[158px]')}
    >
      <div className="flex h-9 shrink-0 items-center gap-2.5 pl-7 pr-4 text-xs text-ink-2">
        <b className="text-sm font-medium text-ink">{t('dataFigureV2.list.heading')}</b>
        {info && <span data-data-strip-info className="min-w-0 truncate tabular-nums">{info}</span>}
        <span className="flex-1" />
        {!forceOpen && ov && !raw && (
          <button
            type="button"
            data-data-strip-toggle
            aria-expanded={expanded}
            onClick={() => setOpen(!open)}
            className="h-6 rounded-full px-2.5 text-ink-2 outline-none hover:bg-surface-hover hover:text-ink focus-visible:focus-ring"
          >
            {t(expanded ? 'dataFigureV2.draft.collapse' : 'dataFigureV2.draft.expand')}
          </button>
        )}
      </div>
      <div className={cn('min-h-0 flex-1 overflow-auto px-7 pb-3', loading && 'opacity-60')}>
        {ov ? (
          <TablePreview ov={ov} rows={rows} guessRow={guessRow ?? null} onHeaderRow={onHeaderRow} />
        ) : (
          <div role="status" aria-label={t('dataFigureV2.working.reading')} className="flex flex-col gap-3 pt-2">
            {[62, 48, 55].map((w, i) => (
              <span key={i} aria-hidden className="block h-4 rounded-sm bg-surface-active" style={{ width: `${w}%` }} />
            ))}
          </div>
        )}
      </div>
    </section>
  )
}
