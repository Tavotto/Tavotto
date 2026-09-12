import { useState } from 'react'

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
  const { mode: committed, ratio } = aspectModeOf(value)
  // 点下去立刻换档，不等引擎把 value 写回来。
  //
  // 这一档完全受控：mode 只从 value 推。写回没到（或者根本不会到——预览里
  // onPick 是个空壳）的那段时间里，「自定义比例」按下去 mode 还是 auto，
  // 数字框自然不挂载，看着就是「点了没反应」。乐观档只影响本地渲染，写入
  // 仍然只走 onPick 一条路，不产生第二个真值来源。
  //
  // 渲染期同步而不是 useEffect：省掉一帧，不会先画旧档再闪一下。committed
  // 一变（引擎确认了，或者别处改了这个属性）就丢掉乐观档，以引擎为准。
  const [seen, setSeen] = useState(committed)
  const [pending, setPending] = useState<AspectMode | null>(null)
  if (seen !== committed) {
    setSeen(committed)
    setPending(null)
  }
  const mode = pending ?? committed
  const custom = mode === 'custom'
  return (
    // 两行、不折行：分段控件靠右占第一行，自定义档的数字框在第二行贴控件列左缘，
    // 与上面「X 范围」那些框对齐。以前是「同一行、数字框从『自定义比例』右边抽出来」
    // 的动效，但那一行在出厂宽度 360 就放不下（zh 三个档名 + 框 > 控件列；en 一旦
    // 改过、右侧多了恢复芯片也放不下），只好允许换行——折下去的框悬在右边和谁都
    // 不对齐，而且宽度动画到一半才跳到第二行（2026-09-12 用户实测：躲过裁切但更怪）。
    // 什么宽度、哪种语言都是同一个样子，比一个只在宽面板 + 中文下成立的动效值钱。
    // 字段本身仍然只在自定义档挂载，不是 CSS 藏起来的：看不见却能 Tab 到、能被读屏
    // 念到的输入框是个陷阱。
    <div className="flex min-w-0 flex-1 flex-col gap-1">
      <Segmented
        tone="quiet"
        className="w-auto shrink-0 gap-1 self-end"
        ariaLabel={label}
        value={mode}
        onChange={(m) => {
          if (m === mode) return
          setPending(m)
          onPick(aspectValueOf(m, ratio ?? DEFAULT_RATIO))
        }}
        items={MODES.map((m) => ({ value: m, label: ctl(`aspect.${m}`) }))}
      />
      {custom && (
        <div className="animate-fade-in self-start">
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
        </div>
      )}
    </div>
  )
}
