import { useLayoutEffect, useRef } from 'react'

/** 同一位置 400ms 内再次变化就直接换，计数器快速跳动时才读得清 */
const SETTLE_MS = 400
const ROLL_MS = 180

/**
 * 变化的字符从下方滑入。字符以「距右端的位置」为键，所以「9s → 10s」只有变了的位动。
 * 屏幕阅读器读 sr-only 的整串，逐字符的 span 隐藏。
 */
export function MorphText({ value }: { value: string }) {
  const chars = [...value]
  const committed = useRef(new Map<number, { ch: string; at: number; rolled: boolean }>())
  const now = performance.now()
  const rolls = chars.map((ch, i) => {
    const prev = committed.current.get(chars.length - i)
    if (!prev) return false
    // 一次滚动没播完前保持类名，否则移除类名会把它截断
    return prev.ch === ch ? prev.rolled && now - prev.at < ROLL_MS : now - prev.at > SETTLE_MS
  })

  useLayoutEffect(() => {
    const t = performance.now()
    chars.forEach((ch, i) => {
      const pos = chars.length - i
      if (committed.current.get(pos)?.ch !== ch) committed.current.set(pos, { ch, at: t, rolled: rolls[i] })
    })
  })

  return (
    <span className="relative inline-flex whitespace-nowrap tabular-nums">
      <span className="sr-only">{value}</span>
      {chars.map((ch, i) => {
        const pos = chars.length - i
        return (
          <span
            key={`${pos}:${ch}`}
            aria-hidden
            className={rolls[i] ? 'inline-block motion-safe:animate-pop-in-up' : 'inline-block'}
          >
            {ch === ' ' ? ' ' : ch}
          </span>
        )
      })}
    </span>
  )
}
