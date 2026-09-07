import { ChevronDown } from 'lucide-react'
import { ICON_SIZE } from '@/components/ui/Icon'
import { useState } from 'react'
import { t as translate } from '@/i18n'
import type { MarkerShape } from '@/lib/api'
import { cn } from '@/lib/utils'
import { optionLabel } from '../roles/registry'
import { Popover } from '../../ui/Popover'
import { OptionGrid, type GridOption } from './OptionGrid'

/** 多选取值不一致时触发按钮上的占位文案（函数：常量会把语言定死在模块求值那一刻） */
const MIXED_TEXT = () => translate('element.mixedValues', { ns: 'inspector' })


/**
 * Marker 选择器：图形网格，不再要求用户记住 "D" 是菱形、"^" 是上三角。
 * 写入值仍是 Matplotlib 原始 marker 字符；"original" 表示回到脚本原始
 * 路径（散点整体替换过 marker 之后的还原档）。
 *
 * **取值说不出形状的那两种情形，形状由引擎的只读事实补上**（`MarkerShape`，
 * manifest 的 `marker_current`）：散点没被换过标记时取值是 `"original"`，
 * 曲线的取值可能是 `(5, 1, 0)` / `$\alpha$` / 一个 Path 的 repr。事实里
 * 认得出名字的复用下面这份 switch 的图形，认不出的照顶点画。
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

/* -------------------- 引擎发来的几何 → 同一个 12×12 viewBox ------------------ */

/** 单位框四周留的边距：让照顶点画出来的形状与上面那份手绘图形份量相当 */
const PAD = 1.8
const SPAN = 12 - PAD * 2
/** 引擎的单位框是 [-0.5, 0.5] 且 **y 向上**；SVG 的 y 向下，所以 y 要翻过来 */
const sx = (v: number) => (PAD + (v + 0.5) * SPAN).toFixed(2)
const sy = (v: number) => (PAD + (0.5 - v) * SPAN).toFixed(2)

/** matplotlib 的路径码（`engine/manifest.py` 原样发过来的那几个数） */
const MOVETO = 1
const LINETO = 2
const CURVE3 = 3
const CURVE4 = 4
const CLOSEPOLY = 79

/**
 * 顶点 + codes → SVG 的 `d`。
 *
 * `codes` 为 null 是一种取值不是缺失：matplotlib 用它表示「首点 MOVETO，
 * 其余 LINETO」。CLOSEPOLY 那一个顶点是占位（引擎发的就是 `[0, 0]`），
 * 只闭合、不读坐标。认不出的码（STOP 之类）跳过——宁可少画一段也不猜。
 */
function toSvgPath(vertices: [number, number][], codes: number[] | null): string {
  const out: string[] = []
  let i = 0
  while (i < vertices.length) {
    const code = codes ? codes[i] : i === 0 ? MOVETO : LINETO
    const v = vertices[i]
    if (code === MOVETO || code === LINETO) {
      out.push(`${code === MOVETO ? 'M' : 'L'}${sx(v[0])} ${sy(v[1])}`)
      i += 1
    } else if (code === CURVE3) {
      const end = vertices[i + 1]
      if (!end) break
      out.push(`Q${sx(v[0])} ${sy(v[1])} ${sx(end[0])} ${sy(end[1])}`)
      i += 2
    } else if (code === CURVE4) {
      const c2 = vertices[i + 1]
      const end = vertices[i + 2]
      if (!c2 || !end) break
      out.push(`C${sx(v[0])} ${sy(v[1])} ${sx(c2[0])} ${sy(c2[1])} ${sx(end[0])} ${sy(end[1])}`)
      i += 3
    } else if (code === CLOSEPOLY) {
      out.push('Z')
      i += 1
    } else {
      i += 1
    }
  }
  return out.join(' ')
}

/**
 * 事实 → 一个形状（画不出就是 null）。
 *
 * **同时填充与描边**，不去猜这个标记「是不是实心的」：闭合的字形照常填出来；
 * `x` / `+` 那种开放子路径填出来是零面积（看不见），靠描边画；而星形元组标记
 * （`(4, 2, 0)`）是一条来回穿过中心的闭合路径，填出来同样是零面积——
 * 只填的话它会整个消失。一条规则同时罩住这三类。
 */
function shapeOfFact(fact: MarkerShape | undefined): React.ReactNode | null {
  if (!fact) return null
  if (fact.kind === 'named') {
    // 引擎给的名字这边画不出（两侧漂了）→ 退回没有这个字段时的样子
    return markerShape(fact.name)
  }
  if (fact.kind === 'path' && fact.vertices.length) {
    return (
      <path
        d={toSvgPath(fact.vertices, fact.codes)}
        fill="currentColor"
        stroke="currentColor"
        strokeWidth={1}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    )
  }
  // none / multiple / too_complex：都没有「那一个形状」可画
  return null
}

/**
 * 预览图形的外壳。`data-marker-preview` 是给判据用的锚点：触发按钮里还有
 * 下拉箭头、格子里还有选中角标，两个都是 `<svg>`——按标签名找的断言会被它们
 * 咬到，于是「没画形状」这句话永远为假。
 */
const Svg = ({ children }: { children: React.ReactNode }) => (
  <svg
    width="12"
    height="12"
    viewBox="0 0 12 12"
    aria-hidden
    data-marker-preview
    className="shrink-0"
  >
    {children}
  </svg>
)

/**
 * 取值之外，事实还多说了什么 —— 补进文字名里的那半句。
 *
 * 只在**取值自己说不出形状**时才补（`original` 那一档，以及认不出的代码）。
 * 取值本身就是已知图形时补一句「（圆点）」是纯噪音。
 */
function shapeNote(value: string, fact: MarkerShape | undefined): string | null {
  if (!fact || markerShape(value)) return null
  if (fact.kind === 'named' && markerShape(fact.name)) return optionLabel('marker', fact.name)
  if (fact.kind === 'multiple') return translate('element.markerShapeMultiple', { ns: 'inspector' })
  return null
}

/** 文字名：取值的名字，必要时带上事实补的那半句（网格里的可达名与 tooltip 同一份） */
function markerLabel(value: string, fact: MarkerShape | undefined): string {
  const base = optionLabel('marker', value)
  const note = shapeNote(value, fact)
  return note
    ? translate('element.markerValueWithShape', { ns: 'inspector', value: base, shape: note })
    : base
}

function MarkerPreview({ code, current }: { code: string; current?: MarkerShape }) {
  const shape = markerShape(code)
  if (shape) {
    return <Svg>{shape}</Svg>
  }
  const drawn = shapeOfFact(current)
  /**
   * `original`（散点没被整体换过标记）是**继承**，不是一个形状。
   *
   * 小空心状态点表示的正是「继承」（审计 T16）；以前画的是 `↺`，一个看起来
   * 像「点了会还原」的按钮字形，而它其实什么都不是。
   *
   * 图上真正那个形状由引擎的 `marker_current` 补上，画在状态点**旁边**——
   * 两件事是并列的：形状说「图上是个圆」，状态点说「这个圆是脚本给的、
   * 你没设过」。把状态点换成形状会让继承这一档消失。
   */
  if (code === 'original') {
    return (
      <span className="flex shrink-0 items-center gap-0.5">
        {drawn && <Svg>{drawn}</Svg>}
        <span
          aria-hidden
          data-marker-inherited
          className="grid h-3 w-3 shrink-0 place-items-center"
        >
          <span className="h-[7px] w-[7px] rounded-full border border-current opacity-60" />
        </span>
      </span>
    )
  }
  // 认不出的取值：引擎给了几何就照着画，否则退回文字（名字在旁边，气泡里也是名字）
  if (drawn) return <Svg>{drawn}</Svg>
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
  current,
  onChange,
  ariaLabel,
}: {
  /**
   * 当前值。**多选取值不一致时传 null**——那时一个格子都不该被标成选中，
   * 也不该把「空」当成一个自定义值塞进选项表。
   */
  value: string | null
  options: string[]
  /**
   * 图上此刻画的形状（manifest 的只读事实）。**只描述当前值那一格**：
   * 用户把散点换成菱形之后，「脚本原始」那一格就说不出形状了——引擎读到的
   * 是换过之后的路径，脚本原来那条已经不在图上。多选时各成员的事实不一致
   * 就不给，别拿其中一个冒充全体。
   */
  current?: MarkerShape
  onChange: (v: string) => void
  ariaLabel: string
}) {
  const [open, setOpen] = useState(false)
  const all = value && !options.includes(value) ? [value, ...options] : options
  const grid: GridOption[] = all.map((o) => ({
    value: o,
    label: o === value ? markerLabel(o, current) : optionLabel('marker', o),
    preview: <MarkerPreview code={o} current={o === value ? current : undefined} />,
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
          {value !== null && <MarkerPreview code={value} current={current} />}
          <span className="min-w-0 flex-1 truncate text-left">
            {value === null ? MIXED_TEXT() : markerLabel(value, current)}
          </span>
          <ChevronDown size={ICON_SIZE.xs} className="shrink-0 text-ink-3" />
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
