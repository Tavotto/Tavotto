import { useEffect, useId, useRef, useState } from 'react'
import { ChevronDown } from 'lucide-react'
import { ICON_SIZE } from '@/components/ui/Icon'
import { cn } from '@/lib/utils'
import { t as translate } from '@/i18n'
import { optionLabel } from '../roles/registry'
import { OptionGrid, type GridOption } from './OptionGrid'

/**
 * 线型选择器：真实线段预览，不再让用户先在脑子里把 "--" 翻译成虚线。
 *
 * 写入值仍是 Matplotlib 原始 enum（"-" / "--" / ":" / "-."）；
 * 认不出的值（脚本里自定义的 dash 元组，字符串形如 "(0, (1, 2))"）
 * 显示通用预览 + 原始名称，选它 = 保持原样。
 */

/** 已知线型 → SVG dasharray（viewBox 40 宽下的视觉近似，不承诺像素等价） */
const DASH: Record<string, string | undefined> = {
  '-': undefined,
  '--': '6 3',
  ':': '1.5 2.5',
  '-.': '6 2.5 1.5 2.5',
  none: '0 100',
  // 画布标注（Arrow/Shape）的线型代码：同一个选择器、同一种视觉语言（§16）
  solid: undefined,
  dashed: '6 3',
  dotted: '1.5 2.5',
}

function LinePreview({ style }: { style: string }) {
  const known = style in DASH
  return (
    <svg width="34" height="10" viewBox="0 0 34 10" aria-hidden className="shrink-0">
      <line
        x1="1"
        y1="5"
        x2="33"
        y2="5"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap={style === ':' ? 'round' : 'butt'}
        strokeDasharray={known ? DASH[style] : '4 2 1 2 1 2'}
      />
    </svg>
  )
}

export function LineStylePicker({
  value,
  options,
  onChange,
  ariaLabel,
  labelOf,
}: {
  /**
   * 当前值。**多选取值不一致时传 null**——那时一个格子都不该被标成选中，
   * 也不该把「空」当成一个自定义值塞进选项表。
   */
  value: string | null
  options: string[]
  onChange: (v: string) => void
  ariaLabel: string
  /** 选项显示名；缺省按 matplotlib linestyle 的 enum 表查 */
  labelOf?: (v: string) => string
}) {
  const nameOf = (o: string): string => {
    if (labelOf) return labelOf(o)
    const known = optionLabel('linestyle', o)
    return o in DASH || known !== o
      ? known
      : translate('control.customLineStyle', { ns: 'inspector', value: o })
  }
  // 当前值不在选项里（自定义 dash）也要能看到、能保持
  const all = value && !options.includes(value) ? [value, ...options] : options
  const grid: GridOption[] = all.map((o) => ({
    value: o,
    label: nameOf(o),
    preview: <LinePreview style={o} />,
    code: o,
  }))
  const current = value === null ? undefined : grid.find((o) => o.value === value)

  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const listId = useId()

  // 点外面 / Esc 收起；Esc 把焦点还给触发按钮
  useEffect(() => {
    if (!open) return
    const onPointerDown = (e: PointerEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.stopPropagation()
      setOpen(false)
      triggerRef.current?.focus()
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  const pick = (v: string) => {
    onChange(v)
    setOpen(false)
    triggerRef.current?.focus()
  }

  return (
    <div ref={rootRef} className="relative">
      <button
        ref={triggerRef}
        type="button"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        onClick={() => setOpen((o) => !o)}
        className="flex h-7 w-full items-center gap-2 rounded-sm border border-border bg-surface px-2 text-left text-sm text-ink hover:border-border-strong focus-visible:focus-ring"
      >
        {current ? (
          <>
            <LinePreview style={current.value} />
            <span className="min-w-0 flex-1 truncate">{current.label}</span>
          </>
        ) : (
          <span className="min-w-0 flex-1 truncate text-ink-3">
            {translate('mixed', { ns: 'common' })}
          </span>
        )}
        {/* 下拉的记号只有 chevron-down（Design Constitution 第四节）：展开时转到朝上 */}
        <ChevronDown
          size={ICON_SIZE.xs}
          aria-hidden
          className={cn('shrink-0 text-ink-3 transition-transform duration-fast', open && 'rotate-180')}
        />
      </button>
      {open && (
        <div
          id={listId}
          className="absolute left-0 top-full z-50 mt-1 min-w-full animate-pop-in rounded-md border border-border bg-surface p-1 shadow-pop"
        >
          <OptionGrid
            value={value}
            options={grid}
            onChange={pick}
            columns={1}
            ariaLabel={ariaLabel}
          />
        </div>
      )}
    </div>
  )
}
