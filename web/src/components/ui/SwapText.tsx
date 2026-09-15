import { useEffect, useRef, useState, type HTMLAttributes } from 'react'
import { prefersReducedMotion } from '@/lib/motion'
import { cn } from '@/lib/utils'

/**
 * 原位换字（2026-09-15，Spectrum UI 的 TextStates 手法；宪法第二十三节）。
 *
 * `text` 变了：刚被换掉的那句记成 `data-ghost`，由 `index.css` 的 `swap-text` 画在 ::before 里
 * 压在同一个位置上退场（上移 4px 淡出，fast）；新字在里层 span 里接力进场（从下 4px 浮上来，
 * fast）。两段接力；盒子一开始就按新字定宽，幽灵按盒子裁。此前通知轨与改图助手状态行都是硬切——
 * 两条状态 1.4s 内先后到达时像闪了一下。
 *
 * 三条边界：
 *   - **DOM 文本永远是当前的**：幽灵只在生成内容里，`textContent` / 读屏 / e2e 立刻看到新字，
 *     动画只是点缀（第七节）；reduced-motion 下连幽灵都没有，就是换了一句话。
 *   - 首次渲染不播（没有旧字可退）。
 *   - 里层 span 按文本换 key：连换两句时上一段进场作废、新的一段从头来，最后一次 `animationend`
 *     把幽灵清掉。
 *
 * `className` 给外层盒子（布局：flex-1 / min-w-0），`textClassName` 与其余属性给里层那句字
 * （颜色、`text-shimmer`、`data-*`）：`background-clip: text` 得落在会动的那一层自己身上，
 * 落在外层的话被合成到独立图层的子元素会把字裁没。
 */
export function SwapText({
  text,
  className,
  textClassName,
  ...rest
}: {
  text: string
  className?: string
  textClassName?: string
} & Omit<HTMLAttributes<HTMLSpanElement>, 'children' | 'className'>) {
  const [pair, setPair] = useState<{ text: string; ghost: string | null }>({ text, ghost: null })
  // 渲染期派生「上一句」：React 会丢掉这一次的输出立刻重渲染，DOM 里不会出现新字没带幽灵的那一帧
  if (pair.text !== text) setPair({ text, ghost: prefersReducedMotion() ? null : pair.text })

  // 进场播完把幽灵清掉。原生监听而不是 onAnimationEnd：React 在没有 `AnimationEvent` 的环境
  // （jsdom）里改听 webkitAnimationEnd，用例派发的 animationend 到不了它
  const box = useRef<HTMLSpanElement>(null)
  useEffect(() => {
    const el = box.current
    if (!el) return
    const done = (e: AnimationEvent) => {
      if (e.animationName !== 'swap-in') return
      setPair((p) => (p.ghost === null ? p : { ...p, ghost: null }))
    }
    el.addEventListener('animationend', done)
    return () => el.removeEventListener('animationend', done)
  }, [])

  return (
    <span ref={box} className={cn('swap-text', className)} data-ghost={pair.ghost ?? undefined}>
      <span key={pair.text} className={textClassName} {...rest}>
        {pair.text}
      </span>
    </span>
  )
}
