import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import type { EditableField } from '@/lib/api'
import { Row } from '../../ui/Field'
import { NumberField } from '../../ui/Input'
import { Segmented } from '../../ui/Segmented'
import { Toggle } from '../../ui/Toggle'
import {
  axisChoice,
  axisChoicePlan,
  sidesOfAxis,
  type AxesTickModel,
  type AxisTickChoice,
  type SidePlan,
  type TickDirection,
} from '@/lib/tickSides'
import type { TickAxis } from '../tickAdapter'
import { ResetChip, labeledWithState } from './textRows'

/**
 * 刻度任务卡：**「刻度朝哪、多长多粗、要不要次刻度」在同一处完成**。
 *
 * 修改前这三件事分散在三个地方：四边开关在子图页的状态图上，方向与长宽在
 * 「刻度组」元素的「刻度线」折叠组里，次刻度在同一个元素的「刻度定位」折叠
 * 组里——而「刻度组」这个元素本身要先在元素树里展开「坐标轴」才找得到。
 * 用户得先理解 axes / xticks / yticks 三个内部对象的关系，才能改一件事。
 *
 * **每组设置只有一处控件**（审计 T13）：方向只有这里的一组分段；「在哪几条边
 * 显示」只在示意图上点（`TickAndSpineDiagram` 的内 / 外两带，键盘可达）；
 * 这里不再摆一排与示意图重复的开关。次刻度关着时它的长度 / 线宽 / 方式 /
 * 格式一并收起——摆一排此刻写了不生效的控件比藏起来更不诚实；用户改过的
 * 照样显示（不因折叠而不可发现）。
 *
 * 本组件只负责**摆放与写入**；能力仍由 manifest 说了算：
 * `axis.has(prop)` 为假就整行不画，绝不摆一个「点了不生效」的控件。3D 的
 * Z 轴由此天然只剩长度 / 宽度 / 次刻度（引擎摘掉了 direction 与 minor_*）。
 * 主刻度**没有** `major_visible` 字段，所以这里也不造一个——次刻度开关说的
 * 是「只要主刻度 / 主刻度 + 次刻度」，不是「主刻度开关」。
 *
 * 同一个组件被两处复用：
 *   * 选中子图  → 两个轴都给，顶部出 X / Y 分段切换；
 *   * 选中刻度组 → 只给它自己那个轴，不出切换（切过去会写到另一个元素，
 *     而用户选的是这一个）。
 */

const tk = (key: string, values?: Record<string, unknown>) =>
  translate(`tick.${key}`, { ns: 'inspector', ...(values ?? {}) })

/** 一个轴的刻度写入面。由调用方按 host 元素组装（axes 页与刻度组页各一份） */
export interface TickAxisAdapter {
  axis: TickAxis
  has: (prop: string) => boolean
  fieldOf: (prop: string) => EditableField | undefined
  read: (prop: string) => unknown
  /**
   * 这个字段此刻该不该显示（展示注册表的 `visibleWhen` + 用户改过的必须能看到）
   * ——次刻度关着时长度 / 线宽收起，与通用列表同一条判据（`registry.fieldVisible`）
   */
  visible: (prop: string) => boolean
  /** 离散写入（方向、次刻度开关）：一次点击 = 一条历史 + 一次渲染 */
  writeOnce: (prop: string, value: unknown) => void
  /** 连续写入（长度 / 宽度 scrub）：整轮一条历史 */
  write: (prop: string, value: unknown) => void
  beginGesture: () => void
  endGesture: () => void
  isOverridden: (prop: string) => boolean
  reset: (prop: string) => void
  /** 刻度元素的 gid（`data-gid` 锚点；问题面板定位到方向字段要靠它） */
  gid?: string
}

/** 卡片承接掉的属性——刻度组页的通用字段列表要把它们让出来，避免两套控件 */
export const TICK_CARD_PROPS = [
  'direction',
  'minor_visible',
  'length',
  'width',
  'minor_length',
  'minor_width',
] as const

const DIRECTIONS: TickDirection[] = ['in', 'out', 'inout']
/** 方向档的显示顺序：三个真方向 + 「隐藏」（两边都不显示刻度线的派生态） */
const CHOICES: AxisTickChoice[] = ['in', 'out', 'inout', 'hidden']

const AXIS_NAME: Record<TickAxis, string> = { x: 'axisX', y: 'axisY', z: 'axisZ' }
const AXIS_TAB: Record<TickAxis, string> = { x: 'xTicks', y: 'yTicks', z: 'zTicks' }

export function TickTaskCard({
  axes,
  labelWidth = 72,
  model = null,
  applyPlan,
  placement,
  minorExtra,
}: {
  /** 一个或多个轴；给多个时顶部出 X / Y 切换 */
  axes: TickAxisAdapter[]
  labelWidth?: number
  /**
   * 宿主子图的四边刻度模型（`readAxesTickModel`）。有它方向档多出「隐藏」
   * ——与画布命中区 / 示意图同一份计划函数。
   */
  model?: AxesTickModel | null
  applyPlan?: (plan: SidePlan) => void
  /** 主刻度的位置字段（方式 / 间距 / 固定值）：排在长宽之后、次刻度之前 */
  placement?: ReactNode
  /** 次刻度的从属字段（方式 / 间距 / 格式）：跟在次刻度的长宽后面 */
  minorExtra?: ReactNode
}) {
  useTranslation('inspector')
  const [active, setActive] = useState<TickAxis>(axes[0]?.axis ?? 'x')
  const cur = axes.find((a) => a.axis === active) ?? axes[0]
  if (!cur) return null

  // 这个轴一条能力都没有就整块不画
  const usable = TICK_CARD_PROPS.filter((p) => cur.has(p))
  if (!usable.length) return null

  const dirField = cur.fieldOf('direction')
  // 方向选项以 manifest 声明的为准；引擎将来加了第四档也不用改这里
  const dirOptions = (dirField?.options ?? DIRECTIONS).filter((o): o is TickDirection =>
    (DIRECTIONS as string[]).includes(o),
  )
  const length = cur.fieldOf('length')
  const width = cur.fieldOf('width')
  const minorLength = cur.fieldOf('minor_length')
  const minorWidth = cur.fieldOf('minor_width')
  const minorOn = cur.read('minor_visible') === true
  // 四边模型在、且这条轴在模型里：方向档带「隐藏」，写入走计划（一次 commit
  // 可能同时动方向与两边显隐）；不在（Z 轴、没发 spines 的轴）：退回只写 direction 的三档
  const modelSides =
    model && cur.axis !== 'z' ? sidesOfAxis(cur.axis).filter((sd) => model.sides[sd]) : []
  const zoned = !!model && !!applyPlan && modelSides.length > 0
  const choice: AxisTickChoice = zoned
    ? (axisChoice(model!, cur.axis as 'x' | 'y') ?? 'out')
    : (String(cur.read('direction') ?? 'out') as TickDirection)
  const choices: AxisTickChoice[] = zoned ? CHOICES : dirOptions
  const pickChoice = (v: AxisTickChoice) => {
    if (zoned) {
      const plan = axisChoicePlan(model!, cur.axis as 'x' | 'y', v)
      if (plan) applyPlan!(plan)
    } else if (v !== 'hidden') {
      cur.writeOnce('direction', v)
    }
  }

  return (
    <div className="flex flex-col gap-1.5" data-tick-card={cur.axis}>
      {axes.length > 1 && (
        <Segmented
          tone="quiet"
          className="w-full"
          ariaLabel={tk('axisSwitch')}
          value={active}
          onChange={setActive}
          items={axes.map((a) => ({ value: a.axis, label: tk(AXIS_TAB[a.axis]) }))}
        />
      )}

      {dirField && dirOptions.length > 0 && (
        /* `data-prop="direction"` 是问题面板的定位锚点（`tick-direction` 规则
           报的就是这个字段）：卡把通用行接管了，锚点也得跟着搬过来 */
        <div data-prop="direction" data-gid={cur.gid}>
          <Row
            label={labeledWithState(tk('direction'), cur.isOverridden('direction'))}
            labelWidth={labelWidth}
          >
            <Segmented
              tone="quiet"
              className="min-w-0 flex-1"
              ariaLabel={tk('direction')}
              value={choice}
              onChange={pickChoice}
              items={choices.map((o) => ({
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
        </div>
      )}

      {/* 长度与宽度同一行：行首标签给第一项，其余项用内联小标签跟在后面 */}
      {(length || width) && (
        <NumberPairRow
          axis={cur}
          labelWidth={labelWidth}
          items={[
            ...(length ? [{ label: tk('length'), field: length, prop: 'length' }] : []),
            ...(width ? [{ label: tk('width'), field: width, prop: 'width' }] : []),
          ]}
        />
      )}

      {placement}

      {cur.has('minor_visible') && (
        <div data-prop="minor_visible" data-gid={cur.gid}>
          <Row
            label={labeledWithState(tk('minor'), cur.isOverridden('minor_visible'))}
            labelWidth={labelWidth}
          >
            <Toggle
              checked={minorOn}
              onChange={(v) => cur.writeOnce('minor_visible', v)}
              aria-label={tk('minorAria', { axis: tk(AXIS_NAME[cur.axis]) })}
            />
            <span className="text-xs text-ink-3">{tk(minorOn ? 'minorOn' : 'minorOff')}</span>
            {cur.isOverridden('minor_visible') && (
              <ResetChip label={tk('minor')} onReset={() => cur.reset('minor_visible')} />
            )}
          </Row>
        </div>
      )}
      {/* 次刻度自己的长度 / 线宽：主刻度那两条只动主刻度。**关着时收起**
          ——值虽然是「开了会是多少」，但此刻写了看不见；改过的照样显示。
          收不收由展示注册表的 visibleWhen 说（`cur.visible`），这里不另判 */}
      {((minorLength && cur.visible('minor_length')) ||
        (minorWidth && cur.visible('minor_width'))) && (
        <NumberPairRow
          axis={cur}
          labelWidth={labelWidth}
          items={[
            ...(minorLength && cur.visible('minor_length')
              ? [{ label: tk('minorLength'), field: minorLength, prop: 'minor_length' }]
              : []),
            ...(minorWidth && cur.visible('minor_width')
              ? [
                  {
                    label: tk('minorWidth'),
                    // 行首已经写了「次刻度」，内联标签只留「宽度」——与主刻度那行
                    // 同字同宽，两行的宽度输入框左边缘才对得齐
                    inline: tk('width'),
                    field: minorWidth,
                    prop: 'minor_width',
                  },
                ]
              : []),
          ]}
        />
      )}
      {minorExtra}
    </div>
  )
}

interface NumberItem {
  /** 完整名称：无障碍名与重置芯片用它 */
  label: string
  /** 内联小标签的显示文案；缺省用 label */
  inline?: string
  field: EditableField
  prop: string
}

/**
 * 几个数值字段挤在同一行：第一项的标签走 Row 的行首标签，其余项用内联小标签
 * 跟在自己的输入框前面。每个字段各自保留 `data-prop` 锚点、覆盖态与重置芯片。
 */
function NumberPairRow({
  items,
  axis,
  labelWidth,
}: {
  items: NumberItem[]
  axis: TickAxisAdapter
  labelWidth: number
}) {
  const [head, ...rest] = items
  if (!head) return null
  return (
    <Row
      label={labeledWithState(head.label, axis.isOverridden(head.prop))}
      labelWidth={labelWidth}
      align="start"
    >
      {/* 行内两端对齐：首项贴行首，后续「标签 + 输入框」成组贴行尾，
          中间空隙由 justify-between 撑开、至少保留 gap-4。
          放不下时（属性栏 320px：两个 96px 框 + 内联标签 > 控件列）后续组**折到下一行**，
          而不是把宽度框裁掉右半边（2026-09-12 critique P2，实测溢出 26px）；
          标签按 start 对齐到第一行控件的中线 */}
      <div className="flex min-w-0 flex-1 flex-wrap items-center justify-between gap-x-3 gap-y-1.5">
        <NumberCell item={head} axis={axis} />
        {rest.map((item) => (
          <span key={item.prop} className="flex shrink-0 items-center gap-1.5">
            <span className="min-w-10 shrink-0 text-xs text-ink-3">
              {labeledWithState(item.inline ?? item.label, axis.isOverridden(item.prop))}
            </span>
            <NumberCell item={item} axis={axis} />
          </span>
        ))}
      </div>
    </Row>
  )
}

function NumberCell({ item, axis }: { item: NumberItem; axis: TickAxisAdapter }) {
  const { label, field, prop } = item
  return (
    <span className="flex shrink-0 items-center gap-1" data-prop={prop} data-gid={axis.gid}>
      <NumberField
        // 定宽 88px、框吃满：单位在框里，数字位不会被挤到只剩两位（以前实测 26px）。
        // 88 而不是 96：标签列 88 之后控件列在 360px 下只剩 240，两个框 + 内联标签 +
        // 间距要在一行里放下（88 + 12 + 40 + 6 + 88 = 234）；320px 时才折行
        fill
        className="w-22 shrink-0"
        dataProp={prop}
        ariaLabel={label}
        value={Number(axis.read(prop) ?? 0)}
        min={field.min}
        max={field.max}
        step={field.step ?? 0.1}
        precision={2}
        unit={field.unit}
        onChange={(v) => axis.write(prop, v)}
        onScrubStart={axis.beginGesture}
        onScrubEnd={axis.endGesture}
      />
      {axis.isOverridden(prop) && <ResetChip label={label} onReset={() => axis.reset(prop)} />}
    </span>
  )
}

/**
 * 方向按钮的图形：一小段轴 + 一根朝对应方向的刻度。
 * **不只靠文字**——「朝内 / 朝外 / 内外」三个词在中英文里都容易看混，
 * 而这件事本来就是图形化的。选中态由 Segmented 统一给（底色 + 字重）。
 */
function DirectionGlyph({ axis, direction }: { axis: TickAxis; direction: AxisTickChoice }) {
  // X 轴画一条横线（下边框），刻度上下伸；Y / Z 轴画一条竖线（左边框），刻度左右伸。
  // 「内」= 朝坐标框里，对下边框就是往上；轴线本身要够实，否则三档只差
  // 「短线在线的哪一侧」，在 18px 里根本分不出来（实测截图上确实分不出）。
  // 「隐藏」= 只有轴线、没有短线。
  const horizontal = axis === 'x'
  const L = 5
  const [t0, t1] = direction === 'in' ? [0, -L] : direction === 'inout' ? [-L, L] : [0, L]
  const marks =
    direction === 'hidden'
      ? []
      : [5, 9, 13].map((p) =>
          horizontal ? `M${p} ${9 + t0} L${p} ${9 + t1}` : `M${9 - t0} ${p} L${9 - t1} ${p}`,
        )
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden className="shrink-0">
      <path
        d={horizontal ? 'M2 9 H16' : 'M9 2 V16'}
        stroke="currentColor"
        strokeWidth="1.5"
        fill="none"
      />
      {marks.length > 0 && (
        <path
          d={marks.join(' ')}
          stroke="currentColor"
          strokeWidth="1.5"
          strokeOpacity="0.75"
          fill="none"
        />
      )}
    </svg>
  )
}
