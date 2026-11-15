import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import type { AiDataOption } from '@/lib/api'
import { usableOptions } from '@/lib/dataFigure'
import { cn } from '@/lib/utils'

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
  onAnswer,
  onBack,
}: {
  code: string
  options: AiDataOption[]
  unsure: AiDataOption | null
  onAnswer: (o: AiDataOption) => void
  onBack: () => void
}) {
  const { t } = useTranslation('ai')
  const label = (o: AiDataOption): string => {
    if (!KNOWN_OPTIONS.includes(o.code)) return typeof o.params?.column === 'string' ? o.params.column : t('dataFigureV2.option.other')
    return t(`dataFigureV2.option.${o.code}`, { ...o.params })
  }
  const choices = usableOptions(options)
  return (
    <div data-studio-ask className="mx-auto flex w-full max-w-[480px] flex-1 flex-col justify-center gap-5 pb-12">
      <h2 className="type-heading text-center">
        {KNOWN_QUESTIONS.includes(code) ? t(`dataFigureV2.question.${code}`) : t('dataFigureV2.question.other')}
      </h2>
      <div className="flex flex-col gap-2">
        {choices.map((o, i) => (
          <Card key={i} interactive padding="none">
            <button
              type="button"
              data-studio-choice
              onClick={() => onAnswer(o)}
              className="flex w-full flex-col items-start gap-0.5 rounded-lg px-4 py-3 text-left outline-none focus-visible:focus-ring"
            >
              <span className="min-w-0 max-w-full truncate text-base">{label(o)}</span>
              {o.code === 'column' && typeof o.params?.kind === 'string' && (
                <span className="text-xs text-ink-3">{t(`dataFigureV2.preview.type.${o.params.kind === 'numeric' || o.params.kind === 'date' || o.params.kind === 'mixed' ? o.params.kind : 'text'}`)}</span>
              )}
            </button>
          </Card>
        ))}
      </div>
      <div className={cn('flex justify-center gap-2')}>
        {unsure && (
          <Button variant="secondary" size="lg" data-studio-unsure onClick={() => onAnswer(unsure)}>
            {t('dataFigureV2.option.unsure')}
          </Button>
        )}
        <Button variant="ghost" size="lg" data-studio-back onClick={onBack}>
          {t('dataFigureV2.back')}
        </Button>
      </div>
    </div>
  )
}
