import { useLayoutEffect, useRef, useState } from 'react'
import { cn } from '@/lib/utils'

/**
 * 长名字保留有区分力的尾部：`X 轴 “Reaction time (mi…”` 截掉的正是括号里的单位，
 * 中间省略成 `X 轴 “Reaction ti…(min)”` 才认得出（2026-09-14 二审 D3；组件模式 §4
 * 「长名字保留有区分力的部分」）。CSS 没有中间省略，这里把尾部 `tail` 个字符固定住、
 * 头部按宽度截；读屏读到的是两段连着的完整文字，完整值另由调用方放在 title / aria-label。
 *
 * `measure`：按真实字宽量出「头部 + … + 尾部」刚好放下的头长，省略号两侧不留空隙
 * （CSS 的 text-overflow 会在被截掉的半个字处留下空隙：`Fig2_c… ation`）。量不了（无 canvas /
 * 没有宽度 / 无 ResizeObserver）时退回上面的 CSS 两段式。可见文字 `aria-hidden`，完整文本放进
 * `sr-only`，所以无障碍名字永远是全名，不含省略号。
 */
export function TruncateMiddle({
  text,
  tail = 6,
  className,
  measure = false,
}: {
  text: string
  /** 固定保留的尾部字符数 */
  tail?: number
  className?: string
  /** 按字宽测量截断（见上） */
  measure?: boolean
}) {
  if (measure && typeof ResizeObserver !== 'undefined') return <MeasuredMiddle text={text} tail={tail} className={className} />
  return <CssMiddle text={text} tail={tail} className={className} />
}

function CssMiddle({ text, tail, className }: { text: string; tail: number; className?: string }) {
  if (text.length <= tail + 4) return <span className={cn('block truncate', className)}>{text}</span>
  // whitespace-pre：切口两侧的空格不能被行尾折叠掉（「time (mi」切成「time」+「 (mi」时那个空格在头段末尾）
  return (
    <span className={cn('flex min-w-0', className)}>
      <span className="min-w-0 truncate whitespace-pre">{text.slice(0, -tail)}</span>
      <span className="shrink-0 whitespace-pre">{text.slice(-tail)}</span>
    </span>
  )
}

function MeasuredMiddle({ text, tail, className }: { text: string; tail: number; className?: string }) {
  const box = useRef<HTMLSpanElement>(null)
  const [shown, setShown] = useState<string | null>(null)
  useLayoutEffect(() => {
    const el = box.current
    if (!el) return
    const fit = () => {
      const width = el.clientWidth
      const ctx = document.createElement('canvas').getContext?.('2d') ?? null
      if (!ctx || width <= 0) return setShown(null)
      const cs = getComputedStyle(el)
      ctx.font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`
      if (ctx.measureText(text).width <= width) return setShown(text)
      const t = text.slice(-tail)
      let lo = 0
      let hi = Math.max(0, text.length - tail)
      while (lo < hi) {
        const mid = Math.ceil((lo + hi) / 2)
        if (ctx.measureText(text.slice(0, mid) + '…' + t).width <= width) lo = mid
        else hi = mid - 1
      }
      setShown(text.slice(0, lo) + '…' + t)
    }
    fit()
    const ro = new ResizeObserver(fit)
    ro.observe(el)
    return () => ro.disconnect()
  }, [text, tail, className])
  return (
    <>
      <span ref={box} aria-hidden="true" className={cn('block overflow-hidden whitespace-pre', className)}>
        {shown ?? text}
      </span>
      <span className="sr-only">{text}</span>
    </>
  )
}
