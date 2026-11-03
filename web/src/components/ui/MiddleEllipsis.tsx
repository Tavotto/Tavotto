import { useLayoutEffect, useRef, useState } from 'react'
import { cn } from '@/lib/utils'
import { TruncateMiddle } from './TruncateMiddle'

/**
 * 中间省略（保留结尾）：按真实字宽量出「头部 + … + 尾部」刚好放下的头长，省略号两侧不留空隙
 * （CSS 的 text-overflow 会在被截掉的半个字处留下空隙：`Fig2_c… ation`）。
 * 量不了（无 canvas / 没有宽度）时退回 `TruncateMiddle`。
 */
export function MiddleEllipsis({ text, tail, className }: { text: string; tail: number; className?: string }) {
  const box = useRef<HTMLSpanElement>(null)
  const [shown, setShown] = useState<string | null>(null)
  useLayoutEffect(() => {
    const el = box.current
    if (!el) return
    const fit = () => {
      const width = el.clientWidth
      let ctx: CanvasRenderingContext2D | null = null
      try {
        ctx = document.createElement('canvas').getContext?.('2d') ?? null
      } catch {
        ctx = null
      }
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
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(fit)
    ro.observe(el)
    return () => ro.disconnect()
  }, [text, tail, className])
  if (shown === null && typeof ResizeObserver === 'undefined') {
    return <TruncateMiddle text={text} tail={tail} className={className} />
  }
  return (
    <span ref={box} className={cn('block overflow-hidden whitespace-pre', className)}>
      {shown ?? text}
    </span>
  )
}
