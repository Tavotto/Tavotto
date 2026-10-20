import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Minus, Plus } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { DEMO_FIGURE, normalizeTitleSize, stepTitleSize, TITLE_SIZE } from '@/lib/onboarding/demoFigure'
import { markDemoStep } from '@/lib/onboarding/tutorial'
import { cn } from '@/lib/utils'
import { useOnboardingStore } from '@/store/onboardingStore'
import { IconButton } from '../ui/Button'
import { NumberField } from '../ui/Input'
import { DemoFigure } from './DemoFigure'
import { isFocusVisible, useTrackedRect } from './tutorialGeometry'
import { FigureBoundary, FigureStage, Spotlight } from './tutorialLayout'
import { useStepFeedback } from './stepFeedback'

/**
 * 第三步：点击标题，把文字调大一点。
 *
 * **受控演示**：字号是教程自己的状态（`onboardingStore.demo.titleSizePt`，pt），不是 override。
 * 标题按 matplotlib 的方式排——水平居中、基线固定在子图上沿之上（字变大往上长），所以
 * 不会压到坐标轴、刻度或轴标签；上限 12 pt 是图幅还装得下的最大值（`TITLE_SIZE`）。
 *
 * 完成判据：提交的字号**大于进入这一步时的字号**——只点一下标题、只把焦点放进输入框都不算；
 * 输入框是 `ui/NumberField`（回车 / 失焦提交、Esc 还原、越界夹到边界并闪一下框、非数还原），
 * 无效输入不会让教程卡住。字号以 pt 为单位，跟着图的 viewBox 一起缩放，与屏幕 DPI 无关。
 */
export function DemoTitleStep() {
  const { t } = useTranslation('dialogs')
  const demo = useOnboardingStore((s) => s.demo)
  const done = useOnboardingStore((s) => s.completedSteps.includes('demo_change_title'))
  const fb = useStepFeedback()
  const size = demo.titleSizePt ?? TITLE_SIZE.initial
  // 进入这一步时的字号：「调大」是相对它说的。封顶在「上限再退一步」：已经站在上限的
  // 进入值（坏数据 / 旧存档）会让「大于进入时」永远不成立，第三步卡死
  const [entrySize] = useState(() => Math.min(size, TITLE_SIZE.max - TITLE_SIZE.step))
  const [selected, setSelected] = useState(false)
  const [focused, setFocused] = useState(false)
  const title = useRef<SVGGElement>(null)
  const stage = useRef<HTMLDivElement>(null)
  const controls = useRef<HTMLDivElement>(null)
  const rect = useTrackedRect(title, stage, [size, selected])

  const commit = (v: number) => {
    const next = normalizeTitleSize(v)
    if (next === null) return
    useOnboardingStore.getState().setDemo({ titleSizePt: next })
    if (!done && next > entrySize) {
      markDemoStep('demo_change_title')
      fb.notice(null)
      fb.focusNext()
    }
  }

  const select = (viaKeyboard: boolean) => {
    setSelected(true)
    // 键盘选中：焦点交给字号框，接着就能打字（指针选中不抢焦点）
    if (viaKeyboard) requestAnimationFrame(() => controls.current?.querySelector('input')?.focus())
  }

  const deselect = () => {
    setSelected(false)
    title.current?.focus({ preventScroll: true })
  }

  // 选中后点图外 / 图上别处：取消选中（与编辑器里点空白一致）
  useEffect(() => {
    if (!selected) return
    const away = (e: PointerEvent) => {
      const target = e.target as Node
      if (title.current?.contains(target) || controls.current?.contains(target)) return
      setSelected(false)
    }
    document.addEventListener('pointerdown', away)
    return () => document.removeEventListener('pointerdown', away)
  }, [selected])

  const onTitleKey = (e: KeyboardEvent<SVGGElement>) => {
    if ((e.key === 'Enter' || e.key === ' ') && !e.repeat) {
      e.preventDefault()
      select(true)
    }
  }

  const onControlsKey = (e: KeyboardEvent<HTMLDivElement>) => {
    // 框里的 Esc 走 NumberField 的 `onEscape`（它自己 stopPropagation，这里收不到）；
    // 这里只接加减按钮上的 Esc：收起选中、焦点回到标题
    if (e.key === 'Escape' && !e.defaultPrevented && (e.target as HTMLElement).tagName !== 'INPUT') {
      e.preventDefault()
      deselect()
    }
  }

  const guiding = !done && !selected

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <h1 id="tutorial-task-title" className="type-display mt-12 shrink-0 text-center [@media(max-height:720px)]:mt-9">
        {t(selected ? 'onboarding.title.taskSelected' : 'onboarding.title.task')}
      </h1>
      <div className="relative flex h-12 shrink-0 items-center justify-center">
        {selected && (
          <div
            ref={controls}
            role="group"
            aria-label={t('onboarding.title.controlsLabel')}
            data-tutorial-font-controls=""
            onKeyDown={onControlsKey}
            className={cn(
              'flex animate-pop-in items-center gap-1 rounded-lg bg-surface py-1 pr-2 pl-3 inset-ring inset-ring-border',
              !done && 'shadow-[0_0_0_4px_var(--color-surface),0_0_0_5px_var(--color-border-control)]',
            )}
          >
            <span aria-hidden="true" className="mr-1 text-sm text-ink-2">
              {t('onboarding.title.sizeLabel')}
            </span>
            <IconButton
              label={t('onboarding.title.decrease')}
              tip={false}
              data-tutorial-size-decrease=""
              disabled={size <= TITLE_SIZE.min}
              onClick={() => commit(stepTitleSize(size, -1))}
            >
              <Minus size={ICON_SIZE.sm} />
            </IconButton>
            <NumberField
              value={size}
              onChange={commit}
              min={TITLE_SIZE.min}
              max={TITLE_SIZE.max}
              step={TITLE_SIZE.step}
              precision={1}
              unit="pt"
              ariaLabel={t('onboarding.title.sizeAria', { min: TITLE_SIZE.min, max: TITLE_SIZE.max })}
              dataProp="tutorial-title-size"
              onEscape={deselect}
            />
            <IconButton
              label={t('onboarding.title.increase')}
              tip={false}
              data-tutorial-size-increase=""
              disabled={size >= TITLE_SIZE.max}
              onClick={() => commit(stepTitleSize(size, 1))}
            >
              <Plus size={ICON_SIZE.sm} />
            </IconButton>
            {!done && (
              <span
                aria-hidden="true"
                className="absolute left-[calc(50%+150px)] hidden items-center [@media(min-width:900px)]:flex"
              >
                <span className="h-2 w-2 translate-x-1 rotate-45 bg-ink" />
                <span className="rounded-md bg-ink px-3 py-1.5 text-sm font-medium whitespace-nowrap text-surface">
                  {t('onboarding.title.controlTip')}
                </span>
              </span>
            )}
          </div>
        )}
      </div>
      <FigureBoundary>
        <FigureStage
          ref={stage}
          className="mx-auto w-full max-w-[900px] flex-1 px-6 pt-3 pb-1 [@media(min-height:760px)]:max-h-[520px]"
          frameClassName="rounded-xs inset-ring inset-ring-border"
          overlay={
            guiding ? (
              <Spotlight rect={rect} tip={t('onboarding.title.tip')} />
            ) : selected || focused ? (
              <Spotlight rect={rect} dim={false} />
            ) : null
          }
        >
          <DemoFigure
            demo={demo}
            label={t('onboarding.title.figureLabel')}
            className="block h-full w-full"
            title={{
              ref: title,
              role: 'button',
              tabIndex: 0,
              'aria-pressed': selected,
              'aria-label': t('onboarding.title.titleLabel', { text: DEMO_FIGURE.title.text, size }),
              className: 'cursor-default outline-none',
              onClick: (e) => select(e.detail === 0),
              onKeyDown: onTitleKey,
              onFocus: (e) => setFocused(isFocusVisible(e.currentTarget)),
              onBlur: () => setFocused(false),
            }}
          />
        </FigureStage>
      </FigureBoundary>
    </div>
  )
}
