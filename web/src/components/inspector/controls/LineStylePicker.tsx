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
}: {
  value: string
  options: string[]
  onChange: (v: string) => void
  ariaLabel: string
}) {
  // 当前值不在选项里（自定义 dash）也要能看到、能保持
  const all = options.includes(value) ? options : [value, ...options]
  const grid: GridOption[] = all.map((o) => ({
    value: o,
    label:
      o in DASH || optionLabel('linestyle', o) !== o
        ? optionLabel('linestyle', o)
        : translate('control.customLineStyle', { ns: 'inspector', value: o }),
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
