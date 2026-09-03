/**
 * 坐标轴边框的语义命中区与四边刻度状态（Prompt 16）。
 *
 * ## 命中模型
 *
 * 每条**直角**边框（manifest 的 `spines`，引擎按画出来的那条线给端点）在
 * 它自己的局部坐标里分三个带：
 *
 *   inner   —— 数据区域那一侧：控制这一边的**向内**刻度
 *   outer   —— 图形外面那一侧：控制这一边的**向外**刻度
 *   neutral —— 线本身附近：不切刻度（点它 = 选中子图），免得贴着线点就误触
 *
 * 带宽以**屏幕像素**计（`ZONE_PX`），调用方把分数坐标按当前 zoom 换算成像素
 * 再进来，所以放大时带不会变宽、缩小时不会窄到点不中。devicePixelRatio 不进
 * 判据：指针坐标本来就是 CSS 像素，HiDPI 只改画法不改命中。
 *
 * 旧的示意图把刻度命中区写死在框外——刻度朝内时用户得点框外才能控制框内
 * 那排短线，这就是「刻度朝内却必须点击图框外侧」那条反直觉命中的根因。
 *
 * ## 状态模型
 *
 * matplotlib 的刻度方向 `direction` 是**一条轴**的属性（x 轴 = 上下两边共
 * 用，y 轴 = 左右两边共用；`tick_params` 没有按边分方向的入口），而一侧
 * 显不显示刻度线是**边**的属性（`ticks_<side>`）。于是每一边的形态由两者
 * 派生：
 *
 *   inward(side)  = ticks_<side> && direction ∈ {in, inout}
 *   outward(side) = ticks_<side> && direction ∈ {out, inout}
 *
 * 反过来「切换某一边的向内刻度」映射回这两个旋钮时，方向那一步会**连带**
 * 同一条轴上另一边（若它可见）——这是底层能力的边界，不是我们发明的耦合。
 * 界面必须把这一点说出来（`SidePlan.effect.coupled`），而不是假装每边独立。
 *
 * 这份模块是**纯函数**：画布命中层与属性页的示意图 / 刻度卡都从这里取状态、
 * 要计划（一次点击 = 一份 patch 列表 = 一条历史），两处永远同源。
 */
import type { Manifest, ManifestElement, SpineGeom, SpineSide } from './api'

export type { SpineGeom, SpineSide }

export type TickDirection = 'in' | 'out' | 'inout'
/** 一条轴在属性页里的四档：三个真方向 + 「两边都不显示」这一档派生态 */
export type AxisTickChoice = TickDirection | 'hidden'
export type SpineZone = 'inner' | 'outer' | 'neutral'
export type SpineAxis = 'x' | 'y'

/** 角落两条边并列时的优先次序：下、左（matplotlib 默认有刻度的两条）在前 */
export const SPINE_SIDES: readonly SpineSide[] = ['bottom', 'left', 'top', 'right']

/** 边所属的轴：上下是 X，左右是 Y（与 matplotlib 的 tick_params(axis=) 一致） */
export const axisOfSide = (side: SpineSide): SpineAxis =>
  side === 'top' || side === 'bottom' ? 'x' : 'y'

export const oppositeSide = (side: SpineSide): SpineSide =>
  side === 'top' ? 'bottom' : side === 'bottom' ? 'top' : side === 'left' ? 'right' : 'left'

export const sidesOfAxis = (axis: SpineAxis): SpineSide[] =>
  axis === 'x' ? ['bottom', 'top'] : ['left', 'right']

/* -------------------------------------------------------------------------- */
/*  几何                                                                       */
/* -------------------------------------------------------------------------- */

export interface ZoneWidths {
  /** 线两侧各留这么宽不切刻度（屏幕 px） */
  neutral: number
  /** 从线算起、命中带的总半宽（屏幕 px）；inner / outer 各占 neutral..band */
  band: number
}

/** 鼠标 / 触控笔 */
export const ZONE_PX: ZoneWidths = { neutral: 2.5, band: 10 }
/** 手指：带要宽得多，否则 44px 的指尖对着 10px 的带只能碰运气 */
export const ZONE_PX_TOUCH: ZoneWidths = { neutral: 4, band: 18 }

export const zoneWidthsFor = (pointerType: string | undefined): ZoneWidths =>
  pointerType === 'touch' ? ZONE_PX_TOUCH : ZONE_PX

/** 一个分数单位对应多少屏幕像素（= 面板内容边长 × zoom），x / y 各一份 */
export interface ZoneScale {
  pxPerFracX: number
  pxPerFracY: number
}

export interface ZoneHit {
  side: SpineSide
  zone: SpineZone
  /** 到线的垂直距离（屏幕 px，无符号） */
  distPx: number
}

/**
 * 一条边的局部坐标：`d` 是点到线的有符号垂直距离（px，**正 = 内侧**），
 * `along` 是沿线方向的位置（px，0..length 落在线段上）。
 */
function localCoords(
  side: SpineSide,
  g: SpineGeom,
  fx: number,
  fy: number,
  scale: ZoneScale,
): { d: number; along: number; length: number } {
  const horizontal = side === 'top' || side === 'bottom'
  if (horizontal) {
    const y = g.from[1]
    const x0 = Math.min(g.from[0], g.to[0])
    const x1 = Math.max(g.from[0], g.to[0])
    // 下边：内侧在线的上方（y 更小）；上边：内侧在下方
    const d = (side === 'bottom' ? y - fy : fy - y) * scale.pxPerFracY
    return {
      d,
      along: (fx - x0) * scale.pxPerFracX,
      length: (x1 - x0) * scale.pxPerFracX,
    }
  }
  const x = g.from[0]
  const y0 = Math.min(g.from[1], g.to[1])
  const y1 = Math.max(g.from[1], g.to[1])
  // 左边：内侧在线的右方；右边：内侧在左方
  const d = (side === 'left' ? fx - x : x - fx) * scale.pxPerFracX
  return {
    d,
    along: (fy - y0) * scale.pxPerFracY,
    length: (y1 - y0) * scale.pxPerFracY,
  }
}

/** 这一边有没有东西可点：线不显示、刻度也不显示的边没有任何可见目标，不设命中区 */
export const sideHasTarget = (g: SpineGeom | undefined): g is SpineGeom =>
  !!g && (g.visible || g.ticks)

/**
 * 点落在哪条边的哪个带里。没落进任何带回 null。
 *
 * 角落：两条边的带会叠在一起，取垂直距离更近的那条；距离并列时先看哪条边
 * 此刻真画着刻度（用户要点的多半是看得见的那排），再按 `SPINE_SIDES` 定序
 * ——结果必须确定，同一个点两次点击不能落到两条边上。
 */
export function spineZoneAt(
  spines: Partial<Record<SpineSide, SpineGeom>> | undefined,
  fx: number,
  fy: number,
  scale: ZoneScale,
  widths: ZoneWidths = ZONE_PX,
): ZoneHit | null {
  if (!spines) return null
  let best: (ZoneHit & { ticks: boolean; order: number }) | null = null
  SPINE_SIDES.forEach((side, order) => {
    const g = spines[side]
    if (!sideHasTarget(g)) return
    const { d, along, length } = localCoords(side, g, fx, fy, scale)
    const dist = Math.abs(d)
    if (dist > widths.band) return
    if (along < -widths.band || along > length + widths.band) return
    const zone: SpineZone = dist <= widths.neutral ? 'neutral' : d > 0 ? 'inner' : 'outer'
    const cand = { side, zone, distPx: dist, ticks: g.ticks, order }
    if (
      !best ||
      cand.distPx < best.distPx - 1e-9 ||
      (Math.abs(cand.distPx - best.distPx) <= 1e-9 &&
        (Number(cand.ticks) > Number(best.ticks) ||
          (cand.ticks === best.ticks && cand.order < best.order)))
    ) {
      best = cand
    }
  })
  if (!best) return null
  const { side, zone, distPx } = best as ZoneHit
  return { side, zone, distPx }
}

/**
 * 一条边某个带在内容分数坐标里的矩形（画 hover 高亮用）。带宽仍按屏幕像素给，
 * 由 `scale` 换算回分数，所以高亮条与命中带逐像素重合。
 */
export function zoneRectFrac(
  side: SpineSide,
  g: SpineGeom,
  zone: Exclude<SpineZone, 'neutral'>,
  scale: ZoneScale,
  widths: ZoneWidths = ZONE_PX,
): { x: number; y: number; w: number; h: number } {
  const horizontal = side === 'top' || side === 'bottom'
  const near = widths.neutral
  const far = widths.band
  if (horizontal) {
    const y = g.from[1]
    const x0 = Math.min(g.from[0], g.to[0])
    const x1 = Math.max(g.from[0], g.to[0])
    // 内侧向框里伸：下边往上（y 减），上边往下
    const inward = side === 'bottom' ? -1 : 1
    const sign = zone === 'inner' ? inward : -inward
    const a = y + (sign * near) / scale.pxPerFracY
    const b = y + (sign * far) / scale.pxPerFracY
    return { x: x0, y: Math.min(a, b), w: x1 - x0, h: Math.abs(b - a) }
  }
  const x = g.from[0]
  const y0 = Math.min(g.from[1], g.to[1])
  const y1 = Math.max(g.from[1], g.to[1])
  const inward = side === 'left' ? 1 : -1
  const sign = zone === 'inner' ? inward : -inward
  const a = x + (sign * near) / scale.pxPerFracX
  const b = x + (sign * far) / scale.pxPerFracX
  return { x: Math.min(a, b), y: y0, w: Math.abs(b - a), h: y1 - y0 }
}

/* -------------------------------------------------------------------------- */
/*  状态                                                                       */
/* -------------------------------------------------------------------------- */

export interface SideTickState {
  side: SpineSide
  axis: SpineAxis
  /** `ticks_<side>`：这一边显不显示刻度线（主刻度口径） */
  visible: boolean
  /** 这条轴的 `direction`（上下共用 / 左右共用） */
  direction: TickDirection
  inward: boolean
  outward: boolean
}

export interface AxesTickModel {
  gid: string
  /** 两条轴的刻度元素 gid（引擎没发那条轴的刻度元素时缺席） */
  tickGid: Partial<Record<SpineAxis, string>>
  /** 只有「四边开关字段 + 那条轴的 direction 字段」都在的边才出现 */
  sides: Partial<Record<SpineSide, SideTickState>>
}

export interface OverrideLike {
  gid: string
  prop: string
  value: unknown
}

const DIRECTIONS = new Set<string>(['in', 'out', 'inout'])

/** 覆盖优先、其次 manifest 当前值 */
function readProp(el: ManifestElement, overrides: readonly OverrideLike[], prop: string): unknown {
  const ov = overrides.find((o) => o.gid === el.gid && o.prop === prop)
  if (ov) return ov.value
  return el.editable.find((f) => f.prop === prop)?.value
}

const hasField = (el: ManifestElement | undefined, prop: string) =>
  !!el?.editable.some((f) => f.prop === prop)

/** 一边的形态：显示与方向合成 */
export function deriveSideState(
  side: SpineSide,
  visible: boolean,
  direction: TickDirection,
): SideTickState {
  return {
    side,
    axis: axisOfSide(side),
    visible,
    direction,
    inward: visible && direction !== 'out',
    outward: visible && direction !== 'in',
  }
}

/** 一边此刻的四档（示意图与状态文案用） */
export const sideChoice = (s: SideTickState): AxisTickChoice => (s.visible ? s.direction : 'hidden')

/**
 * 从 manifest + 文档 override 读出一个子图的四边刻度模型。
 * `axesGid` 不是子图、或它没有任何四边开关字段时回 null。
 */
export function readAxesTickModel(
  manifest: Manifest | null | undefined,
  overrides: readonly OverrideLike[],
  axesGid: string,
): AxesTickModel | null {
  const axes = manifest?.elements.find((e) => e.gid === axesGid)
  if (!axes || (axes.role !== 'axes' && axes.role !== 'axes3d')) return null
  const tickGid: Partial<Record<SpineAxis, string>> = {}
  const dirOf: Partial<Record<SpineAxis, TickDirection>> = {}
  for (const axis of ['x', 'y'] as const) {
    const gid = `${axesGid}.${axis}ticks`
    const el = manifest!.elements.find((e) => e.gid === gid && e.role === 'ticks')
    if (!el || !hasField(el, 'direction')) continue
    tickGid[axis] = gid
    const raw = readProp(el, overrides, 'direction')
    dirOf[axis] = DIRECTIONS.has(String(raw)) ? (raw as TickDirection) : 'out'
  }
  const sides: Partial<Record<SpineSide, SideTickState>> = {}
  for (const side of SPINE_SIDES) {
    const prop = `ticks_${side}`
    if (!hasField(axes, prop)) continue
    const dir = dirOf[axisOfSide(side)]
    if (!dir) continue
    sides[side] = deriveSideState(side, readProp(axes, overrides, prop) === true, dir)
  }
  if (!Object.keys(sides).length) return null
  return { gid: axesGid, tickGid, sides }
}

/* -------------------------------------------------------------------------- */
/*  计划：一次点击 = 一份 patch 列表                                              */
/* -------------------------------------------------------------------------- */

export interface Patch {
  gid: string
  prop: string
  value: unknown
}

export interface SidePlanEffect {
  side: SpineSide
  axis: SpineAxis
  /** 这次动的是向内还是向外 */
  dir: 'in' | 'out'
  /** 打开还是关掉 */
  on: boolean
  /** 关掉之后这一边就没有刻度了（写的是 ticks_<side>=false，方向没动） */
  hides: boolean
  /** 打开时这一边原本没有刻度（写了 ticks_<side>=true） */
  shows: boolean
  /** 方向改成了什么（没动方向时缺席） */
  direction?: TickDirection
  /** 方向那一步连带改到的同轴另一边（那边可见才算连带） */
  coupled: SpineSide[]
}

export interface SidePlan {
  set: Patch[]
  /** 要删掉的 override（回到脚本值） */
  remove: { gid: string; prop: string }[]
  effect: SidePlanEffect
}

/**
 * 切换某一边的向内 / 向外刻度。
 *
 *   开着 → 关：另一方向还开着就只改方向（inout → 单向）；另一方向也没开就
 *          关掉这一边（`ticks_<side>=false`，方向不动）
 *   关着 → 开：这一边本来隐藏就先打开；方向按「另一方向此刻开不开」定：
 *          另一方向开着 → inout，否则就是这一方向本身
 *
 * 方向落在轴上，所以同轴另一边若可见会一起变——写进 `effect.coupled`。
 */
export function toggleSidePlan(
  model: AxesTickModel,
  side: SpineSide,
  zone: 'inner' | 'outer',
): SidePlan | null {
  const s = model.sides[side]
  const axis = axisOfSide(side)
  const tickGid = model.tickGid[axis]
  if (!s || !tickGid) return null
  const dir: 'in' | 'out' = zone === 'inner' ? 'in' : 'out'
  const isOn = dir === 'in' ? s.inward : s.outward
  const otherOn = dir === 'in' ? s.outward : s.inward
  const set: Patch[] = []
  let direction: TickDirection | undefined
  let hides = false
  let shows = false
  if (isOn) {
    if (otherOn) {
      direction = dir === 'in' ? 'out' : 'in'
    } else {
      hides = true
      set.push({ gid: model.gid, prop: `ticks_${side}`, value: false })
    }
  } else {
    if (!s.visible) {
      shows = true
      set.push({ gid: model.gid, prop: `ticks_${side}`, value: true })
    }
    // 加一个方向，不拿走另一个：轴的方向里已含另一方向就成 inout，否则就是
    // 这一方向本身。这一边原本隐藏时 otherOn 恒 false，但轴的方向仍然在
    // ——打开后另一方向开不开由它说了算，所以看轴不看边
    const otherDir: TickDirection = dir === 'in' ? 'out' : 'in'
    const otherIncluded = s.direction === 'inout' || s.direction === otherDir
    direction = otherIncluded ? 'inout' : dir
    if (direction === s.direction) direction = undefined
  }
  if (direction) set.push({ gid: tickGid, prop: 'direction', value: direction })
  const opp = model.sides[oppositeSide(side)]
  const coupled = direction && opp?.visible ? [opp.side] : []
  return {
    set,
    remove: [],
    effect: { side, axis, dir, on: !isOn, hides, shows, direction, coupled },
  }
}

/**
 * 属性页的「刻度方向」四档（按轴）。
 *
 *   in / out / inout：写方向；这条轴两边此刻都没显示刻度时，先把两边的
 *   `ticks_<side>` override 删掉回到脚本的边——脚本那边一般至少开着下 / 左
 *   （脚本本来就一边都不开的，用户在示意图上再点一边即可，不替他猜）。
 *   hidden：两边都关（`ticks_<side>=false`），方向不动——切回来时还是它。
 */
export function axisChoicePlan(
  model: AxesTickModel,
  axis: SpineAxis,
  choice: AxisTickChoice,
): SidePlan | null {
  const tickGid = model.tickGid[axis]
  const sides = sidesOfAxis(axis).filter((s) => model.sides[s])
  if (!tickGid || !sides.length) return null
  const primary = sides[0]
  const state = model.sides[primary]!
  if (choice === 'hidden') {
    return {
      set: sides.map((s) => ({ gid: model.gid, prop: `ticks_${s}`, value: false })),
      remove: [],
      effect: {
        side: primary,
        axis,
        dir: 'out',
        on: false,
        hides: true,
        shows: false,
        coupled: sides.slice(1),
      },
    }
  }
  const set: Patch[] = []
  if (choice !== state.direction) set.push({ gid: tickGid, prop: 'direction', value: choice })
  const anyVisible = sides.some((s) => model.sides[s]!.visible)
  const remove = anyVisible ? [] : sides.map((s) => ({ gid: model.gid, prop: `ticks_${s}` }))
  if (!set.length && !remove.length) return null
  return {
    set,
    remove,
    effect: {
      side: primary,
      axis,
      dir: choice === 'in' ? 'in' : 'out',
      on: true,
      hides: false,
      shows: !anyVisible,
      direction: choice !== state.direction ? choice : undefined,
      coupled: sides.slice(1).filter((s) => model.sides[s]!.visible),
    },
  }
}

/** 一条轴此刻的四档 */
export function axisChoice(model: AxesTickModel, axis: SpineAxis): AxisTickChoice | null {
  const sides = sidesOfAxis(axis).filter((s) => model.sides[s])
  if (!sides.length) return null
  const anyVisible = sides.some((s) => model.sides[s]!.visible)
  return anyVisible ? model.sides[sides[0]]!.direction : 'hidden'
}

/** 单独开关一边（示意图上的「显示边」与属性页的复选框共用） */
export function sideVisiblePlan(model: AxesTickModel, side: SpineSide, on: boolean): SidePlan | null {
  const s = model.sides[side]
  if (!s || s.visible === on) return null
  return {
    set: [{ gid: model.gid, prop: `ticks_${side}`, value: on }],
    remove: [],
    effect: {
      side,
      axis: s.axis,
      dir: s.direction === 'in' ? 'in' : 'out',
      on,
      hides: !on,
      shows: on,
      coupled: [],
    },
  }
}

/* -------------------------------------------------------------------------- */
/*  画布：在整张图里挑边                                                        */
/* -------------------------------------------------------------------------- */

export interface SpineZonePick {
  /** 拥有这条边的子图 */
  gid: string
  hit: ZoneHit
  geom: SpineGeom
}

/**
 * 整张图里的边框命中：逐个子图跑 `spineZoneAt`，取最近的那条。
 *
 * 孪生轴（twinx）与 `secondary_xaxis` 的边框线**逐位重合**，距离永远并列
 * ——并列时先取此刻真画着刻度的那条（用户看见的那排就是它的），再按
 * manifest 的元素序。`allowGid` 是命中优先级的闸：`pickElement` 命中了文字 /
 * 曲线 / 别的子图时，边框命中区让路（文字与 resize 手柄永远高优先级）；
 * 只有命中 figure（图外空白、偏出去的边框）或那条边所属的子图本身时才算。
 */
export function pickSpineZone(
  manifest: Manifest | null | undefined,
  fx: number,
  fy: number,
  scale: ZoneScale,
  widths: ZoneWidths,
  allowGid: (gid: string) => boolean,
): SpineZonePick | null {
  if (!manifest) return null
  let best: SpineZonePick | null = null
  for (const el of manifest.elements) {
    if (!el.spines || el.role !== 'axes') continue
    if (!allowGid(el.gid)) continue
    const hit = spineZoneAt(el.spines, fx, fy, scale, widths)
    if (!hit) continue
    const geom = el.spines[hit.side]!
    if (
      !best ||
      hit.distPx < best.hit.distPx - 1e-9 ||
      (Math.abs(hit.distPx - best.hit.distPx) <= 1e-9 && geom.ticks && !best.geom.ticks)
    ) {
      best = { gid: el.gid, hit, geom }
    }
  }
  return best
}
