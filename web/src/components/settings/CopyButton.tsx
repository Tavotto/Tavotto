import { useEffect, useRef, useState } from 'react'
import { Check, Copy } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { t as translate } from '@/i18n'
import { cn } from '@/lib/utils'
import { Button, IconButton, type ButtonProps } from '../ui/Button'

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
 *
 * **两种形态**（全面打磨 D09）：`text` 是带「复制」二字的钮，只给「复制诊断」
 * 那种自己独立成立的动作；`icon` 是 28×28 的 `IconButton`，给紧跟在一段值
 * 后面的复制——项目页三行各挂一颗 67px 宽的「⧉ 复制」时，三个目录名旁边
 * 各站着一颗同样的钮，读起来比目录名本身还显眼。名字在可达名与气泡里。
 */
export function CopyButton({
  text,
  label,
  className,
  variant = 'ghost',
  appearance = 'text',
}: {
  /** 要复制的文本；函数形式用于「点的那一刻才生成」 */
  text: string | (() => string)
  /** 可达名（复制什么）；缺省「复制」 */
  label?: string
  className?: string
  variant?: ButtonProps['variant']
  /** text：带「复制」二字；icon：只有图标的 28×28 小钮（值旁边的那种） */
  appearance?: 'text' | 'icon'
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
  if (appearance === 'icon') {
    return (
      <IconButton
        // 复制完气泡与可达名一起改口说「已复制」——图标钮没有地方写第二行字
        label={done ? st('copied') : name}
        iconSize="sm"
        variant={variant}
        onClick={() => void copy()}
        className={cn(variant === 'ghost' && 'text-ink-3 hover:text-ink', className)}
      >
        {/* 两个图标叠在同一格淡换，与文字形态同一手法：换的时候钮不跳 */}
        <span className="grid place-items-center">
          <Copy
            size={ICON_SIZE.sm}
            aria-hidden
            className={cn(
              'col-start-1 row-start-1 transition-opacity duration-fast',
              done && 'opacity-0',
            )}
          />
          <Check
            size={ICON_SIZE.sm}
            aria-hidden
            className={cn(
              'col-start-1 row-start-1 transition-opacity duration-fast',
              !done && 'opacity-0',
            )}
          />
        </span>
      </IconButton>
    )
  }
  return (
    <Button
      variant={variant}
      size="sm"
      onClick={() => void copy()}
      aria-label={name}
      title={name}
      className={variant === 'ghost' ? cn('text-ink-3 hover:text-ink', className) : className}
    >
      {/* 两份内容叠在同一格、按宽者定宽：「复制 → 已复制」换字时按钮与它旁边的东西不跳
          （与 Button 的 loadingLabel 同一手法；2026-09-14 二审 E7）；图标淡换而不是硬切 */}
      <span className="grid place-items-center">
        <span
          className={cn('col-start-1 row-start-1 inline-flex items-center gap-1 transition-opacity duration-fast', done && 'opacity-0')}
          aria-hidden={done || undefined}
        >
          <Copy size={ICON_SIZE.sm} aria-hidden />
          <span>{st('copy')}</span>
        </span>
        <span
          className={cn('col-start-1 row-start-1 inline-flex items-center gap-1 transition-opacity duration-fast', !done && 'opacity-0')}
          aria-hidden={!done || undefined}
        >
          <Check size={ICON_SIZE.sm} aria-hidden />
          <span>{st('copied')}</span>
        </span>
      </span>
    </Button>
  )
}
