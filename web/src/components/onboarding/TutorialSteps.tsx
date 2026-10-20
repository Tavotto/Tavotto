import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, ArrowRight, Check, ChevronRight } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { PRODUCT_NAME } from '@/lib/brand'
import { dragRegionProps, TRAFFIC_LIGHTS_INSET, windowChrome } from '@/lib/desktop'
import { REAL_STEP_IDS, type StepId } from '@/lib/onboarding/stepIds'
import { skipTutorial } from '@/lib/onboarding/tutorial'
import { cn } from '@/lib/utils'
import { useOnboardingStore } from '@/store/onboardingStore'
import { BrandMark } from '../ui/BrandMark'
import { Button } from '../ui/Button'
import { DemoImportStep } from './DemoImportStep'
import { DemoLegendStep } from './DemoLegendStep'
import { DemoTitleStep } from './DemoTitleStep'
import { StepFeedbackContext, type StepFeedback } from './stepFeedback'
import { TutorialCompletion } from './TutorialCompletion'

/**
 * 教程的三步 + 完成页：顶栏（品牌 · 进度胶囊 · 跳过）+ 一张白卡（返回 · 这一步的内容 · 页脚）。
 *
 * 页脚的「下一步」只认 `completedSteps`：这一步的任务真的做到了（由各步在交互成功那一刻
 * `markDemoStep`）才亮，点它只是挪指针（`onboardingStore.next`），不替用户声称完成。
 * 完成过的步骤返回再来，「下一步」照旧亮着，演示改动也还在。
 */

const SUCCESS_KEY = {
  demo_import: 'onboarding.import.success',
  demo_move_legend: 'onboarding.legend.success',
  demo_change_title: 'onboarding.title.success',
} as const satisfies Record<Exclude<StepId, 'done'>, string>

export function TutorialSteps() {
  const { t } = useTranslation('dialogs')
  const step = useOnboardingStore((s) => s.currentStep) ?? REAL_STEP_IDS[0]
  const completed = useOnboardingStore((s) => s.completedSteps.includes(step))
  const next = useRef<HTMLButtonElement>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [announcement, setAnnouncement] = useState('')

  // 换步骤时提示与播报清空
  useEffect(() => {
    setNotice(null)
    setAnnouncement('')
  }, [step])

  const feedback = useMemo<StepFeedback>(
    () => ({
      notice: setNotice,
      announce: setAnnouncement,
      focusNext: () => requestAnimationFrame(() => next.current?.focus({ preventScroll: true })),
    }),
    [],
  )

  const index = REAL_STEP_IDS.indexOf(step)
  const isDone = step === 'done'
  const successKey = step === 'done' ? null : SUCCESS_KEY[step]
  const ob = useOnboardingStore.getState

  return (
    <div className="flex min-h-0 flex-1 flex-col px-4 pb-4 [@media(min-width:1000px)]:px-7" data-tutorial-view="steps" data-tutorial-step={step}>
      <header
        className="relative flex h-14 shrink-0 items-center justify-between"
        style={windowChrome() === 'overlay' ? { paddingLeft: TRAFFIC_LIGHTS_INSET - 16 } : undefined}
        {...dragRegionProps('self')}
      >
        <div className="flex items-center gap-2">
          <BrandMark size={28} />
          <span className="type-heading">
            {PRODUCT_NAME}
          </span>
        </div>
        {!isDone && (
          <>
            <div
              role="img"
              data-tutorial-progress={`${index + 1}/${REAL_STEP_IDS.length}`}
              aria-label={t('onboarding.shell.progress', { n: index + 1, total: REAL_STEP_IDS.length })}
              className="absolute left-1/2 flex -translate-x-1/2 items-center gap-5 rounded-full bg-surface-2 px-5 py-2 inset-ring inset-ring-border"
            >
              <Dots current={index} />
              <span aria-hidden="true" className="type-section tabular-nums">
                {t('onboarding.shell.count', { n: index + 1, total: REAL_STEP_IDS.length })}
              </span>
            </div>
            <Button size="md" className="gap-1.5 text-ink-2" data-tutorial-skip="" onClick={() => skipTutorial()}>
              {t('onboarding.shell.skip')}
              <ChevronRight size={ICON_SIZE.sm} />
            </Button>
          </>
        )}
      </header>

      <main
        aria-labelledby="tutorial-task-title"
        className="relative mx-auto flex min-h-0 w-full max-w-[1200px] flex-1 flex-col rounded-panel bg-surface px-6 pt-4 pb-5 inset-ring inset-ring-border shadow-[0_12px_30px_color-mix(in_srgb,var(--color-shadow)_4.5%,transparent)] [@media(min-width:1000px)]:w-[calc(100%-100px)]"
      >
        <Button
          size="md"
          className="absolute top-4 left-4 z-sticky gap-1.5 text-ink-2"
          data-tutorial-back=""
          onClick={() => {
            // 第一步再往回是欢迎页（教程状态不动：从那里「开始」接着这一步）
            if (index === 0) ob().openWelcome()
            else ob().back()
          }}
        >
          <ArrowLeft size={ICON_SIZE.sm} />
          {t('onboarding.shell.back')}
        </Button>
        <StepFeedbackContext.Provider value={feedback}>
          <div key={step} className="flex min-h-0 flex-1 animate-fade-in flex-col">
            <StepBody step={step} />
          </div>
        </StepFeedbackContext.Provider>
        {!isDone && (
          <footer className="flex shrink-0 flex-col items-center gap-2 pt-2">
            <div
              role="status"
              aria-live="polite"
              aria-atomic="true"
              data-tutorial-feedback={completed ? 'success' : notice ? 'notice' : ''}
              className="flex min-h-7 items-center justify-center gap-2 text-center text-lg text-ink-2"
            >
              {completed ? (
                <>
                  <span className="grid h-[18px] w-[18px] shrink-0 place-items-center rounded-full bg-ok text-surface">
                    <Check size={ICON_SIZE.xs} />
                  </span>
                  {successKey && t(successKey)}
                </>
              ) : (
                notice
              )}
            </div>
            <span className="sr-only" aria-live="polite" data-tutorial-announcer="">
              {announcement}
            </span>
            <Dots current={index} className="my-2" />
            <Button
              ref={next}
              variant="primary"
              size="lg"
              disabled={!completed}
              data-tutorial-next=""
              className="h-11 min-w-[260px] gap-3 text-lg"
              onClick={() => ob().next()}
            >
              {t(step === 'demo_change_title' ? 'onboarding.shell.finish' : 'onboarding.shell.next')}
              <ArrowRight size={ICON_SIZE.md} />
            </Button>
          </footer>
        )}
      </main>
    </div>
  )
}

function StepBody({ step }: { step: StepId }): ReactNode {
  switch (step) {
    case 'demo_import':
      return <DemoImportStep />
    case 'demo_move_legend':
      return <DemoLegendStep />
    case 'demo_change_title':
      return <DemoTitleStep />
    case 'done':
      return <TutorialCompletion />
  }
}

/** 三颗进度点：当前那颗实心墨色，其余淡（形状不变，靠读屏的「第 n 步，共 3 步」说清） */
function Dots({ current, className }: { current: number; className?: string }) {
  return (
    <span aria-hidden="true" className={cn('inline-flex items-center gap-3', className)}>
      {REAL_STEP_IDS.map((id, i) => (
        <i key={id} className={cn('block h-2 w-2 rounded-full', i === current ? 'bg-ink' : 'bg-selected')} />
      ))}
    </span>
  )
}
