import { NumberField } from '../../ui/Input'

/**
 * 透明度（0–1）的百分比控件：显示 75%、输入 75，写回 0.75（审计 T16 / T20）。
 *
 * 换算**只在这里**：展示注册表按 prop 名把 `alpha` / `framealpha` /
 * `grid_alpha` / `bbox_alpha` 分派到这种控件，单元素行与批量行共用它。
 * 引擎侧一个字不改——manifest 发的、override 存的仍是 0–1，写回原始
 * 文件与 golden vectors 看到的形状不变。
 *
 * 写回值按两位小数取整：`0.75` 而不是 `0.7500000000000001`——那种数会原样
 * 落进 override、写回脚本，成为用户从没输入过的一个「修改」。
 */

export const toPercent = (v: unknown): number =>
  Number.isFinite(Number(v)) ? Math.round(Number(v) * 100) : 0

export const fromPercent = (p: number): number => Math.round(p) / 100

export function PercentField({
  value,
  mixed,
  min = 0,
  max = 1,
  step = 0.05,
  ariaLabel,
  dataProp,
  onChange,
  onScrubStart,
  onScrubEnd,
}: {
  /** 0–1 的当前值 */
  value: unknown
  mixed?: boolean
  min?: number
  max?: number
  /** 引擎给的 0–1 步长（0.05 → 界面按 5% 走） */
  step?: number
  ariaLabel?: string
  dataProp?: string
  /** 写回 0–1 */
  onChange: (v: number) => void
  onScrubStart?: () => void
  onScrubEnd?: () => void
}) {
  return (
    <NumberField
      value={mixed ? 0 : toPercent(value)}
      mixed={mixed}
      min={Math.round(min * 100)}
      max={Math.round(max * 100)}
      step={Math.max(1, Math.round(step * 100))}
      precision={0}
      suffix="%"
      ariaLabel={ariaLabel}
      dataProp={dataProp}
      onChange={(p) => onChange(fromPercent(p))}
      onScrubStart={onScrubStart}
      onScrubEnd={onScrubEnd}
    />
  )
}
