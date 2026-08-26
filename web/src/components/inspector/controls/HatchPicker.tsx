import { ChevronDown } from 'lucide-react'
import { useId, useState } from 'react'
import { t as translate } from '@/i18n'
import { cn } from '@/lib/utils'
import { Popover } from '../../ui/Popover'
import { OptionGrid, type GridOption } from './OptionGrid'

/** 多选取值不一致时触发按钮上的占位文案（函数：常量会把语言定死在模块求值那一刻） */
const MIXED_TEXT = () => translate('element.mixedValues', { ns: 'inspector' })


/**
 * Hatch（花纹）选择器：真实纹理缩略图。写入值仍是 Matplotlib 原始 hatch
 * 串（"/"、"xx"、"++" …），"" = 不用花纹。认不出的花纹显示原始串，
 * 选它 = 保持原样。
 */

/** 单个花纹字符 → SVG pattern 里的线条（8×8 tile 的视觉近似） */
function hatchLines(ch: string): React.ReactNode[] {
  const s = { stroke: 'currentColor', strokeWidth: 0.9, fill: 'none' } as const
  switch (ch) {
    case '/':
      return [<path key="a" d="M-2 10 10 -2 M-2 2 2 -2 M6 10 10 6" {...s} />]
    case '\\':
      return [<path key="a" d="M-2 -2 10 10 M6 -2 10 2 M-2 6 2 10" {...s} />]
    case '|':
      return [<path key="a" d="M4 0 4 8" {...s} />]
    case '-':
      return [<path key="a" d="M0 4 8 4" {...s} />]
    case '+':
      return [<path key="a" d="M4 0 4 8 M0 4 8 4" {...s} />]
    case 'x':
      return [<path key="a" d="M-2 10 10 -2 M-2 -2 10 10" {...s} />]
    case 'o':
      return [<circle key="a" cx="4" cy="4" r="2.2" {...s} />]
    case 'O':
      return [<circle key="a" cx="4" cy="4" r="3.2" {...s} />]
    case '.':
      return [<circle key="a" cx="4" cy="4" r="0.9" fill="currentColor" />]
    case '*':
      return [
        <path key="a" d="M4 1.2 4 6.8 M1.6 2.6 6.4 5.4 M6.4 2.6 1.6 5.4" {...s} />,
      ]
    default:
      return []
  }
}

function HatchPreview({ code }: { code: string }) {
  const pid = useId()
  if (!code) {
    return <span aria-hidden className="font-mono text-xs">—</span>
  }
  const chars = [...new Set(code.split(''))]
  const lines = chars.flatMap((c, i) =>
    hatchLines(c).map((n, j) => <g key={`${i}-${j}`}>{n}</g>),
  )
  if (!lines.length) {
    return <span aria-hidden className="max-w-10 truncate font-mono text-xs">{code}</span>
  }
  // 重复字符（"//"、"xx"）= 更密：tile 从 8 缩到 5
  const tile = code.length > 1 ? 5 : 8
  return (
    <svg width="26" height="16" aria-hidden className="shrink-0">
      <defs>
        <pattern id={pid} width={tile} height={tile} patternUnits="userSpaceOnUse"
          patternTransform={`scale(${tile / 8})`}>
          {lines}
        </pattern>
      </defs>
      <rect x="0.5" y="0.5" width="25" height="15" rx="1" fill={`url(#${pid})`}
        stroke="currentColor" strokeOpacity="0.35" strokeWidth="0.6" />
    </svg>
  )
}

const hatchLabel = (code: string): string =>
  code
    ? translate('control.hatchPattern', { ns: 'inspector', value: code })
    : translate('control.hatchNone', { ns: 'inspector' })

export function HatchPicker({
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
    label: hatchLabel(o),
    preview: <HatchPreview code={o} />,
    code: o ? o : '""',
  }))

  return (
    <Popover
      width={228}
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
          {value !== null && <HatchPreview code={value} />}
          <span className="min-w-0 flex-1 truncate text-left">
            {value === null ? MIXED_TEXT() : hatchLabel(value)}
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
        columns={4}
        ariaLabel={ariaLabel}
      />
    </Popover>
  )
}
