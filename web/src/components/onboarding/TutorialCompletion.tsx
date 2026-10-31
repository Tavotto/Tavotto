import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowRight, CircleCheck, RotateCcw } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { PRODUCT_NAME } from '@/lib/brand'
import { legendDistance, LEGEND_MIN_MOVE_PT, TITLE_SIZE } from '@/lib/onboarding/demoFigure'
import { finishTutorial, restartTutorial } from '@/lib/onboarding/tutorial'
import { cn } from '@/lib/utils'
import { DEMO_EDITS_EMPTY, useOnboardingStore, type DemoEdits } from '@/store/onboardingStore'
import { Button } from '../ui/Button'
import { DemoFigure } from './DemoFigure'
import { FigureBoundary, FigureStage } from './tutorialLayout'

/**
 * 完成页：「调整前 / 你的调整成果」。
 *
 * 两张图是**同一份数据**（`demoFigure.json`）按两份演示状态各画一次：左边是脚本原样
 * （`DEMO_EDITS_EMPTY`），右边是这一轮里用户真的做出来的（`onboardingStore.demo`）。曲线、
 * 刻度、坐标范围逐点相同——不同的只有图例位置与标题字号。没有评分、徽章、烟花。
 *
 * 「开始使用」记完成、收起教程层，落回底下真实的主页 / 工作台（不自动打开文件选择器、
 * 不跑任何脚本）；「重新体验」从第一步重来。
 */
export function TutorialCompletion() {
  const { t } = useTranslation('dialogs')
  const demo = useOnboardingStore((s) => s.demo)
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => heading.current?.focus({ preventScroll: true }), [])

  const size = demo.titleSizePt ?? TITLE_SIZE.initial
  const moved = legendDistance(demo.legendOffset) >= LEGEND_MIN_MOVE_PT

  return (
    <div className="flex min-h-0 flex-1 flex-col items-center gap-4 pt-12 [@media(max-height:720px)]:gap-2.5 [@media(max-height:720px)]:pt-9">
      <h1 ref={heading} id="tutorial-task-title" tabIndex={-1} className="type-display shrink-0 outline-none">
        {t('onboarding.finish.title', { product: PRODUCT_NAME })}
      </h1>
      <div
        role="group"
        aria-label={t('onboarding.finish.compareLabel')}
        data-tutorial-compare=""
        className="grid min-h-0 w-full flex-1 grid-cols-2 gap-5 [@media(max-height:720px)]:gap-4"
      >
        <Panel
          which="before"
          heading={t('onboarding.finish.before')}
          demo={DEMO_EDITS_EMPTY}
          label={t('onboarding.finish.beforeAlt', { size: TITLE_SIZE.initial })}
        />
        <Panel
          which="after"
          heading={t('onboarding.finish.after')}
          demo={demo}
          label={t(moved ? 'onboarding.finish.afterAlt' : 'onboarding.finish.afterAltUnmoved', { size })}
        />
      </div>
      <p className="shrink-0 text-center text-lg text-ink-3">{t('onboarding.finish.nextHint')}</p>
      <p className="-mt-2 shrink-0 text-center text-sm text-ink-3">{t('onboarding.finish.demoNote')}</p>
      <div className="flex shrink-0 flex-col items-center gap-1.5">
        <Button
          variant="primary"
          size="lg"
          data-tutorial-start-using=""
          className="h-11 min-w-[260px] gap-3 text-lg"
          onClick={() => finishTutorial()}
        >
          {t('onboarding.finish.start', { product: PRODUCT_NAME })}
          <ArrowRight size={ICON_SIZE.md} />
        </Button>
        <Button size="md" className="gap-1.5 text-ink-2" data-tutorial-replay="" onClick={() => restartTutorial()}>
          <RotateCcw size={ICON_SIZE.sm} />
          {t('onboarding.finish.replay')}
        </Button>
      </div>
    </div>
  )
}

function Panel({
  which,
  heading,
  demo,
  label,
}: {
  which: 'before' | 'after'
  heading: string
  demo: DemoEdits
  label: string
}) {
  const after = which === 'after'
  return (
    <section
      data-tutorial-compare-panel={which}
      className={cn(
        'flex min-h-0 min-w-0 flex-col gap-3 rounded-lg p-4 inset-ring [@media(max-height:720px)]:gap-2 [@media(max-height:720px)]:p-3',
        after ? 'inset-ring-border-strong' : 'inset-ring-border',
      )}
    >
      <h2 className={cn('flex shrink-0 items-center gap-1.5 text-lg', after ? 'font-medium text-ink' : 'text-ink-3')}>
        {after && <CircleCheck size={ICON_SIZE.md} className="text-ok" aria-hidden="true" />}
        {heading}
      </h2>
      <FigureBoundary>
        <FigureStage className="flex-1" frameClassName="rounded-xs inset-ring inset-ring-border">
          <DemoFigure demo={demo} label={label} className="block h-full w-full" />
        </FigureStage>
      </FigureBoundary>
    </section>
  )
}
