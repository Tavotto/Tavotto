import { t as translate } from '@/i18n'
import { NumberField } from '../../ui/Input'
import { Segmented } from '../../ui/Segmented'

/**
 * 子图纵横比：**自动 / 等比例 / 自定义比例**，三档一个分段控件。
 *
 * 引擎按 text 发这个字段（`ax.get_aspect()` 是 `'auto'`、`'equal'` 或一个
 * 浮点数），落进通用的文字控件就成了一个带上下标、换行、大小写转换的富文本
 * 编辑器（审计 T12）。它根本不是一段文字：三个取值、其中一个带数字。
 *
 * 写回值与 setter 的可还原形式一致（`overrides._set_aspect`：
 * `'auto' | 'equal'` 原样，其余 `float(v)`）；getter 回的是
 * `str(round(float(aspect), 3))`，所以自定义档写的是数字串，不是数字——
 * 类型与 manifest 的 text 字段一致，不在协议里发明第二种形状。
 */

export type AspectMode = 'auto' | 'equal' | 'custom'

const MODES: AspectMode[] = ['auto', 'equal', 'custom']
/** 选「自定义」时的起点：1 = 等比例，从这儿再调最不意外 */
const DEFAULT_RATIO = 1

const ctl = (key: string, values?: Record<string, unknown>) =>
  translate(`control.${key}`, { ns: 'inspector', ...(values ?? {}) })

/** 引擎值 → 三档 + 数值（非法值按「自动」画，不发明一个数字） */
export function aspectModeOf(value: unknown): { mode: AspectMode; ratio: number | null } {
  const s = String(value ?? 'auto').trim()
  if (s === 'equal') return { mode: 'equal', ratio: null }
  if (s === 'auto' || s === '') return { mode: 'auto', ratio: null }
  const n = Number(s)
  return Number.isFinite(n) && n > 0 ? { mode: 'custom', ratio: n } : { mode: 'auto', ratio: null }
}

/** 三档 → 引擎能还原的字符串 */
export const aspectValueOf = (mode: AspectMode, ratio: number = DEFAULT_RATIO): string =>
  mode === 'custom' ? String(ratio) : mode

export function AspectControl({
  value,
  label,
  onPick,
  onRatio,
  onScrubStart,
  onScrubEnd,
}: {
  value: unknown
  /** 整组的可达名（「纵横比」） */
  label: string
  /** 离散写入：换档 = 一条历史 + 一次渲染 */
  onPick: (v: string) => void
  /** 连续写入：自定义数值 scrub */
  onRatio: (v: string) => void
  onScrubStart?: () => void
  onScrubEnd?: () => void
}) {
  const { mode, ratio } = aspectModeOf(value)
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-1">
      <Segmented
        tone="quiet"
        className="w-full"
        ariaLabel={label}
        value={mode}
        onChange={(m) => {
          if (m === mode) return
          onPick(aspectValueOf(m, ratio ?? DEFAULT_RATIO))
        }}
        items={MODES.map((m) => ({ value: m, label: ctl(`aspect.${m}`) }))}
      />
      {mode === 'custom' && (
        <NumberField
          className="w-[92px]"
          dataProp="aspect"
          ariaLabel={ctl('aspectRatio')}
          value={ratio ?? DEFAULT_RATIO}
          min={0.05}
          max={20}
          step={0.1}
          precision={3}
          onChange={(v) => onRatio(aspectValueOf('custom', v))}
          onScrubStart={onScrubStart}
          onScrubEnd={onScrubEnd}
        />
      )}
    </div>
  )
}
