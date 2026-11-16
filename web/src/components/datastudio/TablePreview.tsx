import { useTranslation } from 'react-i18next'
import type { AiDataOverview } from '@/lib/api'
import { cn } from '@/lib/utils'

const KIND_KEY = { numeric: 'numeric', date: 'date', text: 'text', mixed: 'mixed' } as const

/**
 * 数据条里的表：列名 + 每列类型 + 前几行，按内容自然宽度靠左（列多了横向滚），数值列右对齐、文字列左对齐，表头吸顶，行间 hairline。
 * 没认准（`unsure`，或读不成表但有原文行）时显示文件开头的原文行：猜的那一行有框、行尾「列名」，点别的行就按那一行重读；
 * 猜的那行之前的说明行灰显。所有文字是不可信输入：只显示，已由后端截断并去掉控制字符。
 */
export function TablePreview({
  ov,
  rows,
  onHeaderRow,
}: {
  ov: AiDataOverview
  /** 最多显示几行（数据条收起 3 行、展开 20 行） */
  rows: number
  onHeaderRow: (line: number) => void
}) {
  const { t } = useTranslation('ai')
  const lines = ov.raw_lines
  if ((ov.status === 'unsure' || ov.status === 'unreadable') && lines.length > 0) {
    const guess = ov.header_row
    return (
      <div data-header className="flex w-full flex-col font-mono text-xs" role="radiogroup" aria-label={t('dataFigureV2.preview.headerPick')}>
        {lines.slice(0, Math.max(rows, 8)).map((l) => {
          const on = l.line === guess
          return (
            <label
              key={l.line}
              data-look-line={l.line}
              data-checked={on || undefined}
              className={cn(
                'flex h-7 cursor-default items-center gap-3 rounded-md px-2 outline-none hover:bg-surface-hover has-[:focus-visible]:focus-ring-inset',
                on && 'outline outline-2 -outline-offset-2 outline-ink',
                guess != null && l.line < guess && 'text-ink-3',
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
              {on && <span className="shrink-0 font-sans text-ink-2">{t('dataFigureV2.preview.headerTag')}</span>}
            </label>
          )
        })}
      </div>
    )
  }
  if (ov.status !== 'ok') return null

  const numericCols = ov.fields.map((f) => f.kind === 'numeric')
  const decimals = ov.fields.map((_, j) => Math.max(0, ...ov.preview_rows.map((r) => decimalsOf(r.cells[j] ?? ''))))
  return (
    <table data-studio-preview className="border-collapse text-sm tabular-nums">
      <thead>
        <tr>
          {ov.fields.map((f, i) => (
            <th key={i} scope="col" className="sticky top-0 z-sticky h-[38px] bg-surface pb-1 pr-7 text-left align-bottom font-medium last:pr-0">
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
        {ov.preview_rows.slice(0, rows).map((r) => (
          <tr key={r.line} className="border-t border-border">
            {ov.fields.map((_, j) => (
              <td key={j} className={cn('h-7 max-w-56 truncate pr-7 last:pr-0', numericCols[j] ? 'text-right' : 'text-left')}>
                {numericCols[j] ? alignDecimal(r.cells[j] ?? '', decimals[j]!) : (r.cells[j] ?? '')}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

const PLAIN_NUMBER = /^[-+]?\d+(\.\d+)?$/
const decimalsOf = (v: string) => (PLAIN_NUMBER.test(v) ? (v.split('.')[1]?.length ?? 0) : 0)

/** 同一列按最多的小数位补齐宽度：补的是不可见占位，不改显示的数字；不是普通小数的格子原样显示 */
function alignDecimal(v: string, max: number) {
  if (max === 0 || !PLAIN_NUMBER.test(v)) return v
  const d = decimalsOf(v)
  if (d === max) return v
  const pad = (d === 0 ? '.' : '') + '0'.repeat(max - d)
  return (
    <>
      {v}
      <span aria-hidden className="invisible select-none">
        {pad}
      </span>
    </>
  )
}
