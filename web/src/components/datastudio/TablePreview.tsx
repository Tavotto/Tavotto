import { useTranslation } from 'react-i18next'
import { Card } from '@/components/ui/Card'
import { Notice } from '@/components/ui/Notice'
import { backendCodeMsg, type AiDataOverview } from '@/lib/api'
import { useFormatMessage } from '@/i18n/react'
import { cn } from '@/lib/utils'

const KIND_KEY = { numeric: 'numeric', date: 'date', text: 'text', mixed: 'mixed' } as const

/**
 * 舞台里看一张表：列名 + 每列类型 + 前几行（等宽数字），开头被跳过的说明行灰显。
 * 没认准（`unsure`）时显示文件开头的原文行，把猜的那一行高亮、行尾挂「当作每列的名字」，点别的行就按那一行重读（不用确认）。
 * 读不了：一句话 + 后端给的原因。所有文字是不可信输入：只显示，已由后端截断并去掉控制字符。
 */
export function TablePreview({
  ov,
  loading,
  onHeaderRow,
}: {
  ov: AiDataOverview
  /** 点了别的行、重读还在路上：旧内容淡一点，不清空 */
  loading: boolean
  onHeaderRow: (line: number) => void
}) {
  const { t } = useTranslation('ai')
  const fm = useFormatMessage()

  if (ov.status === 'unreadable') {
    // 原因句（后端的稳定码翻出来的）本身就说了「读不了」；码不认识就只说这一句
    const reason = fm(backendCodeMsg(ov.code ?? undefined, undefined, ''))
    return <Notice tone="neutral" data-studio-unreadable title={reason || t('dataFigureV2.preview.unreadable')} />
  }

  const lines = ov.raw_lines
  if (ov.status === 'unsure' && lines.length > 0) {
    const guess = ov.header_row
    return (
      <div role="radiogroup" aria-label={t('dataFigureV2.preview.headerPick')} data-studio-look className={cn('flex w-full max-w-[720px] flex-col gap-0.5 rounded-lg bg-surface-2 p-1.5', loading && 'opacity-60')}>
        {lines.map((l) => {
          const on = l.line === guess
          return (
            <label
              key={l.line}
              data-look-line={l.line}
              data-header={on || undefined}
              className={cn(
                'flex min-h-8 cursor-default items-center gap-3 rounded-md px-2 py-1.5 font-mono text-sm',
                on ? 'bg-selected text-ink' : guess != null && l.line < guess ? 'text-ink-3' : 'text-ink-2 hover:bg-surface-hover',
              )}
            >
              <input
                type="radio"
                name="studio-header-row"
                className="sr-only"
                checked={on}
                // onClick 而不是 onChange：点已经选中的那一行（认可猜的）也算一次确认；方向键换行会派发 click
                onClick={() => onHeaderRow(l.line)}
                onChange={() => undefined}
                aria-label={t('dataFigureV2.preview.lineN', { n: l.line })}
              />
              <span className="w-6 shrink-0 text-right text-ink-3 tabular-nums">{l.line}</span>
              <span className="min-w-0 flex-1 truncate whitespace-pre">{l.text.replace(/\t/g, '    ')}</span>
              {on && (
                <span data-studio-header-tag className="shrink-0 rounded-full bg-surface px-2 py-0.5 font-sans text-xs text-ink">
                  {t('dataFigureV2.preview.headerTag')}
                </span>
              )}
            </label>
          )
        })}
      </div>
    )
  }

  const numericCols = ov.fields.map((f) => f.kind === 'numeric')
  return (
    <div className={cn('flex flex-col gap-3', loading && 'opacity-60')} data-studio-preview>
      {ov.lead_lines.length > 0 && (
        <div className="w-full max-w-[720px] rounded-md bg-surface-2 px-4 py-2" data-studio-skipped>
          <div className="flex flex-col font-mono text-xs leading-5 text-ink-3">
            {ov.lead_lines.slice(0, 4).map((l) => (
              <span key={l.line} className="truncate whitespace-pre">
                {l.text}
              </span>
            ))}
            {ov.lead_lines.length > 4 && <span aria-hidden>…</span>}
          </div>
          <p className="mt-1 text-sm text-ink-2">{t('dataFigureV2.preview.skipped', { count: ov.lead_lines.length })}</p>
        </div>
      )}
      <Card padding="none" className="w-full max-w-[720px] overflow-x-auto">
        <table className="w-full border-collapse text-base tabular-nums">
          <thead>
            <tr>
              {ov.fields.map((f, i) => (
                <th key={i} scope="col" className="min-w-24 px-4 pb-1.5 pt-2.5 font-medium">
                  <div className={cn('flex flex-col', numericCols[i] ? 'items-end text-right' : 'items-start text-left')}>
                    <span className="max-w-56 truncate">{f.name}</span>
                    <span className="text-xs font-normal text-ink-3">
                      {f.id_like ? t('dataFigureV2.preview.type.id') : t(`dataFigureV2.preview.type.${KIND_KEY[f.kind] ?? 'text'}`)}
                    </span>
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {ov.preview_rows.map((r) => (
              <tr key={r.line} className="border-t border-border">
                {ov.fields.map((_, j) => (
                  <td key={j} className={cn('max-w-56 truncate px-4 py-1.5', numericCols[j] ? 'text-right' : 'text-left')}>
                    {r.cells[j] ?? ''}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      {ov.truncated && ov.rows != null && (
        <p className="text-xs text-ink-3">{t('dataFigureV2.preview.shown', { shown: ov.preview_rows.length, total: ov.rows })}</p>
      )}
    </div>
  )
}
