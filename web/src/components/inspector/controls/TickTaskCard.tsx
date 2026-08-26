import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import type { EditableField } from '@/lib/api'
import { Row } from '../../ui/Field'
import { NumberField } from '../../ui/Input'
import { Segmented } from '../../ui/Segmented'
import { Toggle } from '../../ui/Toggle'
import { ResetChip, labeledWithState } from './textRows'
import type { TickDirection } from './TickAndSpineDiagram'

/**
 * 刻度任务卡：**「刻度在哪、朝哪、要不要次刻度」在同一处完成**。
 *
 * 修改前这三件事分散在三个地方：四边开关在子图页的状态图上，方向与长宽在
 * 「刻度组」元素的「刻度线」折叠组里，次刻度在同一个元素的「刻度定位」折叠
 * 组里——而「刻度组」这个元素本身要先在元素树里展开「坐标轴」才找得到。
 * 用户得先理解 axes / xticks / yticks 三个内部对象的关系，才能改一件事。
 *
 * 本组件只负责**摆放与写入**；能力仍由 manifest 说了算：
 * `axis.has(prop)` 为假就整行不画，绝不摆一个「点了不生效」的控件。
 * 主刻度**没有** `major_visible` 字段，所以这里也不造一个——次刻度开关说的
 * 是「只要主刻度 / 主刻度 + 次刻度」，不是「主刻度开关」。
 *
 * 同一个组件被两处复用（`docs/ux/UX_CONSISTENCY_PASS.md`）：
 *   * 选中子图  → 两个轴都给，顶部出 X / Y 分段切换；
 *   * 选中刻度组 → 只给它自己那个轴，不出切换（切过去会写到另一个元素，
 *     而用户选的是这一个）。
 */

const tk = (key: string, values?: Record<string, unknown>) =>
  translate(`tick.${key}`, { ns: 'inspector', ...(values ?? {}) })

/** 一个轴的刻度写入面。由调用方按 host 元素组装（axes 页与刻度组页各一份） */
export interface TickAxisAdapter {
  axis: 'x' | 'y'
  has: (prop: string) => boolean
  fieldOf: (prop: string) => EditableField | undefined
  read: (prop: string) => unknown
  /** 离散写入（方向、次刻度开关）：一次点击 = 一条历史 + 一次渲染 */
  writeOnce: (prop: string, value: unknown) => void
  /** 连续写入（长度 / 宽度 scrub）：整轮一条历史 */
  write: (prop: string, value: unknown) => void
  beginGesture: () => void
  endGesture: () => void
  isOverridden: (prop: string) => boolean
  reset: (prop: string) => void
}

/** 卡片承接掉的属性——刻度组页的通用字段列表要把它们让出来，避免两套控件 */
export const TICK_CARD_PROPS = ['direction', 'minor_visible', 'length', 'width'] as const

const DIRECTIONS: TickDirection[] = ['in', 'out', 'inout']

export function TickTaskCard({
  axes,
  labelWidth = 72,
}: {
  /** 一个或两个轴；给两个时顶部出 X / Y 切换 */
  axes: TickAxisAdapter[]
  labelWidth?: number
}) {
  useTranslation('inspector')
  const [active, setActive] = useState<'x' | 'y'>(axes[0]?.axis ?? 'x')
  const cur = axes.find((a) => a.axis === active) ?? axes[0]
  if (!cur) return null

  // 这个轴一条能力都没有就整块不画（3D 轴的 direction / visible 被引擎摘掉了）
  const usable = TICK_CARD_PROPS.filter((p) => cur.has(p))
  if (!usable.length) return null

  const dirField = cur.fieldOf('direction')
  // 方向选项以 manifest 声明的为准；引擎将来加了第四档也不用改这里
  const dirOptions = (dirField?.options ?? DIRECTIONS).filter((o): o is TickDirection =>
    (DIRECTIONS as string[]).includes(o),
  )
  const length = cur.fieldOf('length')
  const width = cur.fieldOf('width')
  const minorOn = cur.read('minor_visible') === true

  return (
    <div className="flex flex-col gap-1.5">
      {axes.length > 1 && (
        <Segmented
          tone="quiet"
          className="w-full"
          ariaLabel={tk('axisSwitch')}
          value={active}
          onChange={setActive}
          items={axes.map((a) => ({ value: a.axis, label: tk(a.axis === 'x' ? 'xTicks' : 'yTicks') }))}
        />
      )}

      {cur.has('minor_visible') && (
        <Row
          label={labeledWithState(tk('minor'), cur.isOverridden('minor_visible'))}
          labelWidth={labelWidth}
        >
          <Toggle
            checked={minorOn}
            onChange={(v) => cur.writeOnce('minor_visible', v)}
            aria-label={tk('minorAria', { axis: tk(cur.axis === 'x' ? 'axisX' : 'axisY') })}
          />
          <span className="text-xs text-ink-3">{tk(minorOn ? 'minorOn' : 'minorOff')}</span>
          {cur.isOverridden('minor_visible') && (
            <ResetChip label={tk('minor')} onReset={() => cur.reset('minor_visible')} />
          )}
        </Row>
      )}

      {dirField && dirOptions.length > 0 && (
        <Row
          label={labeledWithState(tk('direction'), cur.isOverridden('direction'))}
          labelWidth={labelWidth}
        >
          <Segmented
            tone="quiet"
            className="min-w-0 flex-1"
            ariaLabel={tk('direction')}
            value={String(cur.read('direction') ?? 'out') as TickDirection}
            onChange={(v) => cur.writeOnce('direction', v)}
            items={dirOptions.map((o) => ({
              value: o,
              icon: <DirectionGlyph axis={cur.axis} direction={o} />,
              tip: tk(`dir.${o}`),
              ariaLabel: tk(`dir.${o}`),
            }))}
          />
          {cur.isOverridden('direction') && (
            <ResetChip label={tk('direction')} onReset={() => cur.reset('direction')} />
          )}
        </Row>
      )}

      {length && (
        <NumberRow
          label={tk('length')}
          field={length}
          axis={cur}
          prop="length"
          labelWidth={labelWidth}
        />
      )}
      {width && (
        <NumberRow
          label={tk('width')}
          field={width}
          axis={cur}
          prop="width"
          labelWidth={labelWidth}
        />
      )}
    </div>
  )
}

function NumberRow({
  label,
  field,
  axis,
  prop,
  labelWidth,
}: {
  label: string
  field: EditableField
  axis: TickAxisAdapter
  prop: string
  labelWidth: number
}) {
  return (
    <Row label={labeledWithState(label, axis.isOverridden(prop))} labelWidth={labelWidth}>
      <NumberField
        className="w-[74px] shrink-0"
        dataProp={prop}
        ariaLabel={label}
        value={Number(axis.read(prop) ?? 0)}
        min={field.min}
        max={field.max}
        step={field.step ?? 0.1}
        precision={2}
        suffix={field.unit}
        onChange={(v) => axis.write(prop, v)}
        onScrubStart={axis.beginGesture}
        onScrubEnd={axis.endGesture}
      />
      {axis.isOverridden(prop) && <ResetChip label={label} onReset={() => axis.reset(prop)} />}
    </Row>
  )
}

/**
 * 方向按钮的图形：一小段轴 + 一根朝对应方向的刻度。
 * **不只靠文字**——「朝内 / 朝外 / 内外」三个词在中英文里都容易看混，
 * 而这件事本来就是图形化的。选中态由 Segmented 统一给（底色 + 字重）。
 */
function DirectionGlyph({ axis, direction }: { axis: 'x' | 'y'; direction: TickDirection }) {
  // X 轴画一条横线（下边框），刻度上下伸；Y 轴画一条竖线（左边框），刻度左右伸。
  // 「内」= 朝坐标框里，对下边框就是往上；轴线本身要够实，否则三档只差
  // 「短线在线的哪一侧」，在 18px 里根本分不出来（实测截图上确实分不出）。
  const horizontal = axis === 'x'
  const L = 5
  const [t0, t1] = direction === 'in' ? [0, -L] : direction === 'inout' ? [-L, L] : [0, L]
  const marks = [5, 9, 13].map((p) =>
    horizontal ? `M${p} ${9 + t0} L${p} ${9 + t1}` : `M${9 - t0} ${p} L${9 - t1} ${p}`,
  )
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden className="shrink-0">
      <path
        d={horizontal ? 'M2 9 H16' : 'M9 2 V16'}
        stroke="currentColor"
        strokeWidth="1.3"
        fill="none"
      />
      <path
        d={marks.join(' ')}
        stroke="currentColor"
        strokeWidth="1.3"
        strokeOpacity="0.75"
        fill="none"
      />
    </svg>
  )
}
