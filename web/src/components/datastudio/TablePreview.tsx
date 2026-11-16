import { useTranslation } from 'react-i18next'
import { backendCodeMsg, type AiDataOverview } from '@/lib/api'
import { useFormatMessage } from '@/i18n/react'
import { cn } from '@/lib/utils'
import { OverlayFooter } from './OverlayFooter'

const KIND_KEY = { numeric: 'numeric', date: 'date', text: 'text', mixed: 'mixed' } as const

/**
 * 舞台里看一张表：列名 + 每列类型 + 前几行（等宽数字），开头被跳过的说明行灰显。
 * 没认准（`unsure`）时显示文件开头的原文行，把猜的那一行高亮、行尾挂「当作每列的名字」，点别的行就按那一行重读（不用确认）。
 * 读不了：一句话 + 后端给的原因。所有文字是不可信输入：只显示，已由后端截断并去掉控制字符。
 */
export function TablePreview({
  ov,
  loading,
  picked = false,
  onHeaderRow,
}: {
  ov: AiDataOverview
  /** 点了别的行、重读还在路上：旧内容淡一点，不清空 */
  loading: boolean
  /** 用户已经点过某一行当列名（这次的结果是按那一行读的） */
  picked?: boolean
  onHeaderRow: (line: number) => void
}) {
  const { t } = useTranslation('ai')
  const fm = useFormatMessage()

  const lines = ov.raw_lines
  if ((ov.status === 'unsure' || ov.status === 'unreadable') && lines.length > 0) {
    const guess = ov.header_row
    // 用户点的那一行读不成表：按后端的稳定码说一句为什么，留在这一屏接着点别的行（码不认识就不说原因）
    const why = ov.status === 'unreadable' && picked ? fm(backendCodeMsg(ov.code ?? undefined, undefined, '')) : ''
    return (
      <div className="flex flex-col gap-2">
        {why && (
          <p className="text-sm text-ink-2" data-studio-pick-why>
            {why}
          </p>
        )}
        {/* 这屏没有主按钮：底栏只放一句话，告诉用户点哪里 */}
        <OverlayFooter left={<span className="text-xs text-ink-3" data-studio-pick-hint>{t('dataFigureV2.preview.pickHint')}</span>} />
        <div role="radiogroup" aria-label={t('dataFigureV2.preview.headerPick')} data-studio-look className={cn('flex flex-col', loading && 'opacity-60')}>
          {lines.map((l) => {
            const on = l.line === guess
            return (
              <label
                key={l.line}
                data-look-line={l.line}
                data-header={on || undefined}
                className={cn(
                  'flex min-h-8 cursor-default items-center gap-3 rounded-md px-2 py-1 font-mono text-sm',
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
                  <span data-studio-header-tag className="shrink-0 text-xs text-ink-2 font-sans">
                    {t('dataFigureV2.preview.headerTag')}
                  </span>
                )}
              </label>
            )
          })}
        </div>
      </div>
    )
  }

  if (ov.status === 'unreadable') {
    // 原因句（后端的稳定码翻出来的）本身就说了「读不了」；码不认识就只说这一句
    const reason = fm(backendCodeMsg(ov.code ?? undefined, undefined, ''))
    // 一行正文字：不带底色、不带图标（用户明令不要带底色的状态块）
    return (
      <p data-studio-unreadable className="text-base text-ink-2">
        {reason || t('dataFigureV2.preview.unreadable')}
      </p>
    )
  }

  const numericCols = ov.fields.map((f) => f.kind === 'numeric')
  // 第一列贴左、最后一列贴右（表头和数值的边一致，不管是不是数值列）；中间的数值列右对齐、文字列左对齐
  const lastCol = ov.fields.length - 1
  const right = ov.fields.map((_, j) => (j === 0 ? false : j === lastCol ? true : numericCols[j]!))
  const decimals = ov.fields.map((_, j) => Math.max(0, ...ov.preview_rows.map((r) => decimalsOf(r.cells[j] ?? ''))))
  return (
    <div className={cn('flex flex-col gap-3', loading && 'opacity-60')} data-studio-preview>
      {ov.lead_lines.length > 0 && (
        <div data-studio-skipped className="flex flex-col gap-1 px-1">
          <div className="flex flex-col font-mono text-xs leading-5 text-ink-3">
            {ov.lead_lines.slice(0, 4).map((l) => (
              <span key={l.line} className="truncate whitespace-pre">
                {l.text}
              </span>
            ))}
            {ov.lead_lines.length > 4 && <span aria-hidden>…</span>}
          </div>
          <p className="text-xs text-ink-3">{t('dataFigureV2.preview.skipped', { count: ov.lead_lines.length })}</p>
        </div>
      )}
      {/* 没有套框：撑满卡片内容区，行间 hairline 从左边画到右边；第一列贴左、最后一列贴右，多出的宽度均分到列间距；
          数值列按小数点对齐（用不可见字符补小数位，不改显示的数字），表头对齐到列右边 */}
      <div
        role="table"
        className="grid w-full min-w-max text-base tabular-nums"
        style={{ gridTemplateColumns: `repeat(${ov.fields.length}, max-content)`, justifyContent: 'space-between', columnGap: 24 }}
      >
        <div role="row" className="sticky top-0 z-sticky col-span-full grid grid-cols-subgrid border-b border-border bg-surface pb-1.5 pt-1">
          {ov.fields.map((f, i) => (
            <div key={i} role="columnheader" className={cn('flex flex-col', right[i] ? 'items-end text-right' : 'items-start text-left')}>
              <span className="max-w-56 truncate font-medium">{f.name}</span>
              <span className="text-xs text-ink-3">
                {f.id_like ? t('dataFigureV2.preview.type.id') : t(`dataFigureV2.preview.type.${KIND_KEY[f.kind] ?? 'text'}`)}
              </span>
            </div>
          ))}
        </div>
        {ov.preview_rows.map((r) => (
          <div key={r.line} role="row" className="col-span-full grid grid-cols-subgrid border-t border-border py-1.5 first:border-t-0">
            {ov.fields.map((_, j) => (
              <div key={j} role="cell" className={cn('max-w-56 truncate', right[j] ? 'text-right' : 'text-left')}>
                {numericCols[j] ? alignDecimal(r.cells[j] ?? '', decimals[j]) : (r.cells[j] ?? '')}
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
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
