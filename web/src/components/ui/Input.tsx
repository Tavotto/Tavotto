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

/**
 * 输入框的「框」：边框、底色、hover / focus / invalid / disabled 全在这一份。
 * 单独的 `TextInput` 直接把它画在 `<input>` 上；带后缀的输入框把它画在外壳上、
 * 里面的 `<input>` 透明——两种形态同一套状态，不各写一遍。
 */
const BOX_CLASS = cn(
  'rounded-sm border border-border bg-surface text-xs text-ink transition-colors duration-fast',
  'hover:border-border-strong',
)
const BOX_FOCUS = 'focus:border-accent focus:bg-surface'
const BOX_FOCUS_WITHIN = 'focus-within:border-accent focus-within:bg-surface'
const BOX_INVALID = 'border-danger hover:border-danger'
const BOX_DISABLED = 'cursor-not-allowed bg-surface-2 opacity-60 hover:border-border'

export interface TextInputProps extends InputHTMLAttributes<HTMLInputElement> {
  /** 校验不过：红边 + `aria-invalid`；错误文案由调用方用 `aria-describedby` 指过去 */
  invalid?: boolean
  /**
   * 框内后缀：`[ 393.7      mm ]`——单位坐在框里、靠右，与数字形成稳定结构，
   * 而不是 `[393.7] mm` 那样漂在框外。给了它输入文字自动右对齐（数字的读法）；
   * 要左对齐传 `align="left"`。
   */
  suffix?: ReactNode
  align?: 'left' | 'right'
}

export const TextInput = forwardRef<HTMLInputElement, TextInputProps>(function TextInput(
  { className, invalid, suffix, align, disabled, ...props },
  ref,
) {
  const alignRight = align === 'right' || (align == null && suffix != null)
  if (suffix == null) {
    return (
      <input
        ref={ref}
        disabled={disabled}
        aria-invalid={invalid || undefined}
        className={cn(
          'h-7 w-full min-w-0 px-2 placeholder:text-ink-3 outline-none',
          BOX_CLASS,
          BOX_FOCUS,
          alignRight && 'text-right tabular-nums',
          invalid && BOX_INVALID,
          disabled && BOX_DISABLED,
          className,
        )}
        {...props}
      />
    )
  }
  return (
    // 外壳是「框」，输入框透明：hover / focus 状态挂在外壳上，后缀也在框里
    <span
      className={cn(
        'flex h-7 w-full min-w-0 items-center',
        BOX_CLASS,
        BOX_FOCUS_WITHIN,
        invalid && BOX_INVALID,
        disabled && BOX_DISABLED,
        className,
      )}
    >
      <input
        ref={ref}
        disabled={disabled}
        aria-invalid={invalid || undefined}
        className={cn(
          'h-full min-w-0 flex-1 bg-transparent pl-2 pr-1 text-inherit placeholder:text-ink-3 outline-none',
          alignRight && 'text-right tabular-nums',
        )}
        {...props}
      />
      <span className="shrink-0 select-none whitespace-nowrap pr-2 text-ink-3">{suffix}</span>
    </span>
  )
})

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
  /**
   * 框外的单位 / 说明（`[393.7] mm`）。1.0 之前的形态；页面级 Session 逐页迁到
   * `unit`（框内），迁完这个字段就删。新代码不要再用它。
   */
  suffix?: ReactNode
  /**
   * 框内单位：`[ 393.7      mm ]`。数字右对齐、单位靠右坐在同一个框里，
   * 一列数字框的单位就排成一条稳定的竖线（Design Constitution 第五节）。
   */
  unit?: ReactNode
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
  unit,
  disabled,
  mixed,
  className,
  title,
  ariaLabel,
  onScrubStart,
  onScrubEnd,
  dataProp,
}: NumberFieldProps) {
  const unitText = typeof unit === 'string' && unit ? unit : typeof suffix === 'string' && suffix ? suffix : ''
  const derivedLabel =
    ariaLabel ??
    (typeof prefix === 'string' && prefix
      ? unitText
        ? `${prefix} (${unitText})`
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
          'transition-colors duration-fast hover:border-border focus-within:border-accent focus-within:bg-surface',
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
            'num-input h-full w-[calc(4ch+0.75rem)] min-w-0 bg-transparent px-1.5 text-ink outline-none',
            'placeholder:font-sans placeholder:text-ink-3',
            // 框内有单位时数字右对齐、贴着单位；没有单位时居中（框只比数字大一圈）
            unit != null ? 'pr-1 text-right' : 'text-center',
          )}
        />
        {unit != null && (
          <span className="num-input shrink-0 select-none whitespace-nowrap pr-1.5 text-ink-3">
            {unit}
          </span>
        )}
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
   * 这一轮取色结束（取色盘失焦）。取色是连续动作：系统取色盘拖着走会发一串
   * change，调用方靠它把整轮压成一条历史 + 一次定稿渲染。原生对话框不保证发
   * blur，所以调用方另有安静计时兜底——这里只管报告确实发生了的失焦。
   */
  onGestureEnd?: () => void
  className?: string
  /**
   * 无障碍名，**必填**。外面那行可见标签不是 `<label for>`，取色盘的名字只能
   * 显式给。2026-09-07 之前 18 个调用点一个都没给，读屏里它们全是「编辑文本」
   * （axe `label` critical）；类型必填才是不会烂掉的那种纪律。
   *
   * chromium 上一直是绿的，webkit（Windows）第一次跑就报出来：`input[type=color]`
   * 在那儿退化成普通文本框，axe 的 `label` 规则才落到它头上。
   */
  ariaLabel: string
}) {
  return (
    // 只剩一块色块（2026-09-11 用户反馈：去掉色号框，点色块取色）。
    // 取色盘是**透明盖在色块上的真控件**，自带一圈 focus ring——纯键盘 Tab 到它
    // 时屏幕上得有反馈；overflow-hidden 只裁子元素，不会吃掉这一层自己的 outline。
    // 当前色号走 title：鼠标悬停仍看得到精确值。
    <div className={cn('flex h-7 items-center', className)}>
      <div
        title={value.toUpperCase()}
        className="relative h-5 w-8 shrink-0 overflow-hidden rounded-sm border border-border-strong transition-colors hover:border-ink/45 has-[:focus-visible]:focus-ring"
      >
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
    </div>
  )
}
