import type { HTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

type Tone = 'neutral' | 'accent' | 'danger' | 'warn' | 'ok'

const TONES: Record<Tone, string> = {
  neutral: 'bg-surface-2 text-ink-2',
  accent: 'bg-accent-subtle text-accent',
  danger: 'bg-danger-subtle text-danger',
  warn: 'bg-warn-subtle text-warn',
  ok: 'bg-ok-subtle text-ok',
}

/**
 * 徽章 / 状态标签：全产品唯一允许用胶囊形（rounded-full）的文字元素——
 * 控件一律 6px 圆角，胶囊就成了「这是状态，不是按钮」的形状线索。
 * 16px 高、11px 字、不可折行；`mono` 给版本号 / 尺寸这类数据。
 * 颜色只表达语义（neutral / accent / danger / warn / ok），不做装饰。
 */
export function Badge({
  tone = 'neutral',
  mono,
  className,
  children,
  ...rest
}: HTMLAttributes<HTMLSpanElement> & { tone?: Tone; mono?: boolean }) {
  return (
    <span
      {...rest}
      className={cn(
        'inline-flex h-4 shrink-0 items-center whitespace-nowrap rounded-full px-1.5 text-xs leading-none',
        mono && 'font-mono',
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  )
}
