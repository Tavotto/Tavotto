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
  /** 无障碍名。缺省时从字符串 prefix/suffix 推导（"X (mm)"）；prefix 不是
   *  字符串又不给这个的话，屏幕阅读器只会念「编辑文本」——axe critical */
  ariaLabel?: string
  /** 拖动改数时把连续修改合并成一条撤销记录 */
  onScrubStart?: () => void
  onScrubEnd?: () => void
  /** 中性的稳定定位属性（data-inspector-prop）——测试与引导用，不进业务逻辑 */
  dataProp?: string
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
  ariaLabel,
  onScrubStart,
  onScrubEnd,
  dataProp,
}: NumberFieldProps) {
  const derivedLabel =
    ariaLabel ??
    (typeof prefix === 'string' && prefix
      ? typeof suffix === 'string' && suffix
        ? `${prefix} (${suffix})`
        : prefix
      : (title ?? undefined))
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
    // 原文没动过就不提交：键盘用户 Tab 路过一个输入框（聚焦→失焦）不该
    // 产生 onChange——上层会把它记成一条“修改”历史，撤销时表现为
    // 「按了没反应」（issue #37 的纯键盘闭环实测撞见）。
    if (raw === display) return
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
    // 外层只管排布：标签在框外，所以背景 / 边框 / focus 态都不在这一层。
    // 高度与禁用态仍写在这里——它是组件根，调用方的 className 也落在这里。
    <div
      title={title}
      className={cn(
        'group flex h-7 items-center gap-1.5',
        disabled && 'pointer-events-none opacity-40',
        className,
      )}
    >
      {prefix != null && (
        // 标签放在框外：框只圈住真正可编辑的部分，「字号」这类词读作行首的
        // 说明文字，而不是框里的一截。仍然是拖动改数的手柄（startScrub 没动）。
        // `min-w-5` 保留单字符标记（X / Y / W）的 20px 对齐宽度；
        // `whitespace-nowrap` 保证两字及以上的标签横排、不被折成上下两行。
        <span
          onPointerDown={startScrub}
          className="flex h-full min-w-5 shrink-0 cursor-ew-resize items-center justify-center whitespace-nowrap text-xs text-ink-3 select-none"
        >
          {prefix}
        </span>
      )}
      {/* 只有输入框才是「框」：hover / focus-within 挂在这一层，标签与单位都移到
          框外之后，焦点高亮只圈住真正可编辑的数字。框不再 flex-1 撑满整行——
          宽度由里面的输入框决定，刚好包住数字；min-w-0 让框在窄行里仍先让位
          （标签、单位都是 shrink-0）。 */}
      <div
        className={cn(
          'flex h-full min-w-0 items-center rounded-sm border border-transparent bg-surface-2',
          'transition-colors hover:border-border focus-within:border-accent focus-within:bg-surface',
        )}
      >
        <input
          ref={inputRef}
          type="text"
          inputMode="decimal"
          data-inspector-prop={dataProp}
          aria-label={derivedLabel}
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
            // 框里只剩它一个（标签、单位都在框外）。宽度 = 等宽字体 4 个字符
            // （12 / 1000 / 12.5 这类常见值刚好放下）+ 左右内边距 0.75rem——Tailwind 的
            // 盒模型是 border-box，只写 4ch 的话内边距会吃掉两个字符，「100」就只剩「10」
            // （2026-09-11 真项目里量到）。框只比数字大一圈，不再按文本框默认的 20 字符
            // 固有宽度（≈170px）撑开；数字居中。调用方要更宽时覆盖 input 的宽度即可。
            'num-input h-full w-[calc(4ch+0.75rem)] min-w-0 bg-transparent px-1.5 text-center text-ink outline-none',
            'placeholder:font-sans placeholder:text-ink-3',
          )}
        />
      </div>
      {suffix != null && (
        // 单位放在框外，与前缀标签对称：框只圈住可编辑的数字，「pt」读作框后的说明。
        // `shrink-0 whitespace-nowrap`：单位不是可以折行的正文。窄侧栏里
        // 「数据单位」被挤成「数据单」+「位」两行，把整行撑破（审计 T19，
        // 走查截图 95 拍到）。让位的应该是输入框（它 min-w-0），不是单位。
        <span className="shrink-0 whitespace-nowrap text-xs text-ink-3 select-none">
          {suffix}
        </span>
      )}
    </div>
  )
}

export function ColorField({
  value,
  onChange,
  onGestureEnd,
  className,
  ariaLabel,
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
  /**
   * 无障碍名，**必填**。这一格是**两个**输入框（取色盘 + 十六进制文本框），
   * 外面那行可见标签既不是 `<label for>` 也指不了两个控件，所以名字只能显式给。
   *
   * 为什么是必填而不是可选：2026-09-07 之前 18 个调用点一个都没给，读屏里
   * 它们全是「编辑文本」（axe `label` critical）。改成可选加一次性补齐的话，
   * 下一个调用点照样会漏——类型必填才是不会烂掉的那种纪律。
   *
   * chromium 上一直是绿的，webkit（Windows）第一次跑就报出来：`input[type=color]`
   * 在那儿退化成普通文本框，axe 的 `label` 规则才落到它头上。**引擎不同，
   * 能看见的维度也不同**，别拿「chromium 绿」当「没有这个缺陷」。
   */
  ariaLabel: string
}) {
  return (
    // 外层只管排布：色块在框外，所以背景 / 边框 / focus 态都不在这一层。
    // 高度写在这里——它是组件根，调用方的 className 也落在这里。
    <div className={cn('flex h-7 items-center gap-1.5', className)}>
      {/* 色块移到框外：框只圈住真正可编辑的色号。取色盘是**透明盖在色块上的真控件**，
          原来靠外层的 focus-within 边框顺带提示焦点；移出去之后必须自带一圈 focus
          ring，否则纯键盘 Tab 到它时屏幕上没有任何反馈。overflow-hidden 只裁子元素，
          不会吃掉这一层自己的 outline。 */}
      <div className="relative h-3.5 w-3.5 shrink-0 overflow-hidden rounded-[3px] border border-border-strong has-[:focus-visible]:focus-ring">
        <div className="absolute inset-0" style={{ background: value }} />
        <input
          type="color"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onBlur={onGestureEnd}
          aria-label={t('colorField.picker', { label: ariaLabel })}
          className="absolute inset-0 cursor-pointer opacity-0"
        />
      </div>
      {/* 十六进制框才是「框」：hover / focus-within 挂在这一层，色块移出去之后
          焦点高亮不该再把它一起框住。min-w-0 让框在窄行里先让位（色块 shrink-0）。 */}
      <div
        className={cn(
          'flex h-full min-w-0 flex-1 items-center rounded-sm border border-transparent bg-surface-2 px-1.5',
          'transition-colors hover:border-border focus-within:border-accent focus-within:bg-surface',
        )}
      >
        <input
          value={value.toUpperCase()}
          onChange={(e) => {
            const v = e.target.value
            if (/^#[0-9a-fA-F]{0,6}$/.test(v)) onChange(v)
          }}
          onBlur={onGestureEnd}
          onKeyDown={(e) => e.stopPropagation()}
          aria-label={t('colorField.hex', { label: ariaLabel })}
          className="num-input h-full w-full min-w-0 bg-transparent uppercase text-ink outline-none"
        />
      </div>
    </div>
  )
}
