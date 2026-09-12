import { useEffect, useRef, useState } from 'react'
import { Check, Copy } from 'lucide-react'
import { ICON_SIZE } from '@/components/ui/Icon'
import { t as translate } from '@/i18n'
import { cn } from '@/lib/utils'
import { Button, type ButtonProps } from '../ui/Button'

const st = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 「复制」小按钮（设置页里路径 / 命令 / 诊断文本的统一出口）。
 *
 * 复制之后按钮自己说「已复制」两秒——不用 toast：这些按钮多半出现在折叠的
 * 详情区里，toast 会盖住用户正在看的东西。剪贴板不可用（无权限 / 非安全
 * 上下文）时保持原样，用户仍可从旁边的文本手工选中复制。
 *
 * 建在 `Button` 上（Session 6）：以前是一颗自己画的 24px 钮，与旁边 28px 的
 * 控件差一档、聚焦环与忙碌态也各写一套。默认 ghost 小钮（路径 / 命令旁），
 * 作为一行里的主动作时传 `variant="secondary"`。
 */
export function CopyButton({
  text,
  label,
  className,
  variant = 'ghost',
}: {
  /** 要复制的文本；函数形式用于「点的那一刻才生成」 */
  text: string | (() => string)
  /** 可达名（复制什么）；缺省「复制」 */
  label?: string
  className?: string
  variant?: ButtonProps['variant']
}) {
  const [done, setDone] = useState(false)
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(timer.current), [])
  const copy = async () => {
    const value = typeof text === 'function' ? text() : text
    try {
      await navigator.clipboard.writeText(value)
      setDone(true)
      window.clearTimeout(timer.current)
      timer.current = window.setTimeout(() => setDone(false), 2000)
    } catch {
      /* 剪贴板不可用：按钮保持原样 */
    }
  }
  const name = label ?? st('copy')
  return (
    <Button
      variant={variant}
      size="sm"
      onClick={() => void copy()}
      aria-label={name}
      title={name}
      className={variant === 'ghost' ? cn('text-ink-3 hover:text-ink', className) : className}
    >
      {done ? <Check size={ICON_SIZE.sm} aria-hidden /> : <Copy size={ICON_SIZE.sm} aria-hidden />}
      <span>{done ? st('copied') : st('copy')}</span>
    </Button>
  )
}
