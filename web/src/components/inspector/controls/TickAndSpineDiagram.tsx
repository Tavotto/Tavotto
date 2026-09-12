import type { KeyboardEvent } from 'react'
import { RotateCcw } from 'lucide-react'
import { ICON_SIZE } from '@/components/ui/Icon'
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
  /**
   * 把示意图承接的全部已修改字段一次恢复到脚本（一条历史）。**恢复动作统一**
   * （审计 T13）：每条边各出一枚「× 恢复」chip 时，示意图下面会长出一排与
   * 图上状态重复表达的标签；改过哪条边由图上那条边自己说（accent 色 +
   * tooltip 里的「已修改」）。
   */
  resetAll: () => void
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

/** 中央坐标框（viewBox 220×148）：四周留出内外两条命中带的余量 */
const BOX = { x: 38, y: 22, w: 144, h: 100 }

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

const MAJOR_LEN = 8
/** 次刻度明显更短——两者长度必须一眼可辨，否则「开了次刻度」看不出来 */
const MINOR_LEN = 4

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
const NEUTRAL = 4
const BAND = 13
/** 命中带沿边长方向两端各缩进这么多：四个角不互相叠压，也不误触相邻边 */
const INSET = 10

type Rect = { x: number; y: number; width: number; height: number }

const hitRect = (side: Side, kind: 'spine' | 'ticks' | 'inner' | 'outer'): Rect => {
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
    ? { x: x + INSET, y: lo, width: w - INSET * 2, height: t }
    : { x: lo, y: y + INSET, width: t, height: h - INSET * 2 }
}

/**
 * 悬停 / 聚焦时那圈淡蓝底的几何：**只贴着这个开关真正画出来的线**，比命中区
 * 紧得多——命中区为了好点要往外铺 13，底却不能跟着铺，否则一块大蓝底同时罩住
 * 框里框外，看不出「点的是向外那一半」。刻度带的底从边线外 HALO_GAP 起、到
 * 刻度尖端外 HALO_PAD 止；退化的单开关按它此刻画的方向取外侧 / 内侧 / 两侧。
 */
const HALO_GAP = 1
const HALO_PAD = 2.5
const haloRect = (
  side: Side,
  kind: 'spine' | 'ticks' | 'inner' | 'outer',
  direction: TickDirection = 'out',
): Rect => {
  const { x, y, w, h } = BOX
  const outward = side === 'top' || side === 'left' ? -1 : 1
  const edge = side === 'top' ? y : side === 'bottom' ? y + h : side === 'left' ? x : x + w
  let a: number
  let b: number
  if (kind === 'spine') {
    a = edge - HALO_PAD
    b = edge + HALO_PAD
  } else {
    const dir: TickDirection = kind === 'inner' ? 'in' : kind === 'outer' ? 'out' : direction
    const reach = MAJOR_LEN + HALO_PAD
    a = dir === 'out' ? edge + outward * HALO_GAP : edge - outward * reach
    b = dir === 'in' ? edge - outward * HALO_GAP : edge + outward * reach
  }
  const lo = Math.min(a, b)
  const t = Math.abs(b - a)
  return side === 'top' || side === 'bottom'
    ? { x: x + INSET, y: lo, width: w - INSET * 2, height: t }
    : { x: lo, y: y + INSET, width: t, height: h - INSET * 2 }
}

/** 每个开关的外壳：悬停 / 聚焦整体变深（ink），路径都用 currentColor */
const SWITCH_CLS =
  'group cursor-pointer outline-none transition-colors hover:text-ink focus-visible:text-ink'
/** 开关里的线：不吃指针（命中只认 hit 矩形）；悬停 / 聚焦时不管开关全亮 */
const MARK_CLS = 'pointer-events-none group-hover:opacity-100 group-focus-visible:opacity-100'

/**
 * 每个开关共用的两层底：`halo` 是悬停 / 聚焦时的淡蓝底（不吃指针，聚焦再加
 * 一圈 accent 描边），下面那个透明矩形才是真正的命中区。悬停与聚焦用同一种
 * 反馈——先看到「这块能点」，再看到点下去会变成什么（那一半刻度亮起来）。
 */
function Halo({ hit, halo }: { hit: Rect; halo?: Rect }) {
  return (
    <>
      <rect
        {...(halo ?? hit)}
        rx="2"
        strokeWidth="1"
        className={cn(
          'pointer-events-none fill-transparent stroke-transparent transition-colors',
          'group-hover:fill-selected group-focus-visible:fill-selected group-focus-visible:stroke-ink',
        )}
      />
      <rect {...hit} fill="transparent" />
    </>
  )
}

const ctl = (key: string, values?: Record<string, unknown>) =>
  translate(`control.${key}`, { ns: 'inspector', ...(values ?? {}) })

function SvgSwitch({
  prop,
  on,
  adapter,
  children,
  hit,
  halo,
}: {
  prop: string
  on: boolean
  adapter: TickSpineAdapter
  children: React.ReactNode
  hit: Rect
  /** 淡蓝底的几何；不给就与命中区同大 */
  halo?: Rect
}) {
  const name = ctl(on ? 'switchOn' : 'switchOff', { label: adapter.labelOf(prop) })
  const modified = adapter.isOverridden(prop)
  const onKey = (e: KeyboardEvent) => {
    if (e.key !== 'Enter' && e.key !== ' ') return
    e.preventDefault()
    adapter.toggle(prop, !on)
  }
  return (
    <Tip label={modified ? `${name} · ${translate('element.modified', { ns: 'inspector' })}` : name}>
      <g
        role="switch"
        aria-checked={on}
        aria-label={adapter.labelOf(prop)}
        tabIndex={0}
        // 修改标记跟着这条边走（颜色 + tooltip 文字两重表达），不另列一排标签
        data-tick-modified={modified ? 'true' : undefined}
        className={cn(SWITCH_CLS, modified && 'text-ink')}
        onClick={() => adapter.toggle(prop, !on)}
        onKeyDown={onKey}
      >
        <Halo hit={hit} halo={halo} />
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

/**
 * 内 / 外一个带的开关（纯展示）：aria-checked = 这一方向此刻开不开。点下去
 * 写什么由调用处决定（`toggleSidePlan` 的计划）。
 */
function ZoneSwitch({
  side,
  zone,
  on,
  coupled,
  modified,
  minor,
  fire,
}: {
  side: Side
  zone: 'inner' | 'outer'
  on: boolean
  /** 这次点击会连带改到的同轴另一边（方向是整条轴的） */
  coupled: readonly string[]
  modified: boolean
  minor: boolean
  fire: () => void
}) {
  const dir = zone === 'inner' ? 'in' : 'out'
  const name = ctl('zoneAria', {
    side: translate(`tick.side.${side}`, { ns: 'inspector' }),
    dir: translate(`tick.dir.${dir}`, { ns: 'inspector' }),
  })
  const coupledText = coupled.length
    ? translate('spineZone.coupled', {
        ns: 'workspace',
        sides: listJoin(coupled.map((sd) => translate(`tick.side.${sd}`, { ns: 'inspector' }))),
      })
    : ''
  const tip = `${ctl(on ? 'switchOn' : 'switchOff', { label: name })}${coupledText ? ` ${coupledText}` : ''}`
  const onKey = (e: KeyboardEvent) => {
    if (e.key !== 'Enter' && e.key !== ' ') return
    e.preventDefault()
    fire()
  }
  const hit = hitRect(side, zone)
  const tipText = modified ? `${tip} · ${translate('element.modified', { ns: 'inspector' })}` : tip
  return (
    <Tip label={tipText}>
      <g
        role="switch"
        aria-checked={on}
        aria-label={name}
        tabIndex={0}
        data-tick-modified={modified ? 'true' : undefined}
        className={cn(SWITCH_CLS, modified && 'text-ink')}
        data-tick-zone={`${side}:${zone}`}
        data-tick-coupled={coupled.length ? coupled.join(',') : undefined}
        onClick={fire}
        onKeyDown={onKey}
      >
        <Halo hit={hit} halo={haloRect(side, zone)} />
        {/* 开着：实线；关着的内侧带画成极淡的虚线占位（痕迹留在框里，图外
            保持干净）；关着的外侧带平时不画——悬停 / 聚焦时才亮出来 */}
        <path
          d={halfMarks(side, dir, MAJOR_LEN, false)}
          fill="none"
          stroke="currentColor"
          strokeWidth={on ? 1.15 : 0.9}
          opacity={on ? 0.88 : zone === 'outer' ? 0 : 0.16}
          strokeDasharray={on ? undefined : '2 2'}
          className={MARK_CLS}
          data-tick-major={side}
          data-tick-half={dir}
          data-tick-on={on ? 'true' : 'false'}
        />
        {minor && on && (
          <path
            d={halfMarks(side, dir, MINOR_LEN, true)}
            fill="none"
            stroke="currentColor"
            strokeWidth={0.9}
            opacity={0.7}
            className={MARK_CLS}
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

  const gridLabel = translate('canvas.grid', { ns: 'inspector' })
  const resetLabel = ctl('resetDiagram', { count: modified.length })

  return (
    <div className="flex flex-col gap-2">
      <div className="relative flex justify-center pb-2 pt-1">
        <svg
          viewBox="0 0 220 148"
          className="w-full max-w-[220px] overflow-visible text-ink"
          aria-label={ctl('tickSpineDiagram')}
          role="group"
        >
          {/* 网格预览（非交互，开关在下方） */}
          {gridX && (
            <path
              d={`M${BOX.x + BOX.w * 0.25} ${BOX.y} V${BOX.y + BOX.h} M${BOX.x + BOX.w * 0.5} ${BOX.y} V${BOX.y + BOX.h} M${BOX.x + BOX.w * 0.75} ${BOX.y} V${BOX.y + BOX.h}`}
              fill="none"
              stroke="currentColor"
              strokeOpacity="0.32"
              strokeWidth="0.7"
              className="pointer-events-none"
              aria-hidden
            />
          )}
          {gridY && (
            <path
              d={`M${BOX.x} ${BOX.y + BOX.h * 0.25} H${BOX.x + BOX.w} M${BOX.x} ${BOX.y + BOX.h * 0.5} H${BOX.x + BOX.w} M${BOX.x} ${BOX.y + BOX.h * 0.75} H${BOX.x + BOX.w}`}
              fill="none"
              stroke="currentColor"
              strokeOpacity="0.32"
              strokeWidth="0.7"
              className="pointer-events-none"
              aria-hidden
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
                  halo={haloRect(side, 'spine')}
                >
                  {/* 四条边是四个独立开关、各画各的路径，端点默认 butt——线正好
                      停在角点上，每个角缺一块半线宽见方的口子。square 端点让每条线
                      各向两端外延半个线宽，恰好补到相邻边的外沿：路径、命中区、
                      开关语义都不动，四角自然闭合。 */}
                  <path
                    d={spinePath(side)}
                    fill="none"
                    stroke="currentColor"
                    strokeWidth={on(`spine_${side}`) ? 1.5 : 1}
                    opacity={on(`spine_${side}`) ? 1 : 0.26}
                    strokeDasharray={on(`spine_${side}`) ? undefined : '3 3'}
                    strokeLinecap="square"
                    className={MARK_CLS}
                  />
                </SvgSwitch>
              )}
              {adapter.has(`ticks_${side}`) &&
                zoned(side) &&
                (['inner', 'outer'] as const).map((zone) => {
                  const st = model!.sides[side]!
                  const plan = toggleSidePlan(model!, side, zone)
                  return (
                    <ZoneSwitch
                      key={zone}
                      side={side}
                      zone={zone}
                      on={zone === 'inner' ? st.inward : st.outward}
                      coupled={plan?.effect.coupled ?? []}
                      modified={adapter.isOverridden(`ticks_${side}`)}
                      minor={stateOf(side).minor}
                      fire={() => {
                        if (plan) adapter.applyPlan?.(plan)
                      }}
                    />
                  )
                })}
              {adapter.has(`ticks_${side}`) && !zoned(side) && (
                <SvgSwitch
                  prop={`ticks_${side}`}
                  on={on(`ticks_${side}`)}
                  adapter={adapter}
                  hit={hitRect(side, 'ticks')}
                  halo={haloRect(side, 'ticks', stateOf(side).direction)}
                >
                  {/* 主刻度与次刻度都在同一个开关里：这条边一关，两者一起变成
                      关闭样式——「关了但次刻度还亮着」是自相矛盾的状态 */}
                  <path
                    d={tickMarks(side, stateOf(side).direction)}
                    fill="none"
                    stroke="currentColor"
                    strokeWidth={on(`ticks_${side}`) ? 1.15 : 0.9}
                    opacity={on(`ticks_${side}`) ? 0.88 : 0.16}
                    strokeDasharray={on(`ticks_${side}`) ? undefined : '2 2'}
                    className={MARK_CLS}
                    data-tick-major={side}
                    data-tick-direction={stateOf(side).direction}
                  />
                  {stateOf(side).minor && (
                    <path
                      d={minorTickMarks(side, stateOf(side).direction)}
                      fill="none"
                      stroke="currentColor"
                      strokeWidth={on(`ticks_${side}`) ? 0.9 : 0.8}
                      opacity={on(`ticks_${side}`) ? 0.7 : 0.16}
                      strokeDasharray={on(`ticks_${side}`) ? undefined : '2 2'}
                      className={MARK_CLS}
                      data-tick-minor={side}
                    />
                  )}
                </SvgSwitch>
              )}
            </g>
          ))}
        </svg>

        {/* 改过的边在图上自己说（accent 色 + tooltip「已修改」）；恢复只有一个
            动作——一次把示意图承接的全部修改回到脚本（一条历史）。做成图右上角
            的图标按钮，与分区标题里的动作同一位置；没改过时不渲染、不占位。 */}
        {modified.length > 0 && (
          <Tip label={resetLabel}>
            <button
              type="button"
              onClick={adapter.resetAll}
              data-tick-reset-all
              aria-label={resetLabel}
              className={cn(
                'absolute right-0 top-0 flex h-7 w-7 items-center justify-center rounded-sm text-ink-2',
                'outline-none transition-colors hover:bg-surface-2 hover:text-ink focus-visible:focus-ring',
              )}
            >
              <RotateCcw size={ICON_SIZE.sm} aria-hidden />
            </button>
          </Tip>
        )}
      </div>
      {/* 示意图能点这件事只有悬停才知道（2026-09-12 critique：首次用户不知道它能点）；
          一句 caption 说清，读屏也拿得到。它说的是图的用法，不重复各边的名字 */}
      <p className="-mt-1 text-center type-caption" data-tick-diagram-caption>
        {ctl('tickSpineDiagramHint')}
      </p>

      {(adapter.has('grid_x') || adapter.has('grid_y')) && (
        <div
          role="group"
          aria-label={gridLabel}
          className="flex items-center justify-between gap-3 border-t border-border pt-2"
        >
          <span className="text-sm text-ink-2">{gridLabel}</span>
          <div className="flex items-center gap-1">
            {(['grid_x', 'grid_y'] as const).map((p) =>
              adapter.has(p) ? (
                <Tip key={p} label={adapter.labelOf(p)}>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={on(p)}
                    onClick={() => adapter.toggle(p, !on(p))}
                    className={cn(
                      'flex h-7 min-w-14 items-center justify-center gap-1.5 rounded-sm px-2 text-sm',
                      'outline-none transition-colors focus-visible:focus-ring',
                      on(p)
                        ? 'bg-selected text-ink'
                        : 'text-ink-2 hover:bg-surface-2 hover:text-ink',
                    )}
                  >
                    <svg width="13" height="13" viewBox="0 0 14 14" fill="none" aria-hidden>
                      {p === 'grid_x' ? (
                        <path d="M5 2V12M9 2V12" stroke="currentColor" strokeWidth="1.4" />
                      ) : (
                        <path d="M2 5H12M2 9H12" stroke="currentColor" strokeWidth="1.4" />
                      )}
                    </svg>
                    {translate(p === 'grid_x' ? 'tick.axisX' : 'tick.axisY', { ns: 'inspector' })}
                    {/* 开关状态的第二重表达：不只靠底色，开着时多一颗小点；关着时留同样的位 */}
                    <span
                      aria-hidden
                      className={cn('h-[3px] w-[3px] rounded-full', on(p) && 'bg-current')}
                    />
                  </button>
                </Tip>
              ) : null,
            )}
          </div>
        </div>
      )}
    </div>
  )
}
