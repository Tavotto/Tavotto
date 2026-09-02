import { useEffect, useRef, useState } from 'react'
import { Check, ClipboardCopy } from 'lucide-react'
import { t as translate } from '@/i18n'
import { cn } from '@/lib/utils'

const st = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 「复制」小按钮（设置页里路径 / 命令 / 诊断文本的统一出口）。
 *
 * 复制之后按钮自己说「已复制」两秒——不用 toast：这些按钮多半出现在折叠的
 * 详情区里，toast 会盖住用户正在看的东西。剪贴板不可用（无权限 / 非安全
 * 上下文）时保持原样，用户仍可从旁边的文本手工选中复制。
 */
export function CopyButton({
  text,
  label,
  className,
}: {
  /** 要复制的文本；函数形式用于「点的那一刻才生成」 */
  text: string | (() => string)
  /** 可达名（复制什么）；缺省「复制」 */
  label?: string
  className?: string
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
    <button
      type="button"
      onClick={() => void copy()}
      aria-label={name}
      title={name}
      className={cn(
        'inline-flex h-6 shrink-0 items-center gap-1 rounded-sm px-1.5 text-xs text-ink-3',
        'outline-none hover:bg-ink/[.045] hover:text-ink focus-visible:focus-ring',
        className,
      )}
    >
      {done ? <Check size={12} aria-hidden /> : <ClipboardCopy size={12} aria-hidden />}
      <span>{done ? st('copied') : st('copy')}</span>
    </button>
  )
}
