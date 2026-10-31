import type { AlignMode, ResizeDir } from './geometry'
import { clamp } from './units'

/** figure 分数坐标的 [x, y, w, h]；y 的原点由使用方约定 */
export type Rect4 = [number, number, number, number]

/**
 * figure 分数坐标翻转 y —— matplotlib 的 axes position 是 bottom-origin，
 * manifest 的 bbox / anchor 是 top-origin，两个方向换算公式相同。
 */
export const flipY = (r: Rect4): Rect4 => [r[0], 1 - (r[1] + r[3]), r[2], r[3]]

/** 一个参与对齐的元素；box 一律是 top-origin，resizable 决定能否等宽等高 */
export interface AlignItem {
  key: string
  box: Rect4
  resizable: boolean
}

const L = (r: Rect4) => r[0]
const R = (r: Rect4) => r[0] + r[2]
const T = (r: Rect4) => r[1]
const B = (r: Rect4) => r[1] + r[3]

/**
 * round4 之后逐位相等 = 这次对齐 / 拖动对它**没有可表示的位移**。
 *
 * 「没有视觉位移就不写 override」的唯一判据：对齐（`layoutBoxes`）与图内拖动的
 * 松手（`interactions.ts` 的单拖 / 整组平移 / 子图移动与缩放 / 箭头 / 成组缩放）
 * 共用这一份，按**终点净位移**判，不按途中是否越过拖动阈值判（GEO-07）。
 *
 * 阈值不是拍脑袋取的：写出去的 override 本来就要过 `round4`（figure 分数保留
 * 四位），比它更小的位移落到文件里是同一个数。200mm 宽的图上 1e-4 ≈ 0.02mm，
 * 远在任何印刷分辨率之下。
 */
export const sameAfterRound4 = (a: readonly number[], b: readonly number[]): boolean =>
  a.length === b.length && a.every((v, i) => round4(v) === round4(b[i]))

/**
 * 对一组元素做对齐 / 分布，返回**真正需要改动**的新框（top-origin 分数坐标）。
 * 基准（等宽/等高）取 items 末位——与画布对象层「最后选中的为基准」保持一致。
 *
 * 位置类模式（left/right/center/top/bottom/分布）的基准是**选区边界**，不是
 * 末位元素；末位只对 samew/sameh 有意义。UI 上的「基准」角标必须跟着这条走。
 *
 * **已经在目标位置的元素不进结果**（issue #131）：它们本来可能压根没有位置
 * override——标题、轴标签、图例默认是 matplotlib 自动布局的，为「没动」也写
 * 一条等于当前值的绝对坐标，等于把它钉死在 figure 上：此后再改字号、改图幅，
 * 它就不会自己让位了，而用户只是点了一下左对齐。
 */
export function layoutBoxes(items: AlignItem[], mode: AlignMode): Map<string, Rect4> {
  const out = new Map<string, Rect4>()
  if (items.length < 2) return out

  const minL = Math.min(...items.map((i) => L(i.box)))
  const maxR = Math.max(...items.map((i) => R(i.box)))
  const minT = Math.min(...items.map((i) => T(i.box)))
  const maxB = Math.max(...items.map((i) => B(i.box)))
  const ref = items[items.length - 1]

  const put = (it: AlignItem, box: Rect4) => {
    const next = box.map(round4) as Rect4
    if (sameAfterRound4(next, it.box)) return
    out.set(it.key, next)
  }

  if (mode === 'hdist' || mode === 'vdist') {
    if (items.length < 3) return out
    const horizontal = mode === 'hdist'
    const sorted = [...items].sort((a, b) =>
      horizontal ? L(a.box) - L(b.box) : T(a.box) - T(b.box),
    )
    const span = horizontal ? maxR - minL : maxB - minT
    const total = sorted.reduce((t, i) => t + (horizontal ? i.box[2] : i.box[3]), 0)
    const gap = (span - total) / (sorted.length - 1)
    let cur = horizontal ? minL : minT
    for (const it of sorted) {
      const [x, y, w, h] = it.box
      put(it, horizontal ? [cur, y, w, h] : [x, cur, w, h])
      cur += (horizontal ? w : h) + gap
    }
    return out
  }

  for (const it of items) {
    const [x, y, w, h] = it.box
    switch (mode) {
      case 'left':
        put(it, [minL, y, w, h])
        break
      case 'right':
        put(it, [maxR - w, y, w, h])
        break
      case 'hcenter':
        put(it, [(minL + maxR) / 2 - w / 2, y, w, h])
        break
      case 'top':
        put(it, [x, minT, w, h])
        break
      case 'bottom':
        put(it, [x, maxB - h, w, h])
        break
      case 'vcenter':
        put(it, [x, (minT + maxB) / 2 - h / 2, w, h])
        break
      case 'samew':
        if (it !== ref) put(it, [x, y, ref.box[2], h])
        break
      case 'sameh':
        // 等高保持顶边不动，视觉上更符合预期
        if (it !== ref) put(it, [x, y, w, ref.box[3]])
        break
    }
  }
  return out
}

export const round4 = (v: number) => Math.round(v * 1e4) / 1e4

/* -------------------------------------------------------------------------- */
/*  成组缩放                                                                   */
/* -------------------------------------------------------------------------- */

/** 组框最小边长（figure 分数），防止一路拖到 0 */
const MIN_SPAN = 0.02

/** 一组框的并集，用作组包围框 */
export function unionBox(boxes: Rect4[]): Rect4 | null {
  if (!boxes.length) return null
  const x = Math.min(...boxes.map(L))
  const y = Math.min(...boxes.map(T))
  const r = Math.max(...boxes.map(R))
  const b = Math.max(...boxes.map(B))
  return [x, y, r - x, b - y]
}

/** 把 box 按组框的变化线性重映射，组内相对布局因此保持不变 */
export function remapBox(box: Rect4, from: Rect4, to: Rect4): Rect4 {
  const sx = from[2] > 0 ? to[2] / from[2] : 1
  const sy = from[3] > 0 ? to[3] / from[3] : 1
  return [
    to[0] + (box[0] - from[0]) * sx,
    to[1] + (box[1] - from[1]) * sy,
    box[2] * sx,
    box[3] * sy,
  ]
}

/**
 * 拖组框手柄后的新组框：对边/对角为锚，角手柄等比（取偏离 1 更多的那一维），
 * 边手柄单轴。
 *
 * 只兜最小尺寸，**不把组框钳进画布**：matplotlib 里 axes 超出 figure 是合法的
 * （超出部分被裁掉），而钳进画布会让贴边的组再也放不大——一旦某条边顶到
 * 0 或 1，允许的比例就恒为 1，缩放变成静默的空操作。用户有撤销兜底。
 */
export function resizeGroup(g: Rect4, dir: ResizeDir, dfx: number, dfy: number): Rect4 {
  const [gx, gy, gw, gh] = g
  const east = dir.includes('e')
  const west = dir.includes('w')
  const south = dir.includes('s')
  const north = dir.includes('n')

  const floorX = MIN_SPAN / gw
  const floorY = MIN_SPAN / gh

  let sx = Math.max(east ? (gw + dfx) / gw : west ? (gw - dfx) / gw : 1, floorX)
  let sy = Math.max(south ? (gh + dfy) / gh : north ? (gh - dfy) / gh : 1, floorY)

  if ((east || west) && (north || south)) {
    const dominant = Math.abs(sx - 1) >= Math.abs(sy - 1) ? sx : sy
    sx = sy = Math.max(dominant, Math.max(floorX, floorY))
  }

  return [
    west ? gx + gw - gw * sx : gx,
    north ? gy + gh - gh * sy : gy,
    gw * sx,
    gh * sy,
  ]
}

/** 子图的长宽比是否被 matplotlib 锁着（`aspect` 不是 auto：imshow、`set_aspect('equal')`、数值） */
export function axesAspectLocked(el: { editable: readonly { prop: string; value?: unknown }[] }): boolean {
  const f = el.editable.find((x) => x.prop === 'aspect')
  return f != null && f.value !== 'auto'
}

/**
 * 锁比例子图拖缩放手柄后的 position（bottom-origin 分数）：宽高同乘一个倍数 s。
 *
 * 自由缩放写下的框比例一变，matplotlib 重渲时 `apply_aspect` 会把画出来的框按原比例
 * 缩回去并在写下的框里居中——吸到参考线的上边落不到线上、不动的下边也跟着挪
 * （宣传片测试页实测）。等比写下的框比例不变，`apply_aspect` 不再改它，拖的时候看到的线框就是结果。
 *
 * 固定点 = 被拖手柄对侧；边柄没提到的那一轴固定左 / 上边（与画布对象等比缩放同一约定）。
 * 倍数默认取偏离 1 更多的那一轴；`by` 指定按哪一轴定（吸附吸上的那一轴）。
 * 框钳在整图里，两边都不小于 `min`；起始框已违反某条约束（短边已小于 `min` / 已超出图边）时，
 * 那条约束只挡继续变坏的方向——倍数 1（原样）始终可取，不会被钳得跳到图边或被强行缩。
 */
export function lockedAxesResize(
  start: Rect4,
  dir: ResizeDir,
  dfx: number,
  dfy: number,
  min: number,
  by?: 'x' | 'y',
): Rect4 {
  const [x, y, w, h] = start
  // 固定点在框内的相对位置：ax 0 = 左边不动、1 = 右边不动；ay 0 = 下边不动、1 = 上边不动
  const ax = dir.includes('w') ? 1 : 0
  const ay = dir.includes('n') ? 0 : 1
  const fx = x + ax * w
  const fy = y + ay * h
  // dfy 是屏幕向下为正；上柄向上拖 = 变高
  const sx = dir.includes('e') ? (w + dfx) / w : dir.includes('w') ? (w - dfx) / w : null
  const sy = dir.includes('n') ? (h - dfy) / h : dir.includes('s') ? (h + dfy) / h : null
  let s: number
  if (sx == null) s = sy ?? 1
  else if (sy == null) s = sx
  else if (by) s = by === 'x' ? sx : sy
  else s = Math.abs(sx - 1) >= Math.abs(sy - 1) ? sx : sy
  const room = (f: number, a: number, size: number) =>
    Math.min(a > 0 ? f / a : Infinity, a < 1 ? (1 - f) / (1 - a) : Infinity) / size
  // 下限 / 上限都与 1 取让步：已经太小的框不能再缩但不被放大，已经出图的框不能再放大但不被强行缩
  const lo = Math.min(1, Math.max(min / w, min / h))
  const hi = Math.max(1, Math.min(room(fx, ax, w), room(fy, ay, h)))
  s = clamp(s, lo, hi)
  return [fx - ax * w * s, fy - ay * h * s, w * s, h * s]
}

/** 绕中心按比例缩放（Inspector 的百分比输入）；同样只兜最小尺寸，不钳画布 */
export function scaleGroupAbout(g: Rect4, s: number): Rect4 {
  const [gx, gy, gw, gh] = g
  const cx = gx + gw / 2
  const cy = gy + gh / 2
  const k = Math.max(s, Math.max(MIN_SPAN / gw, MIN_SPAN / gh))
  const w = gw * k
  const h = gh * k
  return [cx - w / 2, cy - h / 2, w, h]
}

/** 在整张图里居中（bottom-origin 的 axes position） */
export function centerInFigure(rect: Rect4, axis: 'x' | 'y'): Rect4 {
  const [l, b, w, h] = rect
  return axis === 'x' ? [round4((1 - w) / 2), b, w, h] : [l, round4((1 - h) / 2), w, h]
}

/** 分数 ↔ mm：figure 尺寸来自 manifest.size_mm */
export const fracToMm = (frac: number, sizeMm: number) => Math.round(frac * sizeMm * 100) / 100
export const mmToFrac = (mm: number, sizeMm: number) => (sizeMm > 0 ? mm / sizeMm : 0)
