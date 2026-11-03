import { useEffect, useLayoutEffect, useRef } from 'react'

import { useEffectiveTheme } from '@/lib/theme'
import { cn } from '@/lib/utils'
import { createOrb, disposeOrb, drawOrb, now, readInk, type Orb } from './thinkingOrb/loop'
import type { OrbForm, OrbSize } from './thinkingOrb/presets'

export type { OrbForm } from './thinkingOrb/presets'
export type OrbScale = 'row' | 'hero'
const ORB_PX: Record<OrbScale, OrbSize> = { row: 16, hero: 64 }

/**
 * 思考小球（ADR 0164；改自 thinking-orbs，MIT）：AI「正在想 / 正在做 / 想过了」的点阵小球。
 *
 * - 只出现在改图助手（`components/ai/`）里：过程行的 16px 图标槽、发出后等第一个字的那一行、
 *   还没开始对话的空白面板（64px）。文件读写、导出、安装这类「加载」照旧是 `Spinner`。
 * - `tone="live"`：进行中，墨色 `ink`，正常速度；`tone="settled"`：结束后，墨色 `ink-3`、半速，**也一直在动**
 *   （用户 2026-11-03：「我希望这些都会动」）。
 * - 墨色读调用处的语义 token（canvas 自己的 `color`），换主题（`useEffectiveTheme`）当场重读重画。
 * - 所有小球共用一个时钟、一个循环；滚出视口的不画（长对话里几十颗历史小球不会同时转），
 *   标签页隐藏时一起停；`prefers-reduced-motion` 下画一帧静止的。
 * - 纯装饰：`aria-hidden`，意思由旁边的文字说。
 */
export function ThinkingOrb({
  form,
  size: scale = 'row',
  tone = 'live',
  className,
}: {
  form: OrbForm
  /** `row` = 16px（过程行图标槽、状态行）；`hero` = 64px（空白面板）——只有这两档 */
  size?: OrbScale
  tone?: 'live' | 'settled'
  className?: string
}) {
  const size = ORB_PX[scale]
  const ref = useRef<HTMLCanvasElement | null>(null)
  const orb = useRef<Orb | null>(null)
  const theme = useEffectiveTheme()

  useLayoutEffect(() => {
    const canvas = ref.current
    if (!canvas) return
    const o = createOrb(canvas, form, size, tone)
    if (!o) return
    orb.current = o
    return () => {
      orb.current = null
      disposeOrb(o)
    }
  }, [form, size, tone])

  // 换主题：canvas 的 `color` 跟着 token 变了，重读墨色再画一帧（循环里的下一帧也用新墨色）
  useEffect(() => {
    const o = orb.current
    if (!o) return
    o.ink = readInk(o.canvas)
    drawOrb(o, now())
  }, [theme])

  return (
    <canvas
      ref={ref}
      aria-hidden
      data-thinking-orb={form}
      data-thinking-orb-tone={tone}
      width={size}
      height={size}
      style={{ width: size, height: size }}
      className={cn('block shrink-0', tone === 'live' ? 'text-ink' : 'text-ink-3', className)}
    />
  )
}
