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
        strokeWidth="1.6"
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
  return (
    <OptionGrid
      value={value}
      options={grid}
      onChange={onChange}
      columns={Math.min(4, grid.length)}
      ariaLabel={ariaLabel}
    />
  )
}
