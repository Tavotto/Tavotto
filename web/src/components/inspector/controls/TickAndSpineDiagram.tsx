import { useState, type KeyboardEvent } from 'react'
import { t as translate } from '@/i18n'
import { listJoin } from '@/i18n/format'
import {
  toggleSidePlan,
  type AxesTickModel,
  type SidePlan,
  type SpineSide,
  type TickDirection,
} from '@/lib/tickSides'
import { cn } from '@/lib/utils'
import { Tip } from '../../ui/Tooltip'

/**
 * 刻度与边框状态图：一张可点击的小坐标轴。
 *
 * 四条边各画成边线本体（spine_<side> 开关）+ 边上的刻度短线。刻度短线的
 * 命中按**视觉语义**分两个带（Prompt 16）：
 *
 *   边线内侧（框里）的带 —— 这一边的**向内**刻度
 *   边线外侧（框外）的带 —— 这一边的**向外**刻度
 *   边线本身             —— 边框开关
 *
 * 旧实现把刻度命中区写死在框外：刻度朝内时短线画在框里，点它却什么都不
 * 发生，得去点框外那块空白——就是「刻度朝内却必须点击图框外侧」那条反直觉
 * 命中。现在两个带各是一个 switch（aria-checked = 这一方向此刻开不开），
 * 点击走 `toggleSidePlan`——与画布上的边框命中区、刻度卡的方向档同一份
 * 计划函数，三处永远同源。方向在 matplotlib 里是整条轴的，所以 tooltip 会
 * 说出连带改到的同轴另一边。
 *
 * 网格（grid_x / grid_y）是图下方的两个开关，状态同时预览在图内。开关状态
 * 用「实线 vs 虚线 + 透明度」表达，不只靠颜色。
 *
 * 纯展示 + 回调组件：字段在不在、当前值、写入全部由调用方（manifest 与
 * ElementWriter）决定；manifest 没有的部分整块不画。`model` 里没有的边
 * （引擎没发那条轴的刻度元素）退回单个 `ticks_<side>` 开关，命中带盖住
 * 内外两侧——此时方向未知，画成朝外只是 matplotlib 的默认。
 */

/** 一个轴当前的刻度形态。字段缺席时由调用方给缺省（out / 无次刻度） */
export interface AxisTickState {
  direction: TickDirection
  minor: boolean
}

export type { TickDirection }

export interface TickSpineAdapter {
  has: (prop: string) => boolean
  read: (prop: string) => unknown
  /** 一次点击 = 一条历史 + 一次渲染（writeOnce 语义） */
  toggle: (prop: string, next: boolean) => void
  labelOf: (prop: string) => string
  isOverridden: (prop: string) => boolean
  /** 单独恢复一条到脚本值（clearOverride） */
  reset: (prop: string) => void
  /**
   * 该轴刻度的真实朝向与次刻度状态。**不给就按 out / 无次刻度画**——
   * 旧实现把刻度写死在框外侧，用户把 direction 改成 in 之后示意图纹丝不动，
   * 于是这张图说的和画布上发生的是两回事。上下边读 x、左右边读 y。
   */
  axisState?: (axis: 'x' | 'y') => AxisTickState
  /** 四边刻度模型（`readAxesTickModel`）：有它内外两个带才各自可点 */
  model?: AxesTickModel | null
  /** 一次点击 = 一份计划 = 一条历史（`applyTickSidePlan`） */
  applyPlan?: (plan: SidePlan) => void
}

export const TICK_SPINE_PROPS = [
  'ticks_bottom', 'ticks_top', 'ticks_left', 'ticks_right',
  'spine_bottom', 'spine_top', 'spine_left', 'spine_right',
  'grid_x', 'grid_y',
] as const

type Side = 'top' | 'bottom' | 'left' | 'right'
const SIDES: Side[] = ['top', 'right', 'bottom', 'left']

/** 中央坐标框（viewBox 148×104） */
const BOX = { x: 26, y: 14, w: 96, h: 68 }

const spinePath = (side: Side): string => {
  const { x, y, w, h } = BOX
  switch (side) {
    case 'top': return `M${x} ${y} H${x + w}`
    case 'bottom': return `M${x} ${y + h} H${x + w}`
    case 'left': return `M${x} ${y} V${y + h}`
    case 'right': return `M${x + w} ${y} V${y + h}`
  }
}

/** 边所属的轴：上下是 X，左右是 Y（与 matplotlib 的 tick_params(axis=) 一致） */
export const axisOfSide = (side: Side): 'x' | 'y' =>
  side === 'top' || side === 'bottom' ? 'x' : 'y'

const MAJOR_LEN = 6
/** 次刻度明显更短——两者长度必须一眼可辨，否则「开了次刻度」看不出来 */
const MINOR_LEN = 3

/**
 * 一根刻度短线。`direction` 决定它往哪边伸：
 *   out   —— 框外（matplotlib 默认）
 *   in    —— 框内
 *   inout —— 两侧各伸一半长度
 *
 * 返回的是相对边框的一段路径，坐标已换算到 viewBox。
 */
const tickAt = (side: Side, at: number, len: number, direction: TickDirection): string => {
  const { x, y, w, h } = BOX
  // 「外」的方向：上边朝上、下边朝下、左边朝左、右边朝右
  const outward = side === 'top' || side === 'left' ? -1 : 1
  const [t0, t1] =
    direction === 'in' ? [0, -len] : direction === 'inout' ? [-len, len] : [0, len]
  const edge = side === 'top' ? y : side === 'bottom' ? y + h : side === 'left' ? x : x + w
  const a = edge + outward * t0
  const b = edge + outward * t1
  return side === 'top' || side === 'bottom'
    ? `M${at} ${a} L${at} ${b}`
    : `M${a} ${at} L${b} ${at}`
}

/** 主刻度：三根，落在 25% / 50% / 75% */
const majorPositions = (side: Side): number[] => {
  const { x, y, w, h } = BOX
  return side === 'top' || side === 'bottom'
    ? [x + w * 0.25, x + w * 0.5, x + w * 0.75]
    : [y + h * 0.25, y + h * 0.5, y + h * 0.75]
}

/** 次刻度：落在主刻度之间（12.5% / 37.5% / 62.5% / 87.5%） */
const minorPositions = (side: Side): number[] => {
  const { x, y, w, h } = BOX
  const fr = [0.125, 0.375, 0.625, 0.875]
  return side === 'top' || side === 'bottom'
    ? fr.map((f) => x + w * f)
    : fr.map((f) => y + h * f)
}

const tickMarks = (side: Side, direction: TickDirection): string =>
  majorPositions(side)
    .map((p) => tickAt(side, p, MAJOR_LEN, direction))
    .join(' ')

const minorTickMarks = (side: Side, direction: TickDirection): string =>
  minorPositions(side)
    .map((p) => tickAt(side, p, MINOR_LEN, direction))
    .join(' ')

/**
 * 命中区（viewBox 单位）。边线两侧各留 `NEUTRAL` 不切刻度（那是边框开关），
 * 往里 / 往外各一条 `BAND` 宽的带：`inner` 在框里，`outer` 在框外——与画布
 * 上 `lib/tickSides.spineZoneAt` 的三带同构，只是这里是固定尺寸的示意图。
 * `ticks` 是退化形态（没有方向信息时）：内外两带合成一块。
 */
const NEUTRAL = 3.5
const BAND = 10.5
const hitRect = (side: Side, kind: 'spine' | 'ticks' | 'inner' | 'outer') => {
  const { x, y, w, h } = BOX
  // 每条边「向外」的符号：上 / 左为负，下 / 右为正
  const outward = side === 'top' || side === 'left' ? -1 : 1
  const edge = side === 'top' ? y : side === 'bottom' ? y + h : side === 'left' ? x : x + w
  let a: number
  let b: number
  if (kind === 'spine') {
    a = edge - NEUTRAL
    b = edge + NEUTRAL
  } else if (kind === 'ticks') {
    a = edge - NEUTRAL - BAND
    b = edge + NEUTRAL + BAND
  } else {
    const sign = kind === 'outer' ? outward : -outward
    a = edge + sign * NEUTRAL
    b = edge + sign * (NEUTRAL + BAND)
  }
  const lo = Math.min(a, b)
  const t = Math.abs(b - a)
  return side === 'top' || side === 'bottom'
    ? { x, y: lo, width: w, height: t }
    : { x: lo, y, width: t, height: h }
}

const ctl = (key: string, values?: Record<string, unknown>) =>
  translate(`control.${key}`, { ns: 'inspector', ...(values ?? {}) })

function SvgSwitch({
  prop,
  on,
  adapter,
  children,
  hit,
}: {
  prop: string
  on: boolean
  adapter: TickSpineAdapter
  children: React.ReactNode
  hit: { x: number; y: number; width: number; height: number }
}) {
  const [focused, setFocused] = useState(false)
  const name = ctl(on ? 'switchOn' : 'switchOff', { label: adapter.labelOf(prop) })
  const onKey = (e: KeyboardEvent) => {
    if (e.key !== 'Enter' && e.key !== ' ') return
    e.preventDefault()
    adapter.toggle(prop, !on)
  }
  return (
    <Tip label={adapter.isOverridden(prop) ? `${name} · ${translate('element.modified', { ns: 'inspector' })}` : name}>
      <g
        role="switch"
        aria-checked={on}
        aria-label={adapter.labelOf(prop)}
        tabIndex={0}
        className="cursor-pointer outline-none"
        onClick={() => adapter.toggle(prop, !on)}
        onKeyDown={onKey}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
      >
        <rect {...hit} fill="transparent" />
        {focused && (
          <rect
            {...hit}
            fill="none"
            stroke="var(--color-accent)"
            strokeWidth="1"
            rx="2"
          />
        )}
        {children}
      </g>
    </Tip>
  )
}

/** 一根方向的刻度短线：只画向内或只画向外那一半（两个带各画各的） */
const halfMarks = (side: Side, half: 'in' | 'out', len: number, minor: boolean): string =>
  (minor ? minorPositions(side) : majorPositions(side))
    .map((p) => tickAt(side, p, len, half))
    .join(' ')

/** 内 / 外一个带的开关：aria-checked = 这一方向此刻开不开，点击走同一份计划 */
function ZoneSwitch({
  side,
  zone,
  adapter,
  model,
  minor,
}: {
  side: Side
  zone: 'inner' | 'outer'
  adapter: TickSpineAdapter
  model: AxesTickModel
  minor: boolean
}) {
  const [focused, setFocused] = useState(false)
  const state = model.sides[side]!
  const dir = zone === 'inner' ? 'in' : 'out'
  const on = dir === 'in' ? state.inward : state.outward
  const plan = toggleSidePlan(model, side, zone)
  const name = ctl('zoneAria', {
    side: translate(`tick.side.${side}`, { ns: 'inspector' }),
    dir: translate(`tick.dir.${dir}`, { ns: 'inspector' }),
  })
  const coupled = plan?.effect.coupled.length
    ? translate('spineZone.coupled', {
        ns: 'workspace',
        sides: listJoin(
          plan.effect.coupled.map((sd) => translate(`tick.side.${sd}`, { ns: 'inspector' })),
        ),
      })
    : ''
  const tip = `${ctl(on ? 'switchOn' : 'switchOff', { label: name })}${coupled ? ` ${coupled}` : ''}`
  const fire = () => {
    if (plan) adapter.applyPlan?.(plan)
  }
  const onKey = (e: KeyboardEvent) => {
    if (e.key !== 'Enter' && e.key !== ' ') return
    e.preventDefault()
    fire()
  }
  const hit = hitRect(side, zone)
  const sideOn = state.visible
  return (
    <Tip label={tip}>
      <g
        role="switch"
        aria-checked={on}
        aria-label={name}
        tabIndex={0}
        className="cursor-pointer outline-none"
        data-tick-zone={`${side}:${zone}`}
        data-tick-coupled={plan?.effect.coupled.length ? plan.effect.coupled.join(',') : undefined}
        onClick={fire}
        onKeyDown={onKey}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
      >
        <rect {...hit} fill="transparent" />
        {focused && (
          <rect {...hit} fill="none" stroke="var(--color-accent)" strokeWidth="1" rx="2" />
        )}
        {/* 开着：实线；关着：这一半画成浅虚线占位，让用户看得出「这里能点出一排刻度」 */}
        <path
          d={halfMarks(side, dir, MAJOR_LEN, false)}
          fill="none"
          stroke="currentColor"
          strokeWidth={on ? 1.6 : 1}
          strokeOpacity={on ? 1 : sideOn ? 0.22 : 0.3}
          strokeDasharray={on ? undefined : '1.5 1.5'}
          data-tick-major={side}
          data-tick-half={dir}
          data-tick-on={on ? 'true' : 'false'}
        />
        {minor && on && (
          <path
            d={halfMarks(side, dir, MINOR_LEN, true)}
            fill="none"
            stroke="currentColor"
            strokeWidth={1.1}
            strokeOpacity={0.85}
            data-tick-minor={side}
            data-tick-half={dir}
          />
        )}
      </g>
    </Tip>
  )
}

export function TickAndSpineDiagram({ adapter }: { adapter: TickSpineAdapter }) {
  const sideProps = SIDES.flatMap((s) => [`spine_${s}`, `ticks_${s}`])
  if (!sideProps.some((p) => adapter.has(p))) return null

  const on = (p: string) => adapter.read(p) === true
  const gridX = adapter.has('grid_x') && on('grid_x')
  const gridY = adapter.has('grid_y') && on('grid_y')
  // 引擎没给 direction / minor_visible（3D 轴、老引擎）时按 matplotlib 默认画
  const stateOf = (side: Side): AxisTickState =>
    adapter.axisState?.(axisOfSide(side)) ?? { direction: 'out', minor: false }
  const modified = TICK_SPINE_PROPS.filter((p) => adapter.has(p) && adapter.isOverridden(p))
  const model = adapter.model ?? null
  /** 这条边有方向信息（模型里有它）→ 内外两带各自可点；否则退回单开关 */
  const zoned = (side: Side): side is SpineSide =>
    !!model && !!model.sides[side] && !!adapter.applyPlan

  return (
    <div className="flex flex-col gap-1">
      <svg
        viewBox="0 0 148 104"
        className="w-full max-w-[220px] self-center text-ink"
        aria-label={ctl('tickSpineDiagram')}
        role="group"
      >
        {/* 网格预览（非交互，开关在下方） */}
        {gridX && (
          <path
            d={`M${BOX.x + BOX.w * 0.25} ${BOX.y} V${BOX.y + BOX.h} M${BOX.x + BOX.w * 0.5} ${BOX.y} V${BOX.y + BOX.h} M${BOX.x + BOX.w * 0.75} ${BOX.y} V${BOX.y + BOX.h}`}
            stroke="currentColor" strokeOpacity="0.18" strokeWidth="0.8" aria-hidden
          />
        )}
        {gridY && (
          <path
            d={`M${BOX.x} ${BOX.y + BOX.h * 0.25} H${BOX.x + BOX.w} M${BOX.x} ${BOX.y + BOX.h * 0.5} H${BOX.x + BOX.w} M${BOX.x} ${BOX.y + BOX.h * 0.75} H${BOX.x + BOX.w}`}
            stroke="currentColor" strokeOpacity="0.18" strokeWidth="0.8" aria-hidden
          />
        )}
        {SIDES.map((side) => (
          <g key={side}>
            {adapter.has(`spine_${side}`) && (
              <SvgSwitch
                prop={`spine_${side}`}
                on={on(`spine_${side}`)}
                adapter={adapter}
                hit={hitRect(side, 'spine')}
              >
                <path
                  d={spinePath(side)}
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={on(`spine_${side}`) ? 2 : 1}
                  strokeOpacity={on(`spine_${side}`) ? 1 : 0.3}
                  strokeDasharray={on(`spine_${side}`) ? undefined : '3 3'}
                />
              </SvgSwitch>
            )}
            {adapter.has(`ticks_${side}`) && zoned(side) && (
              <>
                <ZoneSwitch
                  side={side}
                  zone="inner"
                  adapter={adapter}
                  model={model!}
                  minor={stateOf(side).minor}
                />
                <ZoneSwitch
                  side={side}
                  zone="outer"
                  adapter={adapter}
                  model={model!}
                  minor={stateOf(side).minor}
                />
              </>
            )}
            {adapter.has(`ticks_${side}`) && !zoned(side) && (
              <SvgSwitch
                prop={`ticks_${side}`}
                on={on(`ticks_${side}`)}
                adapter={adapter}
                hit={hitRect(side, 'ticks')}
              >
                {/* 主刻度与次刻度都在同一个开关里：这条边一关，两者一起变成
                    关闭样式——「关了但次刻度还亮着」是自相矛盾的状态 */}
                <path
                  d={tickMarks(side, stateOf(side).direction)}
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={on(`ticks_${side}`) ? 1.6 : 1}
                  strokeOpacity={on(`ticks_${side}`) ? 1 : 0.3}
                  strokeDasharray={on(`ticks_${side}`) ? undefined : '1.5 1.5'}
                  data-tick-major={side}
                  data-tick-direction={stateOf(side).direction}
                />
                {stateOf(side).minor && (
                  <path
                    d={minorTickMarks(side, stateOf(side).direction)}
                    fill="none"
                    stroke="currentColor"
                    strokeWidth={on(`ticks_${side}`) ? 1.1 : 0.9}
                    strokeOpacity={on(`ticks_${side}`) ? 0.85 : 0.3}
                    strokeDasharray={on(`ticks_${side}`) ? undefined : '1.5 1.5'}
                    data-tick-minor={side}
                  />
                )}
              </SvgSwitch>
            )}
          </g>
        ))}
      </svg>

      {(adapter.has('grid_x') || adapter.has('grid_y')) && (
        <div className="flex gap-1.5">
          {(['grid_x', 'grid_y'] as const).map((p) =>
            adapter.has(p) ? (
              <button
                key={p}
                type="button"
                role="switch"
                aria-checked={on(p)}
                onClick={() => adapter.toggle(p, !on(p))}
                className={cn(
                  'flex h-6 flex-1 items-center justify-center gap-1 rounded-sm border text-xs outline-none transition-colors',
                  'focus-visible:focus-ring',
                  on(p)
                    ? 'border-accent bg-accent-subtle font-medium text-accent'
                    : 'border-border text-ink-2 hover:border-border-strong hover:text-ink',
                )}
              >
                <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden>
                  {p === 'grid_x' ? (
                    <path d="M4 1 V11 M8 1 V11" stroke="currentColor" strokeWidth="1" fill="none" />
                  ) : (
                    <path d="M1 4 H11 M1 8 H11" stroke="currentColor" strokeWidth="1" fill="none" />
                  )}
                </svg>
                {adapter.labelOf(p)}
              </button>
            ) : null,
          )}
        </div>
      )}

      {/* 已修改的边逐条列出，点 × 单独恢复到脚本——折叠进图形的属性也保有
          「一眼可辨来源 + 单项恢复」这两条契约 */}
      {modified.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {modified.map((p) => (
            <button
              key={p}
              type="button"
              onClick={() => adapter.reset(p)}
              aria-label={ctl('resetSide', { label: adapter.labelOf(p) })}
              className={cn(
                'flex h-5 items-center gap-1 rounded-sm bg-accent-subtle px-1.5 text-xs text-accent',
                'outline-none transition-colors hover:bg-accent/15 focus-visible:focus-ring',
              )}
            >
              <span aria-hidden className="h-1 w-1 rounded-full bg-accent" />
              {adapter.labelOf(p)}
              <span aria-hidden>×</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
