import { useEffect, useRef, useState } from 'react'
import { Check, Copy } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { cn } from '@/lib/utils'
import { IconButton } from '../ui/Button'

/** 复制成功后 ✓ 停留多久（2026-10-07 设计审计 §6.3：1.2s，比设置页的 2s 短——对话流里复制是高频动作） */
export const COPIED_MS = 1200

/**
 * 助手面板里「复制」的统一出口（代码块、用户消息、diff 补丁）：一颗 xs 图标钮，复制成功后图标在
 * 同一格里淡换成 ✓，`COPIED_MS` 后换回；可达名与气泡一起改口说「已复制」。剪贴板不可用时保持原样
 * ——不装成功。
 */
export function CopyAction({
  text,
  label,
  copiedLabel,
  className,
  ...rest
}: {
  /** 要复制的文本；函数形式在点的那一刻才求值 */
  text: string | (() => string)
  label: string
  copiedLabel: string
  className?: string
} & Record<`data-${string}`, string | boolean | undefined>) {
  const [done, setDone] = useState(false)
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(timer.current), [])
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(typeof text === 'function' ? text() : text)
      setDone(true)
      window.clearTimeout(timer.current)
      timer.current = window.setTimeout(() => setDone(false), COPIED_MS)
    } catch {
      /* 剪贴板被策略挡住：不装成功 */
    }
  }
  return (
    <IconButton
      {...rest}
      data-copied={done || undefined}
      label={done ? copiedLabel : label}
      iconSize="xs"
      side="top"
      onClick={() => void copy()}
      className={cn('text-ink-3 hover:text-ink', className)}
    >
      <span className="grid place-items-center" aria-hidden>
        <Copy
          size={ICON_SIZE.sm}
          className={cn('col-start-1 row-start-1 transition-opacity duration-fast', done && 'opacity-0')}
        />
        <Check
          size={ICON_SIZE.sm}
          className={cn('col-start-1 row-start-1 text-ok transition-opacity duration-fast', !done && 'opacity-0')}
        />
      </span>
    </IconButton>
  )
}
