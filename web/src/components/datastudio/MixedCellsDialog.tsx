import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/Button'
import { Dialog } from '@/components/ui/Dialog'
import { Radio } from '@/components/ui/Radio'
import type { AiDataMixedColumn, AiDataOption } from '@/lib/api'
import { cellModeOf, type CellMode } from '@/lib/dataFigure'
import { cn } from '@/lib/utils'

/**
 * 弹窗：数值列里混着少量文字。后端一次把这次画图用到的所有这样的列都问完（每列一块）；先预览是哪几行，
 * 每列二选一：「不画这 N 行」（默认，最保守：什么都不改）/「只用里面的数字」。右列「画图时用」跟着选项变。
 * 「我来说明怎么处理」要先由模型整理成规则，这版没有，所以不出现。左下角一句：原文件不会改。
 */
export function MixedCellsDialog({
  columns,
  onContinue,
  onCancel,
}: {
  columns: AiDataMixedColumn[]
  onContinue: (picks: { column: string; option: AiDataOption }[]) => void
  onCancel: () => void
}) {
  const { t } = useTranslation('ai')
  const [modes, setModes] = useState<Record<string, CellMode>>({})
  const modeOf = (c: AiDataMixedColumn): CellMode => modes[c.column] ?? 'skip'
  const optionFor = (c: AiDataMixedColumn, m: CellMode): AiDataOption | undefined => c.options.find((o) => !o.free_text && cellModeOf(o) === m)
  const canContinue = columns.every((c) => optionFor(c, modeOf(c)))
  return (
    <Dialog
      open
      onOpenChange={(v) => !v && onCancel()}
      size="md"
      anchor="data-studio-mixed"
      title={t('dataFigureV2.mixed.title')}
      onEscape={onCancel}
      footer={{
        start: <span className="text-sm text-ink-3">{t('dataFigureV2.mixed.note')}</span>,
        secondary: (
          <Button variant="secondary" size="lg" onClick={onCancel}>
            {t('dataFigureV2.mixed.cancel')}
          </Button>
        ),
        primary: (
          <Button
            variant="primary"
            size="lg"
            data-studio-mixed-continue
            disabled={!canContinue}
            onClick={() => {
              const picks = columns.flatMap((c) => {
                const o = optionFor(c, modeOf(c))
                return o ? [{ column: c.column, option: o }] : []
              })
              onContinue(picks)
            }}
          >
            {t('dataFigureV2.mixed.continue')}
          </Button>
        ),
      }}
    >
      <div className="flex flex-col gap-6">
        {columns.map((c, ci) => {
          const mode = modeOf(c)
          const hasOthers = c.preview.some((p) => Object.keys(p.others ?? {}).length > 0)
          const gridCols = hasOthers ? 'grid-cols-[3rem_1fr_1fr_1fr]' : 'grid-cols-[3rem_1fr_1fr]'
          return (
            <section key={c.column} data-studio-mixed-column={ci} className="flex flex-col gap-3">
              <h3 className="text-base font-medium">{t('dataFigureV2.question.mixed_cells', { column: c.column, count: c.non_numeric, rows: c.rows })}</h3>
              <div role="table" aria-label={c.column} className="overflow-hidden rounded-md bg-surface-2">
                <div role="row" className={cn('grid gap-2 px-3 py-1.5 text-xs text-ink-3', gridCols)}>
                  <span role="columnheader">{t('dataFigureV2.mixed.colRow')}</span>
                  {hasOthers && <span role="columnheader">{t('dataFigureV2.mixed.colOthers')}</span>}
                  <span role="columnheader">{t('dataFigureV2.mixed.colRaw')}</span>
                  <span role="columnheader">{t('dataFigureV2.mixed.colUse')}</span>
                </div>
                {c.preview.map((p) => {
                  const use = mode === 'skip' ? null : p.extract
                  return (
                    <div key={p.row} role="row" data-dirty-row className={cn('grid gap-2 border-t border-border px-3 py-1.5 text-base tabular-nums', gridCols)}>
                      <span role="cell" className="text-ink-3">
                        {p.row}
                      </span>
                      {hasOthers && (
                        <span role="cell" className="truncate">
                          {Object.values(p.others ?? {}).join(' · ')}
                        </span>
                      )}
                      <span role="cell" className="truncate font-mono">
                        {p.text}
                      </span>
                      <span role="cell" data-dirty-use className={cn(use == null && 'text-ink-3')}>
                        {use ?? t('dataFigureV2.mixed.none')}
                      </span>
                    </div>
                  )
                })}
              </div>
              {c.more > 0 && <p className="-mt-1 text-xs text-ink-3">{t('dataFigureV2.mixedCells.more', { count: c.more })}</p>}
              <div role="radiogroup" aria-label={c.column} className="flex flex-col gap-2.5">
                {(['skip', 'extract'] as const).map((m) => (
                  <label key={m} className={cn('flex items-center gap-2 text-base', !optionFor(c, m) && 'opacity-40')}>
                    <Radio
                      name={`mixed-${ci}`}
                      checked={mode === m}
                      disabled={!optionFor(c, m)}
                      onChange={() => setModes((s) => ({ ...s, [c.column]: m }))}
                    />
                    {t(`dataFigureV2.option.${m}`, { count: c.non_numeric })}
                  </label>
                ))}
              </div>
            </section>
          )
        })}
      </div>
    </Dialog>
  )
}
