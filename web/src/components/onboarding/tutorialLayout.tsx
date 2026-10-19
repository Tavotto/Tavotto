import { Component, forwardRef, Fragment, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { RotateCcw } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { DEMO_FIGURE } from '@/lib/onboarding/demoFigure'
import { cn } from '@/lib/utils'
import { Button } from '../ui/Button'
import type { Rect } from './tutorialGeometry'

/**
 * 教程里几处共用的版面件：把演示图按比例放进可用空间（`FigureStage`）、把图里某个元素的
 * 屏幕框换算到舞台坐标（`useTrackedRect`）、指着它的聚焦环与提示（`Spotlight`）。
 *
 * **不写固定像素坐标**：环与提示每次都按目标元素此刻的 `getBoundingClientRect` 现量，
 * 窗口缩放、字号变化、图例挪动之后跟着重量（ResizeObserver + 依赖变化），不会与目标错位。
 */

const ASPECT = DEMO_FIGURE.figure.width / DEMO_FIGURE.figure.height

/**
 * 演示图的舞台：在父容器里按图幅比例取最大的框（contain），框里是一张纸（图本身）+ 覆盖层。
 * 窗口变小时先缩的是图周围的留白，不是图里的东西；图例与标题的交互目标随图一起缩放。
 */
export const FigureStage = forwardRef<
  HTMLDivElement,
  { children: ReactNode; overlay?: ReactNode; className?: string; frameClassName?: string }
>(function FigureStage({ children, overlay, className, frameClassName }, ref) {
  const host = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState<{ w: number; h: number } | null>(null)
  useLayoutEffect(() => {
    const el = host.current
    if (!el) return
    const fit = () => {
      const w = el.clientWidth
      const h = el.clientHeight
      if (!w || !h) return
      const fw = Math.min(w, h * ASPECT)
      const next = { w: Math.floor(fw), h: Math.floor(fw / ASPECT) }
      setSize((s) => (s && s.w === next.w && s.h === next.h ? s : next))
    }
    fit()
    // 没有 ResizeObserver 的宿主（jsdom）退回窗口缩放事件
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', fit)
      return () => window.removeEventListener('resize', fit)
    }
    const ro = new ResizeObserver(fit)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return (
    <div ref={host} className={cn('relative flex min-h-0 min-w-0 items-center justify-center', className)}>
      {size && (
        <div
          ref={ref}
          data-demo-stage=""
          className={cn('relative shrink-0', frameClassName)}
          style={{ width: size.w, height: size.h }}
        >
          {children}
          {overlay}
        </div>
      )}
    </div>
  )
})

/**
 * 聚焦环 + 一句提示：没有遮罩层吃点击（环与提示都不接指针），环外一圈很淡的暗化只为把目光
 * 引过去（从 `--color-shadow` 派生，暗色主题跟着走）。提示默认在目标上方，放不下就翻到下方。
 */
/** Spotlight 层伸出图外的宽度（px，= `-inset-3`） */
const BLEED = 12

export function Spotlight({ rect, tip, dim = true }: { rect: Rect | null; tip?: ReactNode; dim?: boolean }) {
  if (!rect) return null
  const pad = 5
  // 这一层比图多出 BLEED 一圈：贴着图边的目标（标题紧挨着图的上沿）外面那圈环不被裁掉
  const box = { x: rect.x - pad + BLEED, y: rect.y - pad + BLEED, w: rect.w + pad * 2, h: rect.h + pad * 2 }
  // 上方放不下（目标贴着图的上沿，如标题）就放到下方
  const below = box.y < 44 + BLEED
  return (
    <div aria-hidden="true" className="pointer-events-none absolute -inset-3 overflow-hidden rounded-md">
      <div
        data-tutorial-ring={dim ? 'guide' : 'outline'}
        className={cn(
          'absolute rounded-md border border-border-control',
          dim
            ? 'shadow-[0_0_0_4px_var(--color-paper),0_0_0_9999px_color-mix(in_srgb,var(--color-shadow)_12%,transparent)]'
            : 'shadow-[0_0_0_4px_var(--color-paper)]',
        )}
        style={{ left: box.x, top: box.y, width: box.w, height: box.h }}
      />
      {tip && (
        <div
          data-tutorial-tip=""
          className={cn('absolute flex -translate-x-1/2 flex-col items-center', !below && '-translate-y-full')}
          style={{ left: box.x + box.w / 2, top: below ? box.y + box.h + 10 : box.y - 10 }}
        >
          {below && <span className="h-2 w-2 translate-y-1 rotate-45 bg-ink" />}
          <span className="whitespace-nowrap rounded-md bg-ink px-3 py-1.5 text-sm font-medium text-surface">{tip}</span>
          {!below && <span className="h-2 w-2 -translate-y-1 rotate-45 bg-ink" />}
        </div>
      )}
    </div>
  )
}

/**
 * 演示图画不出来（内置数据损坏 / 浏览器不支持某个 SVG 特性）时的退路：一句话 +「重新加载示例」，
 * 顶栏的「跳过教程」照旧可用。说的是人话，不是 Python / 渲染栈的报错——教程里根本没有运行任何脚本。
 */
export class FigureBoundary extends Component<{ children: ReactNode }, { failed: boolean; round: number }> {
  state = { failed: false, round: 0 }
  static getDerivedStateFromError() {
    return { failed: true }
  }
  render() {
    if (this.state.failed) {
      return <FigureFailed onRetry={() => this.setState((s) => ({ failed: false, round: s.round + 1 }))} />
    }
    return <Fragment key={this.state.round}>{this.props.children}</Fragment>
  }
}

function FigureFailed({ onRetry }: { onRetry: () => void }) {
  const { t } = useTranslation('dialogs')
  return (
    <div role="alert" data-tutorial-figure-failed="" className="flex flex-1 flex-col items-center justify-center gap-3 text-center">
      <p className="type-reading text-ink-2">{t('onboarding.figureFailed.body')}</p>
      <Button variant="secondary" size="lg" onClick={onRetry} className="gap-1.5">
        <RotateCcw size={ICON_SIZE.sm} />
        {t('onboarding.figureFailed.retry')}
      </Button>
    </div>
  )
}
