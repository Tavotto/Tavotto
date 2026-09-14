import { useState } from 'react'
import { t as translate } from '@/i18n'
import { optionLabel } from '../roles/registry'
import { Popover } from '../../ui/Popover'
import { OptionGrid, type GridOption } from './OptionGrid'
import { PickerTrigger } from './PickerTrigger'

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
  // 弹层是一列（columns=1）：每行「样张 + 名字」，与箭头端型那一列同一形态；
  // 只画一条线的话四行看起来像四条没有名字的横杠
  const grid: GridOption[] = all.map((o) => ({
    value: o,
    label: nameOf(o),
    preview: (
      <span className="flex items-center gap-2 px-1 text-xs">
        <LinePreview style={o} />
        <span className="truncate">{nameOf(o)}</span>
      </span>
    ),
    code: o,
  }))
  const current = value === null ? undefined : grid.find((o) => o.value === value)

  const [open, setOpen] = useState(false)

  return (
    // 与 MarkerPicker / HatchPicker 同一副外壳（2026-09-14 审计 S5）：Popover 走 portal
    // （不再被面板 overflow 裁掉）、Esc 与点外面由 Radix 处理、焦点还回触发器；
    // aria-haspopup / aria-expanded 由 Popover.Trigger 落到 PickerTrigger 上。
    <Popover
      width={216}
      align="start"
      open={open}
      onOpenChange={setOpen}
      trigger={
        <PickerTrigger
          ariaLabel={ariaLabel}
          mixed={current === undefined}
          preview={current && <LinePreview style={current.value} />}
        >
          {current?.label}
        </PickerTrigger>
      }
    >
      <OptionGrid
        value={value}
        options={grid}
        onChange={onChange}
        onPick={() => setOpen(false)}
        columns={1}
        ariaLabel={ariaLabel}
      />
    </Popover>
  )
}
