import {
  forwardRef,
  useCallback,
  useEffect,
  useRef,
  useState,
  type InputHTMLAttributes,
  type ReactNode,
  type TextareaHTMLAttributes,
} from 'react'
import { t } from '@/i18n'
import { cn } from '@/lib/utils'

export const TextInput = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function TextInput({ className, ...props }, ref) {
    return (
      <input
        ref={ref}
        className={cn(
          'h-7 w-full min-w-0 rounded-sm border border-border bg-surface px-2 text-xs text-ink',
          'placeholder:text-ink-3 outline-none transition-colors',
          'hover:border-border-strong focus:border-accent focus:bg-surface',
          className,
        )}
        {...props}
      />
    )
  },
)

/** TextInput 的多行版：样式同源，供可含换行的文本字段（如图内文字）使用 */
export const TextArea = forwardRef<
  HTMLTextAreaElement,
  TextareaHTMLAttributes<HTMLTextAreaElement>
>(function TextArea({ className, ...props }, ref) {
  return (
    <textarea
      ref={ref}
      className={cn(
        'w-full min-w-0 resize-none rounded-sm border border-border bg-surface px-1.5 py-1 text-xs leading-relaxed text-ink',
        'placeholder:text-ink-3 outline-none transition-colors',
        'hover:border-border-strong focus:border-accent focus:bg-surface',
        className,
      )}
      {...props}
    />
  )
})

interface NumberFieldProps {
  value: number
  onChange: (v: number) => void
  /** 拖动 / 方向键的步长 */
  step?: number
  min?: number
  max?: number
  /** 小数位，仅影响显示 */
  precision?: number
  prefix?: ReactNode
  suffix?: ReactNode
  disabled?: boolean
  /** 多选且取值不一致：留空并显示占位符，而不是谎报一个数 */
  mixed?: boolean
  className?: string
  title?: string
  /** 拖动改数时把连续修改合并成一条撤销记录 */
  onScrubStart?: () => void
  onScrubEnd?: () => void
}

/**
 * 紧凑数值输入：等宽字体，前缀标签可横向拖动改数（Figma 手感），
 * Enter/失焦提交，Esc 还原。
 */
export function NumberField({
  value,
  onChange,
  step = 1,
  min = -100000,
  max = 100000,
  precision = 1,
  prefix,
  suffix,
  disabled,
  mixed,
  className,
  title,
  onScrubStart,
  onScrubEnd,
}: NumberFieldProps) {
  const [text, setText] = useState('')
  const [focused, setFocused] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  // Enter / Esc 自己决定提交与否，随后的 blur 不能再提交一次：
  // 重复提交会让每次输入多压一条撤销记录，Esc 也会变成「还原后又写回去」
  const skipBlurSubmit = useRef(false)

  const display = mixed
    ? ''
    : Number.isFinite(value)
      ? String(Number(value.toFixed(precision)))
      : ''

  useEffect(() => {
    if (!focused) setText(display)
  }, [display, focused])

  const clampVal = useCallback(
    (v: number) => Math.min(max, Math.max(min, v)),
    [min, max],
  )

  const submit = (raw: string) => {
    const parsed = Number(raw)
    if (raw.trim() !== '' && Number.isFinite(parsed)) onChange(clampVal(parsed))
    else setText(display)
  }

  const startScrub = (e: React.PointerEvent) => {
    if (disabled) return
    e.preventDefault()
    const startX = e.clientX
    const startVal = value
    const target = e.currentTarget as HTMLElement
    target.setPointerCapture(e.pointerId)
    let moved = false

    const move = (ev: PointerEvent) => {
      const dx = ev.clientX - startX
      if (Math.abs(dx) < 2 && !moved) return
      if (!moved) onScrubStart?.()
      moved = true
      const mult = ev.shiftKey ? 10 : ev.altKey ? 0.1 : 1
      onChange(clampVal(startVal + dx * step * mult))
    }
    const up = () => {
      target.releasePointerCapture(e.pointerId)
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      if (moved) onScrubEnd?.()
      else inputRef.current?.select()
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  return (
    <div
      title={title}
      className={cn(
        'group flex h-7 items-center rounded-sm border border-transparent bg-surface-2',
        'transition-colors hover:border-border focus-within:border-accent focus-within:bg-surface',
        disabled && 'pointer-events-none opacity-40',
        className,
      )}
    >
      {prefix != null && (
        <span
          onPointerDown={startScrub}
          className="flex h-full w-5 shrink-0 cursor-ew-resize items-center justify-center text-xs text-ink-3 select-none"
        >
          {prefix}
        </span>
      )}
      <input
        ref={inputRef}
        type="text"
        inputMode="decimal"
        disabled={disabled}
        value={text}
        placeholder={mixed ? t('mixed') : undefined}
        onChange={(e) => setText(e.target.value)}
        onFocus={(e) => {
          setFocused(true)
          e.target.select()
        }}
        onBlur={() => {
          setFocused(false)
          if (skipBlurSubmit.current) skipBlurSubmit.current = false
          else submit(text)
        }}
        onKeyDown={(e) => {
          e.stopPropagation()
          if (e.key === 'Enter') {
            submit(text)
            skipBlurSubmit.current = true
            ;(e.target as HTMLInputElement).blur()
          } else if (e.key === 'Escape') {
            setText(display)
            skipBlurSubmit.current = true
            ;(e.target as HTMLInputElement).blur()
          } else if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
            e.preventDefault()
            const mult = e.shiftKey ? 10 : 1
            const next = clampVal(value + (e.key === 'ArrowUp' ? step : -step) * mult)
            onChange(next)
            setText(String(Number(next.toFixed(precision))))
          }
        }}
        className={cn(
          'num-input h-full w-full min-w-0 bg-transparent px-1 text-ink outline-none',
          'placeholder:font-sans placeholder:text-ink-3',
          !prefix && 'pl-1.5',
        )}
      />
      {suffix != null && (
        <span className="pr-1.5 text-xs text-ink-3 select-none">{suffix}</span>
      )}
    </div>
  )
}

export function ColorField({
  value,
  onChange,
  onGestureEnd,
  className,
}: {
  value: string
  onChange: (v: string) => void
  /**
   * 这一轮取色结束（两个输入框任一失焦）。取色是连续动作：系统取色盘拖着走
   * 会发一串 change，调用方靠它把整轮压成一条历史 + 一次定稿渲染。
   * 原生对话框不保证发 blur，所以调用方另有安静计时兜底——这里只管报告
   * 确实发生了的失焦。
   */
  onGestureEnd?: () => void
  className?: string
}) {
  return (
    <div
      className={cn(
        'flex h-7 items-center gap-1.5 rounded-sm border border-transparent bg-surface-2 px-1.5',
        'transition-colors hover:border-border focus-within:border-accent',
        className,
      )}
    >
      <div className="relative h-3.5 w-3.5 shrink-0 overflow-hidden rounded-[3px] border border-border-strong">
        <div className="absolute inset-0" style={{ background: value }} />
        <input
          type="color"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onBlur={onGestureEnd}
          className="absolute inset-0 cursor-pointer opacity-0"
        />
      </div>
      <input
        value={value.toUpperCase()}
        onChange={(e) => {
          const v = e.target.value
          if (/^#[0-9a-fA-F]{0,6}$/.test(v)) onChange(v)
        }}
        onBlur={onGestureEnd}
        onKeyDown={(e) => e.stopPropagation()}
        className="num-input w-full min-w-0 bg-transparent uppercase text-ink outline-none"
      />
    </div>
  )
}
