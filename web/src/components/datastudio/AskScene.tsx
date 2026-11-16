import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/Button'
import type { AiDataOption } from '@/lib/api'
import { usableOptions } from '@/lib/dataFigure'
import { cn } from '@/lib/utils'
import { OverlayFooter } from './OverlayFooter'

const KNOWN_QUESTIONS = ['pick_table', 'pick_value', 'pick_group', 'pick_x', 'pick_design', 'pick_unit']
const KNOWN_OPTIONS = ['table', 'table_sheet', 'column', 'unsure', 'design_independent', 'design_repeated']

/**
 * 选择题：决定科学含义的事（用哪张表 / 哪列是数值 / 按什么分组 ……）只能由用户定。整屏只有这一道题，抉择内容居中；
 * 点一个选项就是回答（回答并进下一次出备选）。「不确定」永远有安全的默认，所以任何回答都能继续。
 * 题目与选项的文字来自后端给的码（`question_code` / `option.code` + `params`），未知的码走一句通用的话。
 */
export function AskScene({
  code,
  options,
  unsure,
  sampleOf,
  onAnswer,
  onBack,
}: {
  code: string
  options: AiDataOption[]
  unsure: AiDataOption | null
  /** 这一列前几个值的样例（灰字，帮不认识列名的人判断）；没有就不放 */
  sampleOf?: (column: string) => string | null
  onAnswer: (o: AiDataOption) => void
  onBack: () => void
}) {
  const { t } = useTranslation('ai')
  const label = (o: AiDataOption): string => {
    if (!KNOWN_OPTIONS.includes(o.code)) return typeof o.params?.column === 'string' ? o.params.column : t('dataFigureV2.option.other')
    return t(`dataFigureV2.option.${o.code}`, { ...o.params })
  }
  const choices = usableOptions(options)
  // 「不确定」是列表最后一行，和别的选项同一个样式：永远有安全的默认，所以任何回答都能继续
  const rows = unsure ? [...choices, unsure] : choices
  return (
    <div data-studio-ask className="flex flex-col gap-3">
      <h2 className="text-base font-medium">{KNOWN_QUESTIONS.includes(code) ? t(`dataFigureV2.question.${code}`) : t('dataFigureV2.question.other')}</h2>
      <div className="flex flex-col">
        {rows.map((o, i) => {
          const sample = o.code === 'column' && typeof o.params?.column === 'string' ? sampleOf?.(o.params.column) : null
          const isUnsure = unsure != null && o === unsure
          return (
            <button
              key={i}
              type="button"
              data-studio-choice
              data-studio-unsure={isUnsure || undefined}
              onClick={() => onAnswer(o)}
              className="flex w-full items-baseline gap-3 rounded-md px-3 py-2.5 text-left text-base outline-none transition-colors hover:bg-surface-hover active:bg-surface-active focus-visible:focus-ring-inset"
            >
              <span className={cn('min-w-0 flex-1 truncate', isUnsure && 'text-ink-2')}>{label(o)}</span>
              {sample && <span className="max-w-[45%] shrink-0 truncate font-mono text-xs text-ink-3 tabular-nums">{sample}</span>}
            </button>
          )
        })}
      </div>
      <OverlayFooter
        left={
          <Button variant="ghost" size="lg" data-studio-back onClick={onBack}>
            {t('dataFigureV2.back')}
          </Button>
        }
      />
    </div>
  )
}
