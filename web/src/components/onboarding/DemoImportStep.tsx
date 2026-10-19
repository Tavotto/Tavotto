import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
} from 'react'
import { useTranslation } from 'react-i18next'
import { Check, FileCodeCorner, GripVertical, Image } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { PRODUCT_NAME } from '@/lib/brand'
import { DURATION } from '@/lib/motion'
import { DEMO_FIGURE } from '@/lib/onboarding/demoFigure'
import { markDemoStep } from '@/lib/onboarding/tutorial'
import { cn } from '@/lib/utils'
import { useOnboardingStore } from '@/store/onboardingStore'
import { Button } from '../ui/Button'
import { DemoFigure } from './DemoFigure'
import { useTrackedRect } from './tutorialGeometry'
import { FigureBoundary, FigureStage } from './tutorialLayout'
import { useStepFeedback } from './stepFeedback'

/**
 * 第一步：把内置的 `fig1_kinetics.py` 卡片拖进投放框。
 *
 * **这是教程里的一个逻辑文件对象，不是系统文件拖放**：没有文件选择器、不读任何本机文件、
 * 不调用脚本运行接口。卡片真的落进投放框（松手点或卡片中心在框里）才算完成——悬停不算、
 * 计时不算；放错了平滑回到原位。键盘：空格拿起、方向键移动、回车放下、Esc 取消；
 * 不方便拖的人点「直接使用示例」，是同一个结果。
 *
 * 完成后出现的是内置演示图（`lib/onboarding/demoFigure`，按真实脚本量出来的几何），
 * 不是重新执行出来的——没有渲染这一步，所以也没有假的进度条。
 */

interface Drag {
  /** null = 键盘拿起 */
  pointerId: number | null
  startX: number
  startY: number
  dx: number
  dy: number
  moved: boolean
  over: boolean
}

const KEY_STEP = 30
const KEY_STEP_FINE = 10
const MOVE_THRESHOLD = 6

export function DemoImportStep() {
  const { t } = useTranslation('dialogs')
  const done = useOnboardingStore((s) => s.completedSteps.includes('demo_import'))
  const demo = useOnboardingStore((s) => s.demo)
  const fb = useStepFeedback()
  const host = useRef<HTMLDivElement>(null)
  const zone = useRef<HTMLDivElement>(null)
  const card = useRef<HTMLDivElement>(null)
  const [drag, setDragState] = useState<Drag | null>(null)
  const dragRef = useRef<Drag | null>(null)
  const [returning, setReturning] = useState(false)
  const name = DEMO_FIGURE.script

  const setDrag = useCallback((d: Drag | null) => {
    dragRef.current = d
    setDragState(d)
  }, [])

  const succeed = useCallback(() => {
    setDrag(null)
    fb.notice(null)
    markDemoStep('demo_import')
    fb.focusNext()
  }, [fb, setDrag])

  /** 放下点或卡片中心落在投放框里 */
  const inside = useCallback((x: number | null, y: number | null): boolean => {
    const z = zone.current?.getBoundingClientRect()
    const c = card.current?.getBoundingClientRect()
    if (!z || !c) return false
    const hit = (px: number, py: number) => px >= z.left && px <= z.right && py >= z.top && py <= z.bottom
    return (x !== null && y !== null && hit(x, y)) || hit(c.left + c.width / 2, c.top + c.height / 2)
  }, [])

  /** 拖动作废：回到原位（`animate` = 平滑回去；窗口失焦 / 缩放时直接复位） */
  const cancel = useCallback(
    (animate: boolean) => {
      const d = dragRef.current
      if (!d) return
      setDrag(null)
      const el = card.current
      if (el && d.pointerId !== null && el.hasPointerCapture(d.pointerId)) el.releasePointerCapture(d.pointerId)
      if (animate && (d.dx || d.dy)) setReturning(true)
    },
    [setDrag],
  )

  // 拖动期间：窗口失焦、缩放、切到后台都作废这一次（不能变成一次「放下」）
  useEffect(() => {
    if (!drag) return
    const reset = () => cancel(false)
    const hidden = () => document.hidden && reset()
    window.addEventListener('blur', reset)
    window.addEventListener('resize', reset)
    document.addEventListener('visibilitychange', hidden)
    return () => {
      window.removeEventListener('blur', reset)
      window.removeEventListener('resize', reset)
      document.removeEventListener('visibilitychange', hidden)
    }
  }, [drag !== null, cancel]) // eslint-disable-line react-hooks/exhaustive-deps

  // 卸载（返回 / 跳过 / 收起教程）时把捕获还回去
  useEffect(() => () => cancel(false), [cancel])

  // 回位动画的兜底：transitionend 没来（元素被藏起、动画被系统关掉）也要把卡片放回可拖的状态
  useEffect(() => {
    if (!returning) return
    const timer = window.setTimeout(() => setReturning(false), DURATION.slow + DURATION.fast)
    return () => window.clearTimeout(timer)
  }, [returning])

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (done || dragRef.current || !e.isPrimary || e.button !== 0) return
    e.preventDefault()
    // 回位途中再抓：不等动画走完（手比动画快是常事），从原位重新算起
    setReturning(false)
    e.currentTarget.focus({ preventScroll: true })
    e.currentTarget.setPointerCapture(e.pointerId)
    fb.notice(null)
    setDrag({ pointerId: e.pointerId, startX: e.clientX, startY: e.clientY, dx: 0, dy: 0, moved: false, over: false })
  }
  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    const d = dragRef.current
    if (!d || d.pointerId !== e.pointerId) return
    const dx = e.clientX - d.startX
    const dy = e.clientY - d.startY
    const moved = d.moved || Math.hypot(dx, dy) >= MOVE_THRESHOLD
    setDrag({ ...d, dx, dy, moved, over: moved && inside(e.clientX, e.clientY) })
  }
  const onPointerUp = (e: PointerEvent<HTMLDivElement>) => {
    const d = dragRef.current
    if (!d || d.pointerId !== e.pointerId) return
    if (d.moved && inside(e.clientX, e.clientY)) {
      if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId)
      succeed()
    } else {
      if (d.moved) fb.notice(t('onboarding.import.missed'))
      cancel(true)
    }
  }
  const onLost = (e: PointerEvent<HTMLDivElement>) => {
    if (dragRef.current?.pointerId === e.pointerId) cancel(true)
  }

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (done) return
    const d = dragRef.current
    const pick = e.key === ' ' || e.key === 'Enter'
    if (!d) {
      if (!pick || e.repeat) return
      e.preventDefault()
      fb.notice(null)
      setDrag({ pointerId: null, startX: 0, startY: 0, dx: 0, dy: 0, moved: false, over: false })
      fb.announce(t('onboarding.import.picked', { name }))
      return
    }
    if (d.pointerId !== null) return
    if (e.key === 'Escape') {
      e.preventDefault()
      cancel(true)
      fb.announce(t('onboarding.import.cancelled'))
      return
    }
    if (e.key === 'Tab') {
      cancel(false)
      return
    }
    const step = e.shiftKey ? KEY_STEP_FINE : KEY_STEP
    const delta: Record<string, [number, number]> = {
      ArrowLeft: [-step, 0],
      ArrowRight: [step, 0],
      ArrowUp: [0, -step],
      ArrowDown: [0, step],
    }
    if (delta[e.key]) {
      e.preventDefault()
      const [ddx, ddy] = delta[e.key]
      const next = { ...d, dx: d.dx + ddx, dy: d.dy + ddy, moved: true }
      setDrag(next)
      // 卡片中心在框里没有：等这一帧的位置落地再量
      requestAnimationFrame(() => {
        const cur = dragRef.current
        if (!cur || cur.pointerId !== null) return
        const over = inside(null, null)
        if (over !== cur.over) {
          setDrag({ ...cur, over })
          fb.announce(t(over ? 'onboarding.import.overZone' : 'onboarding.import.leftZone'))
        }
      })
      return
    }
    if (pick && !e.repeat) {
      e.preventDefault()
      if (inside(null, null)) succeed()
      else {
        fb.notice(t('onboarding.import.missed'))
        cancel(true)
      }
    }
  }

  // 箭头：从卡片右缘画到投放框上沿，按两者此刻的位置现算（不写死坐标）
  const cardRect = useTrackedRect(card, host, [done])
  const zoneRect = useTrackedRect(zone, host, [done])

  const lifted = drag !== null
  return (
    <div ref={host} className="relative flex min-h-0 flex-1 flex-col">
      <h1 id="tutorial-task-title" className="sr-only">
        {t('onboarding.import.task')}
      </h1>
      {done ? (
        <>
          <div className="flex h-14 shrink-0 items-end justify-center pb-2">
            <span
              data-tutorial-imported=""
              className="inline-flex items-center gap-2.5 rounded-md bg-surface-2 py-1.5 pr-2 pl-1.5 text-base text-ink inset-ring inset-ring-border"
            >
              <span className="grid h-7 w-7 place-items-center rounded-sm bg-field text-ink-2">
                <FileCodeCorner size={ICON_SIZE.sm} />
              </span>
              <span className="font-mono font-medium">{name}</span>
              <span className="text-sm text-ink-3">{t('onboarding.import.builtin')}</span>
              <span className="grid h-[18px] w-[18px] place-items-center rounded-full bg-ok text-surface">
                <Check size={ICON_SIZE.xs} />
              </span>
            </span>
          </div>
          <FigureBoundary>
            <FigureStage className="mx-auto w-full max-w-[900px] flex-1 animate-rise-in px-6 pt-3 pb-1 [@media(min-height:760px)]:max-h-[540px]" frameClassName="rounded-xs inset-ring inset-ring-border">
              <DemoFigure demo={demo} label={t('onboarding.import.figureLabel', { name })} className="block h-full w-full" />
            </FigureStage>
          </FigureBoundary>
        </>
      ) : (
        <>
          <div className="h-[clamp(150px,30%,190px)] shrink-0" />
          <div
            ref={zone}
            role="region"
            aria-label={t('onboarding.import.zoneLabel')}
            data-tutorial-drop-zone=""
            data-over={drag?.over || undefined}
            className={cn(
              'mx-[10%] mb-2 grid min-h-0 flex-1 place-items-center rounded-panel border-[1.5px] border-dashed border-border-control bg-surface-2 transition-[background-color,border-color,box-shadow] [@media(min-width:1000px)]:mx-[15%]',
              drag?.over && 'border-ink-3 bg-surface-active shadow-[0_0_0_4px_var(--color-surface-hover)]',
            )}
          >
            <div className="pointer-events-none flex flex-col items-center px-4 text-center">
              <Image size={ICON_SIZE.lg} className="mb-3 h-12 w-12 text-ink-faint" aria-hidden="true" />
              <p className="type-display">{t('onboarding.import.zoneTitle')}</p>
              <p className="mt-2 type-heading font-normal text-ink-3">{t('onboarding.import.zoneBody', { product: PRODUCT_NAME })}</p>
              {/* 拖不动的人（触控板 / 读屏 / 不想拖）的等价入口：放在投放框里、拖动时淡出，不和卡片抢位置 */}
              <div className={cn('mt-5 flex items-center gap-3 transition-opacity', lifted ? 'opacity-0' : 'pointer-events-auto')}>
                <span className="text-base text-ink-3">{t('onboarding.import.or')}</span>
                <Button variant="secondary" size="lg" data-tutorial-use-sample="" tabIndex={lifted ? -1 : undefined} onClick={succeed}>
                  {t('onboarding.import.useSample')}
                </Button>
              </div>
            </div>
          </div>
          <HintArrow from={cardRect} to={zoneRect} hidden={lifted} />
          <div className="absolute top-14 left-[max(4%,24px)] flex flex-col items-start gap-3">
            <div
              ref={card}
              role="button"
              tabIndex={0}
              aria-label={t('onboarding.import.cardLabel', { name })}
              aria-describedby="tutorial-card-help"
              aria-pressed={lifted}
              data-tutorial-file-card=""
              data-dragging={lifted || undefined}
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
              onPointerCancel={onLost}
              onLostPointerCapture={onLost}
              onKeyDown={onKeyDown}
              onDragStart={(e) => e.preventDefault()}
              onTransitionEnd={() => setReturning(false)}
              className={cn(
                'relative z-sticky flex h-[clamp(80px,11vh,100px)] w-[clamp(286px,26vw,310px)] cursor-grab touch-none items-center gap-4 rounded-lg bg-surface px-4 select-none inset-ring inset-ring-border outline-none focus-visible:focus-ring',
                lifted
                  ? 'cursor-grabbing shadow-[0_18px_34px_color-mix(in_srgb,var(--color-shadow)_15%,transparent),0_3px_6px_color-mix(in_srgb,var(--color-shadow)_4%,transparent)]'
                  : 'shadow-[0_12px_24px_color-mix(in_srgb,var(--color-shadow)_8%,transparent),0_2px_4px_color-mix(in_srgb,var(--color-shadow)_2.5%,transparent)] hover:bg-surface-2',
                returning && 'transition-transform duration-slow ease-[var(--ease-pop)]',
              )}
              style={{
                transform: lifted ? `translate(${drag.dx}px, ${drag.dy}px) scale(1.03)` : 'rotate(-5deg)',
              }}
            >
              <span className="grid h-14 w-14 shrink-0 place-items-center rounded-lg bg-field text-ink-2">
                <FileCodeCorner size={ICON_SIZE.lg} className="h-7 w-7" />
              </span>
              <span className="flex min-w-0 flex-col gap-0.5">
                <span className="type-title truncate font-mono font-medium">{name}</span>
                <span className="truncate text-sm text-ink-3">{t('onboarding.import.cardHint')}</span>
              </span>
              <GripVertical size={ICON_SIZE.lg} className="ml-auto shrink-0 text-ink-faint" aria-hidden="true" />
            </div>
            <span id="tutorial-card-help" className="sr-only">
              {t('onboarding.import.keyboardHelp')}
            </span>
          </div>
        </>
      )}
    </div>
  )
}

/** 一条虚线弧，从卡片指向投放框（纯装饰，aria-hidden；拿起卡片时淡出） */
function HintArrow({
  from,
  to,
  hidden,
}: {
  from: { x: number; y: number; w: number; h: number } | null
  to: { x: number; y: number; w: number; h: number } | null
  hidden: boolean
}) {
  if (!from || !to) return null
  const sx = from.x + from.w + 14
  const sy = from.y + from.h * 0.4
  const ex = to.x + to.w * 0.55
  const ey = to.y - 10
  if (ex <= sx || ey <= sy) return null
  const d = `M${sx} ${sy} C${sx + (ex - sx) * 0.55} ${sy - 28} ${ex} ${sy - 10} ${ex} ${ey}`
  return (
    <svg
      aria-hidden="true"
      className={cn('pointer-events-none absolute inset-0 h-full w-full overflow-visible text-ink-faint transition-opacity', hidden && 'opacity-0')}
    >
      <path d={d} fill="none" stroke="currentColor" strokeWidth={2} strokeDasharray="7 7" />
      <path d={`M${ex - 6} ${ey - 8} L${ex} ${ey} L${ex + 6} ${ey - 8}`} fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}
