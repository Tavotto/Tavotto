import { useEffect, useState } from 'react'
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
import { FigureThumb } from './FigureThumb'
import { OverlayFooter } from './OverlayFooter'

/** 一屏一句话 + 按钮：正文字号、左对齐、放在内容区顶部；动作在底栏（主按钮右、次要左） */
function Top({ children, anchor }: { children: ReactNode; anchor: string }) {
  return (
    <div {...{ [anchor]: '' }} className="flex w-full flex-col gap-2 text-left">
      {children}
    </div>
  )
}

/** 出图超过这么久还没回来：换一句话，让人知道没卡住 */
const SLOW_MS = 8000

/**
 * 画备选 / 画这张：等后端一次回来，没有可报的步骤——不定态，不装百分比。
 * 出图时留着刚选中的那张缩略图（带一道扫光），下面一行字；超过 8 秒换成「还在画」。
 */
export function WorkingScene({ kind, thumb }: { kind: 'proposing' | 'drawing'; thumb?: string | null }) {
  const { t } = useTranslation('ai')
  const [slow, setSlow] = useState(false)
  useEffect(() => {
    setSlow(false)
    if (kind !== 'drawing') return
    const id = window.setTimeout(() => setSlow(true), SLOW_MS)
    return () => window.clearTimeout(id)
  }, [kind])
  if (kind === 'drawing')
    return (
      <Top anchor="data-studio-working">
        {thumb && (
          <div className="relative mx-auto w-full max-w-72 overflow-hidden rounded-lg" data-studio-working-thumb>
            <FigureThumb src={thumb} alt="" />
            <span aria-hidden className="pointer-events-none absolute inset-y-0 left-0 w-1/3 animate-sweep bg-linear-to-r from-transparent via-paper/70 to-transparent" />
          </div>
        )}
        <p className={thumb ? 'text-center text-base text-ink-2' : 'text-base text-ink-2'} role="status">
          {t(slow ? 'dataFigureV2.working.slow' : 'dataFigureV2.working.drawing')}
        </p>
        {!thumb && <ProgressBar pct={null} label={t('dataFigureV2.working.drawing')} className="w-56" />}
      </Top>
    )
  return (
    <Top anchor="data-studio-working">
      <p className="text-base text-ink-2" role="status">
        {t('dataFigureV2.working.proposing')}
      </p>
      <ProgressBar pct={null} label={t('dataFigureV2.working.proposing')} className="w-56" />
    </Top>
  )
}

/** 一张表画不出：一句人话（按后端给的原因码）+ 底栏「返回」 */
export function NothingScene({ reasons, onBack }: { reasons: string[]; onBack: () => void }) {
  const { t } = useTranslation('ai')
  const first = reasons.find((r) => KNOWN_NOTHING_REASONS.includes(r))
  return (
    <Top anchor="data-studio-nothing">
      <p className="text-base">{first ? t(`dataFigureV2.reason.${first}`) : t('dataFigureV2.nothing.generic')}</p>
      <OverlayFooter
        left={
          <Button variant="ghost" size="lg" data-studio-back onClick={onBack}>
            {t('dataFigureV2.back')}
          </Button>
        }
      />
    </Top>
  )
}

/** 图画好了，但有话要说：哪几行没画出来 / 数据没能逐项核对（不写成已核对）。说完点「打开这张图」 */
export function DrawnScene({ notes, onOpen }: { notes: DrawNotes; onOpen: () => void }) {
  const { t } = useTranslation('ai')
  const shown = notes.unplotted ? notes.unplotted.rows.slice(0, 8) : []
  return (
    <Top anchor="data-studio-drawn">
      <div className="flex flex-col gap-2">
        {notes.unplotted && (
          <p className="text-base" data-studio-unplotted>
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
          <p className={notes.unplotted ? 'text-base text-ink-2' : 'text-base'} data-studio-unchecked>
            {t(`dataFigureV2.result.${notes.check}`)}
          </p>
        )}
      </div>
      <OverlayFooter
        right={
          <Button variant="primary" size="lg" data-studio-primary onClick={onOpen}>
            {t('dataFigureV2.drawn.open')}
          </Button>
        }
      />
    </Top>
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
  // 主按钮在底栏右端，次要的在左端
  if (problem.kind === 'mismatch') {
    const reveal = root && canRevealInFileManager() ? () => void revealProjectFile(root, problem.script) : null
    return (
      <Top anchor="data-studio-problem">
        <p className="text-base">{t('dataFigureV2.result.mismatch')}</p>
        <p className="text-base text-ink-2">{t('dataFigureV2.problem.mismatchNext')}</p>
        {/* 脚本已经在项目里了：写明在哪，用户要删就知道删哪个（Tavotto 不替用户删文件） */}
        <p className="max-w-full truncate font-mono text-sm text-ink-3" data-studio-script-path title={problem.script}>
          {problem.script}
        </p>
        <OverlayFooter
          left={
            <>
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
            </>
          }
          right={
            <Button variant="primary" size="lg" data-studio-primary onClick={onBack}>
              {t('dataFigureV2.problem.otherWay')}
            </Button>
          }
        />
      </Top>
    )
  }
  if (problem.kind === 'unwritable') {
    return (
      <Top anchor="data-studio-problem">
        <p className="text-base">{t('dataFigureV2.problem.unwritable')}</p>
        <OverlayFooter
          left={
            <Button variant="secondary" size="lg" data-studio-retry onClick={onRetry}>
              {t('dataFigureV2.problem.retry')}
            </Button>
          }
          right={
            <Button variant="primary" size="lg" data-studio-primary onClick={onChooseFolder}>
              {t('dataFigureV2.problem.otherFolder')}
            </Button>
          }
        />
      </Top>
    )
  }
  return (
    <Top anchor="data-studio-problem">
      <p className="text-base">{problem.kind === 'commit' ? t('dataFigureV2.problem.notDrawn') : t('dataFigureV2.problem.proposeFailed')}</p>
      {problem.error && <p className="text-base text-ink-2">{fm(problem.error)}</p>}
      <OverlayFooter
        left={
          <Button variant="secondary" size="lg" data-studio-back onClick={onBack}>
            {t('dataFigureV2.back')}
          </Button>
        }
        right={
          <Button variant="primary" size="lg" data-studio-primary data-studio-retry onClick={onRetry}>
            {t('dataFigureV2.problem.retry')}
          </Button>
        }
      />
    </Top>
  )
}
