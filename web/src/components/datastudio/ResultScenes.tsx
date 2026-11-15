import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/Button'
import { ProgressBar } from '@/components/ui/ProgressBar'
import { canRevealInFileManager, revealProjectFile } from '@/lib/desktop'
import { feedbackAction } from '@/lib/canvasFileIntake'
import { KNOWN_NOTHING_REASONS } from '@/lib/dataFigure'
import { useFormatMessage } from '@/i18n/react'
import { useProjectStore } from '@/store/projectStore'
import type { DrawNotes, ProblemKind } from '@/store/dataFigureStore'
import type { ReactNode } from 'react'

/** 一屏一句话 + 按钮：舞台里居中（抉择 / 说明内容居中） */
function Centered({ children, anchor }: { children: ReactNode; anchor: string }) {
  return (
    <div {...{ [anchor]: '' }} className="mx-auto flex w-full max-w-[480px] flex-1 flex-col items-center justify-center gap-5 pb-12 text-center">
      {children}
    </div>
  )
}

/** 画备选 / 画这张：等后端一次回来，没有可报的步骤——不定态，不装百分比 */
export function WorkingScene({ kind }: { kind: 'proposing' | 'drawing' }) {
  const { t } = useTranslation('ai')
  return (
    <Centered anchor="data-studio-working">
      <p className="type-heading" role="status">
        {t(kind === 'drawing' ? 'dataFigureV2.working.drawing' : 'dataFigureV2.working.proposing')}
      </p>
      <ProgressBar pct={null} label={t(kind === 'drawing' ? 'dataFigureV2.working.drawing' : 'dataFigureV2.working.proposing')} className="w-56" />
    </Centered>
  )
}

/** 一张表画不出：一句人话（按后端给的原因码）+ 下一步（换一张表） */
export function NothingScene({ reasons, onBack }: { reasons: string[]; onBack: () => void }) {
  const { t } = useTranslation('ai')
  const first = reasons.find((r) => KNOWN_NOTHING_REASONS.includes(r))
  return (
    <Centered anchor="data-studio-nothing">
      <p className="type-heading">{first ? t(`dataFigureV2.reason.${first}`) : t('dataFigureV2.nothing.generic')}</p>
      <Button variant="primary" size="lg" data-studio-back onClick={onBack}>
        {t('dataFigureV2.nothing.another')}
      </Button>
    </Centered>
  )
}

/** 图画好了，但有话要说：哪几行没画出来 / 数据没能逐项核对（不写成已核对）。说完点「打开这张图」 */
export function DrawnScene({ notes, onOpen }: { notes: DrawNotes; onOpen: () => void }) {
  const { t } = useTranslation('ai')
  const shown = notes.unplotted ? notes.unplotted.rows.slice(0, 8) : []
  return (
    <Centered anchor="data-studio-drawn">
      <div className="flex flex-col gap-2">
        {notes.unplotted && (
          <p className="type-heading" data-studio-unplotted>
            {t('dataFigureV2.reason.rows_not_plotted', { count: notes.unplotted.count })}
            {shown.length > 0 && (
              <span className="mt-1 block text-base font-normal text-ink-2">
                {t('dataFigureV2.drawn.rows', { rows: shown.join('、') })}
                {notes.unplotted.rows.length > shown.length ? t('dataFigureV2.drawn.andMore') : ''}
              </span>
            )}
          </p>
        )}
        {notes.check && (
          <p className={notes.unplotted ? 'text-base text-ink-2' : 'type-heading'} data-studio-unchecked>
            {t(`dataFigureV2.result.${notes.check}`)}
          </p>
        )}
      </div>
      <Button variant="primary" size="lg" data-studio-primary onClick={onOpen}>
        {t('dataFigureV2.drawn.open')}
      </Button>
    </Centered>
  )
}

/**
 * 没成的结局只有三种：一句话 + 一个能继续原动作的主按钮（再试一次 / 换个画法 / 换个文件夹），
 * 真做不了才「发送问题反馈」。这里每一种都至少给一个出口，不会只报错卡住。
 */
export function ProblemScene({
  problem,
  onRetry,
  onBack,
  onChooseFolder,
}: {
  problem: ProblemKind
  onRetry: () => void
  onBack: () => void
  onChooseFolder: () => void
}) {
  const { t } = useTranslation('ai')
  const fm = useFormatMessage()
  const root = useProjectStore((s) => s.project?.figures_dir)
  const feedback = feedbackAction()
  if (problem.kind === 'mismatch') {
    const reveal = root && canRevealInFileManager() ? () => void revealProjectFile(root, problem.script) : null
    return (
      <Centered anchor="data-studio-problem">
        <p className="type-heading">{t('dataFigureV2.result.mismatch')}</p>
        <div className="flex flex-wrap justify-center gap-2">
          <Button variant="primary" size="lg" data-studio-primary onClick={onBack}>
            {t('dataFigureV2.problem.otherWay')}
          </Button>
          {reveal && (
            <Button variant="secondary" size="lg" data-studio-reveal onClick={reveal}>
              {t('dataFigureV2.problem.reveal')}
            </Button>
          )}
          {feedback && (
            <Button variant="ghost" size="lg" onClick={feedback.run}>
              {fm(feedback.label)}
            </Button>
          )}
        </div>
      </Centered>
    )
  }
  if (problem.kind === 'unwritable') {
    return (
      <Centered anchor="data-studio-problem">
        <p className="type-heading">{t('dataFigureV2.problem.unwritable')}</p>
        <div className="flex flex-wrap justify-center gap-2">
          <Button variant="primary" size="lg" data-studio-primary onClick={onChooseFolder}>
            {t('dataFigureV2.problem.otherFolder')}
          </Button>
          <Button variant="secondary" size="lg" data-studio-retry onClick={onRetry}>
            {t('dataFigureV2.problem.retry')}
          </Button>
        </div>
      </Centered>
    )
  }
  return (
    <Centered anchor="data-studio-problem">
      <p className="type-heading">{problem.kind === 'commit' ? t('dataFigureV2.problem.notDrawn') : t('dataFigureV2.problem.proposeFailed')}</p>
      {problem.error && <p className="text-base text-ink-2">{fm(problem.error)}</p>}
      <div className="flex flex-wrap justify-center gap-2">
        <Button variant="primary" size="lg" data-studio-primary data-studio-retry onClick={onRetry}>
          {t('dataFigureV2.problem.retry')}
        </Button>
        <Button variant="secondary" size="lg" data-studio-back onClick={onBack}>
          {t('dataFigureV2.nothing.another')}
        </Button>
      </div>
    </Centered>
  )
}
