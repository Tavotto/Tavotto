import type { Manifest } from '@/lib/api'
import type { PageColumn } from '@/lib/pageColumns'
import type { FigureDocument, FramePad, FramePadSide, PanelObject } from '@/types/document'
import { figureFrameFacts } from '@/lib/figureEdgeFrame'
import { PAGE_EDGE_SNAP_MM, type PageEdge } from '@/lib/pageEdgeResize'

/**
 * 单图编辑的画布留白（ADR 0161）——纯函数。
 *
 * 坐标约定：留白以**导出 mm**（= 图幅 mm）记；画布（页面 mm）上的框 = 面板框四周各扩 `pad × k`，
 * `k = 面板页面宽 / 图幅宽`（单图编辑里图幅被静默校正为真实图幅，k 通常恰为 1）。
 * 「紧凑」停靠点 = manifest `figure` 元素的 `ink_bbox`（matplotlib `get_tightbbox`，不加 pad）。
 */

export const ZERO_PAD: FramePad = Object.freeze({ l: 0, t: 0, r: 0, b: 0 })
export const PAD_SIDES: readonly FramePadSide[] = ['l', 't', 'r', 'b']
/** 留白往外最多加多少（mm）：比最大的页面还宽没有意义，夹住免得一拖拖没影 */
export const FRAME_PAD_MAX_MM = 200
/** 往里裁进图幅最多裁到图还剩多少（mm） */
export const FRAME_MIN_VISIBLE_MM = 5

const round1 = (v: number) => Math.round(v * 10) / 10
const nearZero = (v: number) => Math.abs(v) < 0.05

export function isZeroPad(p: FramePad | null | undefined): boolean {
  return !p || PAD_SIDES.every((s) => nearZero(p[s]))
}

/**
 * 图幅里「内容边缘」到四边的距离（导出 mm，≥ 0）。读 manifest `figure` 元素的 `ink_bbox`（相对图幅的分数、y 向下）；
 * 旧后端没有这个字段 → null（没有「紧凑」停靠点）。只接精确 manifest（调用方经 `exactPanelManifest`）。
 */
export function inkInsetMm(manifest: Manifest | null, sizeMm: readonly [number, number] | null): FramePad | null {
  if (!manifest || !sizeMm) return null
  const fig = manifest.elements.find((e) => e.gid === 'figure') as { ink_bbox?: unknown } | undefined
  const bb = fig?.ink_bbox
  if (!Array.isArray(bb) || bb.length !== 4 || !bb.every((v) => typeof v === 'number' && Number.isFinite(v))) return null
  const [x0, y0, x1, y1] = bb as number[]
  if (!(x1 > x0) || !(y1 > y0)) return null
  const [w, h] = sizeMm
  return {
    l: Math.max(0, x0 * w),
    t: Math.max(0, y0 * h),
    r: Math.max(0, (1 - x1) * w),
    b: Math.max(0, (1 - y1) * h),
  }
}

/**
 * 此刻生效的留白：存的 mm，紧凑边换成当前内容边缘（`-inset`）。没有内容边缘事实时紧凑边沿用存的 mm。
 * 预览、画布、导出都读这一份。
 */
export function effectiveFramePad(
  doc: Pick<FigureDocument, 'framePad' | 'framePadTight'>,
  inset: FramePad | null,
): FramePad {
  const p = doc.framePad ?? ZERO_PAD
  const tight = doc.framePadTight ?? []
  if (!tight.length || !inset) return { ...p }
  const out = { ...p }
  for (const s of tight) out[s] = -inset[s]
  return out
}

/** 画布（页面 mm）上的框：面板框四周扩 `pad × k` */
export function frameCanvasBox(
  panel: { x: number; y: number; w: number; h: number },
  pad: FramePad,
  k: number,
): { x: number; y: number; w: number; h: number } {
  return {
    x: panel.x - pad.l * k,
    y: panel.y - pad.t * k,
    w: panel.w + (pad.l + pad.r) * k,
    h: panel.h + (pad.t + pad.b) * k,
  }
}

/** 拖一条边落在哪一档（胶囊的句子与语气由它选） */
export type PadEdgeState = 'grow' | 'edge' | 'trim' | 'tight' | 'blocked' | 'crop'

export interface PadEdgePlan {
  /** 这条边新的留白（导出 mm，0.1 一格） */
  value: number
  state: PadEdgeState
  /** 落在「紧凑」停靠点上（写进 `framePadTight`） */
  tight: boolean
  /** 画布宽吸上的栏宽 */
  snap: PageColumn | null
}

/**
 * 拖画布的一条边（ADR 0161）。`deltaOut` 是指针**往外**的位移（导出 mm，往外为正）。
 * 停靠点：图边（0）、内容边缘（`-inset`）、左右边还有「画布宽 = 栏宽」；离得最近、且在 `snapMm` 内的那一个吸过去。
 * 越过内容边缘往里：没按 ⌥（`allowCrop`）停在内容边缘（`blocked`）；按了才放行（`crop`）。没有内容边缘事实时不拦。
 */
export function planPadEdge(opts: {
  edge: PageEdge
  pad0: FramePad
  deltaOut: number
  sizeMm: readonly [number, number]
  inset: FramePad | null
  columns: readonly PageColumn[]
  allowCrop: boolean
  snapMm?: number
}): PadEdgePlan {
  const { edge, pad0, deltaOut, sizeMm, inset, columns, allowCrop, snapMm = PAGE_EDGE_SNAP_MM } = opts
  const horizontal = edge === 'l' || edge === 'r'
  const dim = horizontal ? sizeMm[0] : sizeMm[1]
  let v = pad0[edge] + deltaOut
  const stop = inset ? -inset[edge] : null

  type Cand = { v: number; kind: 'edge' | 'tight' | 'col'; col?: PageColumn }
  const cands: Cand[] = [{ v: 0, kind: 'edge' }]
  if (stop != null && !nearZero(stop)) cands.push({ v: stop, kind: 'tight' })
  if (horizontal) {
    const other = edge === 'l' ? pad0.r : pad0.l
    for (const c of columns) cands.push({ v: c.w - sizeMm[0] - other, kind: 'col', col: c })
  }
  let best: Cand | null = null
  for (const c of cands) {
    const d = Math.abs(v - c.v)
    if (d < snapMm && (!best || d < Math.abs(v - best.v))) best = c
  }
  if (best) v = best.v

  let state: PadEdgeState
  if (v > 0.05) state = 'grow'
  else if (v > -0.05) state = 'edge'
  else if (stop == null || v > stop + 0.05) state = 'trim'
  else if (v > stop - 0.05) state = 'tight'
  else state = 'crop'
  if (state === 'crop' && !allowCrop) {
    v = stop!
    state = 'blocked'
  }
  // 下限 = 这一维的画布至少剩 FRAME_MIN_VISIBLE_MM：图幅 + 对边此刻的留白 + 这条边的留白 ≥ 最小可见量
  const opp = horizontal ? (edge === 'l' ? pad0.r : pad0.l) : edge === 't' ? pad0.b : pad0.t
  const lo = -(dim + opp - FRAME_MIN_VISIBLE_MM)
  v = Math.min(FRAME_PAD_MAX_MM, Math.max(lo, v))
  const tight = state === 'tight' || state === 'blocked'
  return { value: tight ? v : round1(v), state, tight, snap: best?.kind === 'col' ? best.col! : null }
}

export type FrameCorner = 'tl' | 'tr' | 'bl' | 'br'

export interface CornerPlan {
  sizeMm: [number, number]
  /** 新的画布框（页面 mm）：被拖角的对角不动 */
  box: { x: number; y: number; w: number; h: number }
  /** 面板新的左上角（页面 mm） */
  pos: { x: number; y: number }
  snap: PageColumn | null
}

/**
 * 拖画布的一个角 = 改图幅（ADR 0161）：默认等比，`free`（⇧）自由比例；对角不动；留白的 mm 不变。
 * 吸附在**导出宽 = 图幅宽 + 左右留白**上（离栏宽 ±2 mm，`noSnap`（⌥）关）。`limits` 是引擎给 `size_mm` 的界。
 * `dx` / `dy` 是指针位移（页面 mm，右 / 下为正）。
 */
export function planFigureCorner(opts: {
  corner: FrameCorner
  box0: { x: number; y: number; w: number; h: number }
  sizeMm: readonly [number, number]
  pad: FramePad
  k: number
  dx: number
  dy: number
  free: boolean
  noSnap: boolean
  columns: readonly PageColumn[]
  limits?: { min?: number; max?: number }
}): CornerPlan {
  const { corner, box0, sizeMm, pad, k, dx, dy, free, noSnap, columns, limits } = opts
  const left = corner === 'tl' || corner === 'bl'
  const top = corner === 'tl' || corner === 'tr'
  const ax = left ? box0.x + box0.w : box0.x
  const ay = top ? box0.y + box0.h : box0.y
  const lim = (v: number) => Math.min(limits?.max ?? Infinity, Math.max(limits?.min ?? 1, v))
  const round2 = (v: number) => Math.round(v * 100) / 100
  // `pad` 是此刻生效的留白（紧凑边已换成当前内容边缘；图幅变后它会随新内容边缘变，这里按此刻值约束）
  const padW = pad.l + pad.r
  const padH = pad.t + pad.b
  // 图幅下限：除引擎的界外，还要让画布（图幅 + 两侧留白）至少剩 FRAME_MIN_VISIBLE_MM
  const minW = FRAME_MIN_VISIBLE_MM - padW
  const minH = FRAME_MIN_VISIBLE_MM - padH
  // 新的图幅宽（导出 mm）：画布宽变化 / k
  let w = sizeMm[0] + (left ? -dx : dx) / k
  let snap: PageColumn | null = null
  if (!noSnap) {
    let best = PAGE_EDGE_SNAP_MM
    for (const c of columns) {
      const d = Math.abs(c.w - (w + padW))
      if (d < best) {
        best = d
        snap = c
      }
    }
    if (snap) w = snap.w - padW
  }
  const limW = (v: number) => lim(Math.max(minW, v))
  const limH = (v: number) => lim(Math.max(minH, v))
  w = limW(round2(w))
  let h: number
  if (free) h = limH(round2(sizeMm[1] + (top ? -dy : dy) / k))
  else {
    h = round2((sizeMm[1] * w) / sizeMm[0])
    // 等比：高被 minH 顶住时宽跟着抬（取更严的那个），宽高比不变
    if (h < minH || h < (limits?.min ?? 1)) {
      const hh = limH(h)
      w = limW(round2((hh * sizeMm[0]) / sizeMm[1]))
      h = limH(round2((sizeMm[1] * w) / sizeMm[0]))
    } else h = limH(h)
  }
  const bw = (w + padW) * k
  const bh = (h + padH) * k
  const box = { x: left ? ax - bw : ax, y: top ? ay - bh : ay, w: bw, h: bh }
  return { sizeMm: [w, h], box, pos: { x: box.x + pad.l * k, y: box.y + pad.t * k }, snap }
}

/** 「贴合内容」：四边都落在紧凑停靠点上 */
export function tightPad(inset: FramePad): FramePad {
  return { l: -inset.l, t: -inset.t, r: -inset.r, b: -inset.b }
}

/**
 * 单图画布此刻的事实（画布、导出、胶囊共用这一份）：生效留白、内容边缘、图幅、页面 mm / 图幅 mm 的比、画布框。
 * 图幅读精确 manifest（`figureFrameFacts`）；读不到时退到面板的 `nativeW / nativeH`（只用于画纸与导出的外框，
 * 手势入口照旧要精确 manifest）。
 */
export function frameCanvasOf(
  doc: Pick<FigureDocument, 'framePad' | 'framePadTight'>,
  panel: PanelObject,
  manifest: Manifest | null,
): FrameCanvas {
  const facts = figureFrameFacts(panel, manifest)
  const sizeMm: [number, number] = facts ? facts.sizeMm : [panel.nativeW || panel.w, panel.nativeH || panel.h]
  const inset = facts ? inkInsetMm(manifest, sizeMm) : null
  const pad = effectiveFramePad(doc, inset)
  const k = sizeMm[0] > 0 ? panel.w / sizeMm[0] : 1
  return { pad, inset, sizeMm, k, exact: !!facts, box: frameCanvasBox(panel, pad, k) }
}

export interface FrameCanvas {
  pad: FramePad
  inset: FramePad | null
  sizeMm: [number, number]
  k: number
  /** 图幅来自精确 manifest（手势与「贴合内容」只在它为真时可用） */
  exact: boolean
  box: { x: number; y: number; w: number; h: number }
}
