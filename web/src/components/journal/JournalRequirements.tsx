import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { requirementRows, sourcesForCheck, valueText } from '@/lib/journalPick'
import { useDocumentStore } from '@/store/documentStore'
import { SourceButton } from './SourceButton'

const jt = (key: string, values?: Record<string, unknown>) =>
  translate(`journal.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 本项目按哪本期刊检查时，该期刊的要求清单：每个值旁一颗「出处」，期刊没写的要紧项明说「该期刊未规定」（不补）。
 * 读的是绑定里存的精简出处（`journalSources`，选期刊那一刻的样子），离线、库后来更新都不变。
 * 没按期刊选过就不渲染。
 */
export function JournalRequirements() {
  useTranslation('dialogs')
  const ref = useDocumentStore((s) => s.doc.profile?.journalRef)
  const sources = useDocumentStore((s) => s.doc.profile?.journalSources)
  const rows = useMemo(() => (sources ? requirementRows(sources) : []), [sources])
  if (!ref || !sources) return null
  return (
    <dl data-journal-requirements className="flex flex-col">
      {rows.map((r) => (
        <div
          key={r.path}
          data-journal-row={r.path}
          className="flex min-h-7 items-center gap-2 text-sm"
        >
          <dt className="w-32 shrink-0 truncate text-ink-2">{r.label}</dt>
          <dd
            data-journal-value
            className="min-w-0 flex-1 truncate tabular-nums text-ink"
            title={r.source ? valueText(r.path, r.source) : undefined}
          >
            {r.source ? valueText(r.path, r.source) : <span className="text-ink-3">{jt('unspecified')}</span>}
          </dd>
          {r.source && (
            <SourceButton
              journal={ref.label}
              // 建议性字号：正文 / 上下标两条说的是同一句话，一张卡里一起给
              entries={
                r.source.affects === 'advice'
                  ? sourcesForCheck('font-size-recommended', sources)
                  : [[r.path, r.source]]
              }
              name={r.label}
            />
          )}
        </div>
      ))}
    </dl>
  )
}
