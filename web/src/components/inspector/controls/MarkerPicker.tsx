import { ChevronDown } from 'lucide-react'
import { useState } from 'react'
import { t as translate } from '@/i18n'
import { cn } from '@/lib/utils'
import { optionLabel } from '../roles/registry'
import { Popover } from '../../ui/Popover'
import { OptionGrid, type GridOption } from './OptionGrid'

/** 多选取值不一致时触发按钮上的占位文案（函数：常量会把语言定死在模块求值那一刻） */
const MIXED_TEXT = () => translate('element.mixedValues', { ns: 'inspector' })


/**
 * Marker 选择器：图形网格，不再要求用户记住 "D" 是菱形、"^" 是上三角。
 * 写入值仍是 Matplotlib 原始 marker 字符；"original" 表示回到脚本原始
 * 路径（散点整体替换过 marker 之后的还原档）；认不出的 marker 显示原始
 * 代码字样，选它 = 保持原样。
 */

/** 已知 marker → 12×12 viewBox 里的图形 */
function markerShape(code: string): React.ReactNode | null {
  const stroke = { fill: 'none', stroke: 'currentColor', strokeWidth: 1.4 } as const
  const fill = { fill: 'currentColor' } as const
  switch (code) {
    case 'o':
      return <circle cx="6" cy="6" r="3.4" {...fill} />
    case '.':
      return <circle cx="6" cy="6" r="1.6" {...fill} />
    case 's':
      return <rect x="2.8" y="2.8" width="6.4" height="6.4" {...fill} />
    case 'D':
      return <path d="M6 1.6 10.4 6 6 10.4 1.6 6Z" {...fill} />
    case 'd':
      return <path d="M6 1.4 8.8 6 6 10.6 3.2 6Z" {...fill} />
    case '^':
      return <path d="M6 2 10.2 9.6 1.8 9.6Z" {...fill} />
    case 'v':
      return <path d="M6 10 1.8 2.4 10.2 2.4Z" {...fill} />
    case '<':
      return <path d="M2 6 9.6 1.8 9.6 10.2Z" {...fill} />
    case '>':
      return <path d="M10 6 2.4 10.2 2.4 1.8Z" {...fill} />
    case 'x':
      return <path d="M2.5 2.5 9.5 9.5 M9.5 2.5 2.5 9.5" {...stroke} />
    case '+':
      return <path d="M6 1.8 6 10.2 M1.8 6 10.2 6" {...stroke} />
    case '*':
      return <path d="M6 1.5 6 10.5 M2.1 3.75 9.9 8.25 M9.9 3.75 2.1 8.25" {...stroke} />
    case 'p':
      return <path d="M6 1.6 10.3 4.8 8.6 10 3.4 10 1.7 4.8Z" {...fill} />
    case 'h':
      return <path d="M6 1.4 9.9 3.7 9.9 8.3 6 10.6 2.1 8.3 2.1 3.7Z" {...fill} />
    default:
      return null
  }
}

function MarkerPreview({ code }: { code: string }) {
  const shape = markerShape(code)
  if (shape) {
    return (
      <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden className="shrink-0">
        {shape}
      </svg>
    )
  }
  /**
   * `original`（散点没被整体换过标记）是**继承**，不是一个形状。
   *
   * 这里画一个小空心状态点（审计 T16「以小状态点表示继承」）。以前画的是
   * `↺`，一个看起来像「点了会还原」的按钮字形，而它其实什么都不是。
   *
   * **图上真正那个形状我们不知道**：manifest 只发 `"original"` 这个字，
   * 脚本用的 marker 路径没有出口。要显示真形状得给引擎加字段，那是另一件
   * 事（1.0 收敛纪律：不在文案修复里扩能力）。所以这里画的是「继承」，
   * 不是假装知道形状。
   */
  if (code === 'original') {
    return (
      <span aria-hidden data-marker-inherited className="grid h-3 w-3 shrink-0 place-items-center">
        <span className="h-[7px] w-[7px] rounded-full border border-current opacity-60" />
      </span>
    )
  }
  // 无 / 未识别：文字表达（名字在旁边，气泡里也是名字）
  const text = code === 'None' || code === 'none' || code === '' ? '—' : code
  return (
    <span aria-hidden className="max-w-10 truncate font-mono text-xs">
      {text}
    </span>
  )
}

export function MarkerPicker({
  value,
  options,
  onChange,
  ariaLabel,
}: {
  /**
   * 当前值。**多选取值不一致时传 null**——那时一个格子都不该被标成选中，
   * 也不该把「空」当成一个自定义值塞进选项表。
   */
  value: string | null
  options: string[]
  onChange: (v: string) => void
  ariaLabel: string
}) {
  const [open, setOpen] = useState(false)
  const all = value && !options.includes(value) ? [value, ...options] : options
  const grid: GridOption[] = all.map((o) => ({
    value: o,
    label: optionLabel('marker', o),
    preview: <MarkerPreview code={o} />,
    code: o || '""',
  }))

  return (
    <Popover
      width={216}
      align="start"
      open={open}
      onOpenChange={setOpen}
      trigger={
        <button
          type="button"
          aria-label={ariaLabel}
          className={cn(
            'flex h-7 w-full items-center gap-1.5 rounded-sm border border-transparent bg-surface-2 px-1.5',
            'text-xs text-ink outline-none transition-colors hover:border-border',
            'focus-visible:focus-ring',
            open && 'border-accent',
          )}
        >
          {/* 多选取值不一致：触发按钮说「多个值」，不谎报其中某一个的图形 */}
          {value !== null && <MarkerPreview code={value} />}
          <span className="min-w-0 flex-1 truncate text-left">
            {value === null ? MIXED_TEXT() : optionLabel('marker', value)}
          </span>
          <ChevronDown size={12} className="shrink-0 text-ink-3" />
        </button>
      }
    >
      <OptionGrid
        value={value}
        options={grid}
        onChange={(v) => {
          onChange(v)
          setOpen(false)
        }}
        columns={5}
        ariaLabel={ariaLabel}
      />
    </Popover>
  )
}
