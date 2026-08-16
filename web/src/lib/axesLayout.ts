import type { AlignMode, ResizeDir } from './geometry'

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
 * 对一组元素做对齐 / 分布，返回需要改动的新框（top-origin 分数坐标）。
 * 基准（等宽/等高）取 items 末位——与画布对象层「最后选中的为基准」保持一致。
 */
export function layoutBoxes(items: AlignItem[], mode: AlignMode): Map<string, Rect4> {
  const out = new Map<string, Rect4>()
  if (items.length < 2) return out

  const minL = Math.min(...items.map((i) => L(i.box)))
  const maxR = Math.max(...items.map((i) => R(i.box)))
  const minT = Math.min(...items.map((i) => T(i.box)))
  const maxB = Math.max(...items.map((i) => B(i.box)))
  const ref = items[items.length - 1]

  const put = (key: string, box: Rect4) => out.set(key, box.map(round4) as Rect4)

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
      put(it.key, horizontal ? [cur, y, w, h] : [x, cur, w, h])
      cur += (horizontal ? w : h) + gap
    }
    return out
  }

  for (const it of items) {
    const [x, y, w, h] = it.box
    switch (mode) {
      case 'left':
        put(it.key, [minL, y, w, h])
        break
      case 'right':
        put(it.key, [maxR - w, y, w, h])
        break
      case 'hcenter':
        put(it.key, [(minL + maxR) / 2 - w / 2, y, w, h])
        break
      case 'top':
        put(it.key, [x, minT, w, h])
        break
      case 'bottom':
        put(it.key, [x, maxB - h, w, h])
        break
      case 'vcenter':
        put(it.key, [x, (minT + maxB) / 2 - h / 2, w, h])
        break
      case 'samew':
        if (it !== ref) put(it.key, [x, y, ref.box[2], h])
        break
      case 'sameh':
        // 等高保持顶边不动，视觉上更符合预期
        if (it !== ref) put(it.key, [x, y, w, ref.box[3]])
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
