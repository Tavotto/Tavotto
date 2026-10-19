import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import { useTranslation } from 'react-i18next'
import {
  clampLegendOffset,
  DEMO_FIGURE,
  LEGEND_MIN_MOVE_PT,
  legendDistance,
  plainText,
} from '@/lib/onboarding/demoFigure'
import { markDemoStep } from '@/lib/onboarding/tutorial'
import { useOnboardingStore } from '@/store/onboardingStore'
import { DemoFigure } from './DemoFigure'
import { isFocusVisible, useTrackedRect } from './tutorialGeometry'
import { FigureBoundary, FigureStage, Spotlight } from './tutorialLayout'
import { useStepFeedback } from './stepFeedback'

/**
 * 第二步：拖动图例，换个位置。
 *
 * **受控演示**：图例的位置是教程自己的状态（`onboardingStore.demo.legendOffset`，图的 pt），
 * 不是 override、不进撤销历史、不写回任何文件；坐标轴与曲线一像素不动。拖动按图自己的
 * 坐标系算（屏幕 → SVG 的逆变换），所以窗口多大、缩放多少都对得上；图例被夹在坐标轴框里。
 *
 * 完成判据：图例离脚本里的原位置至少挪开 `LEGEND_MIN_MOVE_PT`（手抖不算）。放下就留在新位置，
 * 之后还能接着拖；返回上一步再回来，位置还在。键盘：空格拿起、方向键移动（Shift 微调）、
 * 回车放下、Esc 取消（回到拿起前的位置）。
 */

interface Drag {
  pointerId: number | null
  /** 拿起前的位移（取消时回到这里） */
  origin: [number, number]
  /** 指针按下时在图坐标里的点（键盘拿起时不用） */
  grab: [number, number] | null
  offset: [number, number]
}

const KEY_STEP_PT = 4
const KEY_STEP_FINE_PT = 1

export function DemoLegendStep() {
  const { t } = useTranslation('dialogs')
  const demo = useOnboardingStore((s) => s.demo)
  const done = useOnboardingStore((s) => s.completedSteps.includes('demo_move_legend'))
  const fb = useStepFeedback()
  const svg = useRef<SVGSVGElement>(null)
  const legend = useRef<SVGGElement>(null)
  const stage = useRef<HTMLDivElement>(null)
  const [drag, setDragState] = useState<Drag | null>(null)
  const dragRef = useRef<Drag | null>(null)
  const [focused, setFocused] = useState(false)
  const committed: [number, number] = demo.legendOffset ?? [0, 0]

  const setDrag = useCallback((d: Drag | null) => {
    dragRef.current = d
    setDragState(d)
  }, [])

  /** 屏幕点 → 图坐标（pt） */
  const toFigure = (x: number, y: number): [number, number] | null => {
    const m = svg.current?.getScreenCTM()
    if (!m) return null
    const p = new DOMPoint(x, y).matrixTransform(m.inverse())
    return [p.x, p.y]
  }

  const cancel = useCallback(() => {
    const d = dragRef.current
    if (!d) return
    setDrag(null)
    const el = legend.current
    if (el && d.pointerId !== null && el.hasPointerCapture(d.pointerId)) el.releasePointerCapture(d.pointerId)
  }, [setDrag])

  const finish = useCallback(() => {
    const d = dragRef.current
    if (!d) return
    setDrag(null)
    const el = legend.current
    if (el && d.pointerId !== null && el.hasPointerCapture(d.pointerId)) el.releasePointerCapture(d.pointerId)
    const moved = Math.hypot(d.offset[0] - d.origin[0], d.offset[1] - d.origin[1])
    if (moved < 0.5) return // 点了一下，不是拖动
    useOnboardingStore.getState().setDemo({ legendOffset: d.offset })
    if (legendDistance(d.offset) >= LEGEND_MIN_MOVE_PT) {
      fb.notice(null)
      if (!done) {
        markDemoStep('demo_move_legend')
        fb.focusNext()
      }
    } else if (!done) {
      fb.notice(t('onboarding.legend.farther'))
    }
  }, [done, fb, setDrag, t])

  // 拖动期间窗口失焦 / 缩放 / 切后台：作废，回到拿起前（不能变成一次「放下」）
  useEffect(() => {
    if (!drag) return
    const hidden = () => document.hidden && cancel()
    window.addEventListener('blur', cancel)
    window.addEventListener('resize', cancel)
    document.addEventListener('visibilitychange', hidden)
    return () => {
      window.removeEventListener('blur', cancel)
      window.removeEventListener('resize', cancel)
      document.removeEventListener('visibilitychange', hidden)
    }
  }, [drag !== null, cancel]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => cancel(), [cancel])

  const onPointerDown = (e: PointerEvent<SVGGElement>) => {
    if (dragRef.current || !e.isPrimary || e.button !== 0) return
    const p = toFigure(e.clientX, e.clientY)
    if (!p) return
    e.preventDefault()
    e.currentTarget.focus({ preventScroll: true })
    e.currentTarget.setPointerCapture(e.pointerId)
    setDrag({ pointerId: e.pointerId, origin: committed, grab: p, offset: committed })
  }
  const onPointerMove = (e: PointerEvent<SVGGElement>) => {
    const d = dragRef.current
    if (!d || d.pointerId !== e.pointerId || !d.grab) return
    const p = toFigure(e.clientX, e.clientY)
    if (!p) return
    setDrag({ ...d, offset: clampLegendOffset([d.origin[0] + p[0] - d.grab[0], d.origin[1] + p[1] - d.grab[1]]) })
  }
  const onPointerUp = (e: PointerEvent<SVGGElement>) => {
    if (dragRef.current?.pointerId === e.pointerId) finish()
  }
  const onLost = (e: PointerEvent<SVGGElement>) => {
    if (dragRef.current?.pointerId === e.pointerId) cancel()
  }

  const onKeyDown = (e: KeyboardEvent<SVGGElement>) => {
    const d = dragRef.current
    const pick = e.key === ' ' || e.key === 'Enter'
    if (!d) {
      if (!pick || e.repeat) return
      e.preventDefault()
      setDrag({ pointerId: null, origin: committed, grab: null, offset: committed })
      fb.announce(t('onboarding.legend.picked'))
      return
    }
    if (d.pointerId !== null) return
    if (e.key === 'Escape') {
      e.preventDefault()
      cancel()
      fb.announce(t('onboarding.legend.cancelled'))
      return
    }
    if (e.key === 'Tab') {
      cancel()
      return
    }
    const s = e.shiftKey ? KEY_STEP_FINE_PT : KEY_STEP_PT
    const delta: Record<string, [number, number]> = {
      ArrowLeft: [-s, 0],
      ArrowRight: [s, 0],
      ArrowUp: [0, -s],
      ArrowDown: [0, s],
    }
    if (delta[e.key]) {
      e.preventDefault()
      setDrag({ ...d, offset: clampLegendOffset([d.offset[0] + delta[e.key][0], d.offset[1] + delta[e.key][1]]) })
      return
    }
    if (pick && !e.repeat) {
      e.preventDefault()
      finish()
      fb.announce(t('onboarding.legend.dropped'))
    }
  }

  const live = drag?.offset ?? committed
  const rect = useTrackedRect(legend, stage, [live[0], live[1]])
  const entries = DEMO_FIGURE.legend.entries.map((e) => plainText(e.text)).join('；')
  // 没做完、也没在拖：聚焦环 + 「拖动这里」。其余时候图例就是图例：鼠标悬停 / 拖动不画框（框跟着拖
  // 只会显得图例被套在一个盒子里），只有键盘聚焦时画一圈选中框——那是键盘用户唯一的位置提示
  const guiding = !done && !drag
  const outlined = !guiding && focused

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <h1 id="tutorial-task-title" className="type-display mt-12 shrink-0 text-center [@media(max-height:720px)]:mt-9">
        {t('onboarding.legend.task')}
      </h1>
      <span id="tutorial-legend-help" className="sr-only">
        {t('onboarding.legend.keyboardHelp')}
      </span>
      <FigureBoundary>
        <FigureStage
          ref={stage}
          className="mx-auto w-full max-w-[900px] flex-1 px-6 pt-5 pb-1 [@media(min-height:760px)]:max-h-[540px]"
          frameClassName="rounded-xs inset-ring inset-ring-border"
          overlay={
            guiding ? (
              <Spotlight rect={rect} tip={t('onboarding.legend.tip')} />
            ) : outlined ? (
              <Spotlight rect={rect} dim={false} />
            ) : null
          }
        >
          <DemoFigure
            ref={svg}
            demo={demo}
            liveLegendOffset={live}
            label={t('onboarding.legend.figureLabel')}
            className="block h-full w-full"
            legend={{
              ref: legend,
              role: 'button',
              tabIndex: 0,
              'aria-label': t('onboarding.legend.legendLabel', { entries }),
              'aria-describedby': 'tutorial-legend-help',
              'aria-pressed': !!drag,
              className: drag ? 'cursor-grabbing outline-none' : 'cursor-grab outline-none',
              style: { touchAction: 'none' },
              onPointerDown,
              onPointerMove,
              onPointerUp,
              onPointerCancel: onLost,
              onLostPointerCapture: onLost,
              onKeyDown,
              onFocus: (e) => setFocused(isFocusVisible(e.currentTarget)),
              onBlur: () => setFocused(false),
            }}
          />
        </FigureStage>
      </FigureBoundary>
    </div>
  )
}
