/**
 * 等比缩放吸附（ADR 0128 §三）：固定点 F 不动，每个会跟着动的锚点 a 与候选线 c 解出
 * `s = (c − F) / (a − F)`，宽高按同一个 s 从起手尺寸缩放。
 *
 * 面板缩放是显示缩放、图内分数坐标不变，所以锚点随 s 线性变（`a(s) = F + (a − F)·s`），
 * 解是精确的。纯函数与候选 / 锚点构造都在这里，`startResizeDrag` 只负责接线。
 */
import type { Manifest } from '@/lib/api'
import {
  elementBoxOf,
  elementSnapCandidates,
  fracBoxToMm,
  isElementHidden,
  snapKindOf,
} from '@/lib/elementGeom'
import { rectsIntersect, SNAP_HOLD_FACTOR, SNAP_HOLD_YIELD_RATIO, visualBounds } from '@/lib/geometry'
import type { Rect, ResizeDir, SnapLine, SnapLineCandidates, SnapTargets } from '@/lib/geometry'
import type { CanvasObject, PanelObject } from '@/types/document'
import { panelRotation } from '@/types/document'

/** 缩放锚点：`frame` = 外框线，否则是内容（子图 / 色条）线 */
export interface ScaleAnchor {
  v: number
  frame: boolean
  /** 锚点所属元素在 s = 1（起手）时的页面 mm 框；外框锚点 = 起手外框（吸附反馈用） */
  rect: Rect
}

export interface ScaleAnchors {
  xs: ScaleAnchor[]
  ys: ScaleAnchor[]
}

/** 吸住后保持：一次手势一份；下标指向本次手势固定的锚点表与候选表 */
export interface ScaleHold {
  held: { axis: 'x' | 'y'; ai: number; li: number } | null
}

export const createScaleHold = (): ScaleHold => ({ held: null })

export interface ScalePick {
  s: number
  axis: 'x' | 'y'
  anchor: ScaleAnchor
  line: SnapLine
}

export interface ScaleSnapParams {
  /** 固定点（页面 mm） */
  fixed: { x: number; y: number }
  /** 起手尺寸，用来判最小尺寸 */
  size: { w: number; h: number }
  /** 当前（未吸附）缩放倍数；距离按锚点此刻的位置量 */
  s0: number
  anchors: ScaleAnchors
  cands: SnapLineCandidates
  tol: number
  minW?: number
  minH?: number
  hold?: ScaleHold
}

/** 放开一对的距离 = 这个倍数 × 容差（直接复用图内吸附的 SNAP_HOLD_FACTOR，两处永远同值） */
const HOLD_FACTOR = SNAP_HOLD_FACTOR

/**
 * 手柄跳变上限 = 这个倍数 × 容差：这次吸附让框尺寸变化（≈ 被拖手柄的位移 mm）超过它的对不吸。
 * 比 1× 更紧——用户在手感调试页上实拖后拍板 0.5（2026-10-15，ADR 0128），宁可少吸也不让手柄一下蹦远。
 */
export const SCALE_JUMP_FACTOR = 0.5

const FLOOR_EPS = 1e-6

/** 页面 / 参考线 / 网格对谁都是第一档；外框↔外框、内容↔内容也是；外框与内容混搭是第二档 */
export function scaleTier(anchorIsFrame: boolean, line: SnapLine): 0 | 1 {
  if (line.kind === 'page' || line.kind === 'guide' || line.kind === 'grid') return 0
  return anchorIsFrame === (line.kind === 'frame') ? 0 : 1
}

/**
 * 在锚点 × 候选线里挑一对：容差内（按锚点**当前位置**到线的距离）→ 先档位、后距离。
 * 解出的 s 太小（宽或高低于最小尺寸）的对不参与。带 `hold` 时上一帧那一对只要距离仍
 * < 1.75×容差就继续吸它，否则放开重挑。X / Y 合在一起挑一对（宽高共用一个 s）。
 */
export function snapScaleNearest(p: ScaleSnapParams): ScalePick | null {
  const { fixed, size, s0, anchors, cands, tol, hold } = p
  const minW = p.minW ?? 3
  const minH = p.minH ?? 2
  const axes = [
    { axis: 'x' as const, f: fixed.x, as: anchors.xs, ls: cands.xs },
    { axis: 'y' as const, f: fixed.y, as: anchors.ys, ls: cands.ys },
  ]
  const evalPair = (axis: 'x' | 'y', ai: number, li: number) => {
    const a = axes[axis === 'x' ? 0 : 1]
    const anchor = a.as[ai]
    const line = a.ls[li]
    if (!anchor || !line) return null
    const off = anchor.v - a.f
    if (Math.abs(off) < FLOOR_EPS) return null
    const s = (line.v - a.f) / off
    if (!Number.isFinite(s) || s * size.w < minW || s * size.h < minH) return null
    // 手柄跳变：这次吸附让框尺寸变化多少 mm（≈ 被拖手柄的位移）；锚点贴近固定点时
    // 小误差会被放大成巨大缩放，跳变超过 0.5×容差的对不吸
    const jump = Math.abs(s - s0) * Math.max(size.w, size.h)
    return { s, dist: Math.abs(a.f + off * s0 - line.v), jump, anchor, line }
  }

  type Pick = { tier: number; dist: number; axis: 'x' | 'y'; ai: number; li: number; s: number }
  const better = (c: Pick, cur: Pick | null) =>
    !cur || c.tier < cur.tier || (c.tier === cur.tier && c.dist < cur.dist)

  // 被保持的那一对（仍在 1.75×容差内才算数）；让位对所有合格对判断，不只看总冠军：
  // 档位更好但太远的一对不能遮住「档位不更差、且近到 0.25 倍以内」的另一对
  const held = hold?.held
  const heldEval = held ? evalPair(held.axis, held.ai, held.li) : null
  const holdOk =
    !!held && !!heldEval && heldEval.dist < tol * HOLD_FACTOR && heldEval.jump < tol * SCALE_JUMP_FACTOR * HOLD_FACTOR
  const heldTier = held && holdOk ? scaleTier(axes[held.axis === 'x' ? 0 : 1].as[held.ai].frame, heldEval!.line) : 0

  let best: Pick | null = null
  let yieldTo: Pick | null = null
  for (const { axis, as, ls } of axes) {
    for (let ai = 0; ai < as.length; ai++) {
      for (let li = 0; li < ls.length; li++) {
        const r = evalPair(axis, ai, li)
        if (!r || r.dist >= tol || r.jump >= tol * SCALE_JUMP_FACTOR) continue
        const c: Pick = { tier: scaleTier(as[ai].frame, ls[li]), dist: r.dist, axis, ai, li, s: r.s }
        if (better(c, best)) best = c
        if (
          holdOk &&
          !(axis === held!.axis && ai === held!.ai && li === held!.li) &&
          c.tier <= heldTier &&
          c.dist < heldEval!.dist * SNAP_HOLD_YIELD_RATIO &&
          better(c, yieldTo)
        )
          yieldTo = c
      }
    }
  }

  if (holdOk && !yieldTo) return { s: heldEval!.s, axis: held!.axis, anchor: heldEval!.anchor, line: heldEval!.line }
  if (yieldTo) best = yieldTo // 让位时挑合格对里按档位 / 距离最好的（ADR 0128 §一修订）
  if (hold) hold.held = best && { axis: best.axis, ai: best.ai, li: best.li }
  if (!best) return null
  const a = axes[best.axis === 'x' ? 0 : 1]
  return { s: best.s, axis: best.axis, anchor: a.as[best.ai], line: a.ls[best.li] }
}

/**
 * 固定点：与 `resizeRect` 实际落位一致——东 / 南向柄固定在左 / 上边，西 / 北向柄固定在右 / 下边，
 * 没提到的轴固定在起手框的左 / 上边（边柄等比时 `resizeRect` 保持 x / y 不变，只伸缩 w / h）。
 */
export function scaleFixedPoint(orig: Rect, dir: ResizeDir): { x: number; y: number } {
  return {
    x: dir.includes('w') ? orig.x + orig.w : orig.x,
    y: dir.includes('n') ? orig.y + orig.h : orig.y,
  }
}

/** 按倍数 s 缩放后的框（与 `resizeRect` 的 x / y 落位同口径） */
export function scaledRect(orig: Rect, dir: ResizeDir, s: number): Rect {
  const w = orig.w * s
  const h = orig.h * s
  return {
    x: dir.includes('w') ? orig.x + orig.w - w : orig.x,
    y: dir.includes('n') ? orig.y + orig.h - h : orig.y,
    w,
    h,
  }
}

const threeX = (r: Rect) => [r.x, r.x + r.w / 2, r.x + r.w]
const threeY = (r: Rect) => [r.y, r.y + r.h / 2, r.y + r.h]

/** 外框的三条线（不动的那条由 snapScaleNearest 按「相对 F 偏移为 0」自然略过） */
export function frameAnchors(r: Rect): ScaleAnchor[][] {
  const rect = { x: r.x, y: r.y, w: r.w, h: r.h }
  return [
    threeX(r).map((v) => ({ v, frame: true, rect })),
    threeY(r).map((v) => ({ v, frame: true, rect })),
  ]
}

/** 面板可当「内容」参与缩放吸附：未旋转、未翻转 */
const plainPanel = (p: PanelObject) => !panelRotation(p) && !p.flipH && !p.flipV

/** 面板里子图（非色条）与色条的页面 mm 框；裁剪窗外的整条线由调用方按轴过滤 */
function contentBoxes(panel: PanelObject, manifest: Manifest): { kind: 'axes' | 'colorbar'; rect: Rect }[] {
  const out: { kind: 'axes' | 'colorbar'; rect: Rect }[] = []
  for (const el of manifest.elements) {
    if (el.role !== 'axes' || el.geom_gid || el.arrow_endpoints || isElementHidden(el)) continue
    const kind = snapKindOf(el)
    if (kind !== 'axes' && kind !== 'colorbar') continue
    const box = elementBoxOf(panel, el)
    if (!box || box[2] <= 0 || box[3] <= 0) continue
    const rect = fracBoxToMm(panel, box)
    // 与面板可见窗没有二维交集的框整个丢掉：只按轴过滤会让「x 在窗内、y 在窗外」的框留下看不见的线
    if (!rectsIntersect(rect, panel)) continue
    out.push({ kind, rect })
  }
  return out
}

const within = (v: number, lo: number, hi: number) => v >= lo - FLOOR_EPS && v <= hi + FLOOR_EPS

/**
 * 缩放锚点：外框三线 + （面板、未旋转 / 翻转、有权威 manifest 时）子图与色条的三线（「吸附到对象」开着时）。
 * 在面板裁剪窗外的线不算——画布上看不见。
 */
export function scaleAnchorsFor(
  target: CanvasObject,
  manifest: Manifest | null,
  /** 「吸附到对象」：关掉时自身内容锚点（子图 / 色条）不参与，只剩外框 */
  objects = true,
): ScaleAnchors {
  const [xs, ys] = frameAnchors(target)
  const anchors: ScaleAnchors = { xs, ys }
  if (!objects || target.type !== 'panel' || !manifest || !plainPanel(target)) return anchors
  for (const { rect } of contentBoxes(target, manifest)) {
    for (const v of threeX(rect))
      if (within(v, target.x, target.x + target.w)) anchors.xs.push({ v, frame: false, rect })
    for (const v of threeY(rect))
      if (within(v, target.y, target.y + target.h)) anchors.ys.push({ v, frame: false, rect })
  }
  return anchors
}

export interface ScaleCandidateInput {
  objects: CanvasObject[]
  exclude: ReadonlySet<string>
  page: { w: number; h: number }
  guides: { axis: 'x' | 'y'; pos: number }[]
  prefs: SnapTargets
  /** 权威 manifest；拿不到返回 null（ADR 0017） */
  manifestOf: (panel: PanelObject) => Manifest | null
}

/**
 * 缩放专用候选（带元信息）：`candidatesFor` 的那些——页面、别的对象外框、参考线、网格、
 * 别的图元素中心线——再加别的面板的子图 / 色条左右上下边。只给缩放用，平移候选不变。
 */
export function scaleSnapCandidates(i: ScaleCandidateInput): SnapLineCandidates {
  const { page, prefs } = i
  const xs: SnapLine[] = []
  const ys: SnapLine[] = []
  const pushX = (v: number, l: Partial<SnapLine> & Pick<SnapLine, 'kind'>) => xs.push({ v, edge: null, ...l })
  const pushY = (v: number, l: Partial<SnapLine> & Pick<SnapLine, 'kind'>) => ys.push({ v, edge: null, ...l })
  for (const v of [0, page.w / 2, page.w]) pushX(v, { kind: 'page' })
  for (const v of [0, page.h / 2, page.h]) pushY(v, { kind: 'page' })
  if (prefs.objects) {
    for (const o of i.objects) {
      if (i.exclude.has(o.id) || o.hidden) continue
      // 画布上看得见的是旋转后的外接盒（面板的 x/y/w/h 本来就是，text / arrow / shape 要绕中心转出来）
      const rect = visualBounds(o)
      const meta = { kind: 'frame' as const, rect, panelId: o.type === 'panel' ? o.id : undefined }
      threeX(rect).forEach((v, e) => pushX(v, { ...meta, edge: e as 0 | 1 | 2 }))
      threeY(rect).forEach((v, e) => pushY(v, { ...meta, edge: e as 0 | 1 | 2 }))
    }
  }
  if (prefs.guides) for (const g of i.guides) (g.axis === 'x' ? pushX : pushY)(g.pos, { kind: 'guide' })
  if (prefs.grid && prefs.gridSize > 0) {
    for (let v = 0; v <= page.w + 1e-6; v += prefs.gridSize) pushX(v, { kind: 'grid' })
    for (let v = 0; v <= page.h + 1e-6; v += prefs.gridSize) pushY(v, { kind: 'grid' })
  }
  if (prefs.objects) {
    for (const o of i.objects) {
      if (o.type !== 'panel' || o.hidden || i.exclude.has(o.id)) continue
      const manifest = i.manifestOf(o)
      if (!manifest) continue
      const plain = plainPanel(o)
      const boxes = plain ? contentBoxes(o, manifest) : []
      // 子图 / 色条的中线以 axes / colorbar 的身份出线（反馈要认出对方是谁）；
      // elementSnapCandidates 里与之重合的 'other' 线去掉，免得并列时 'other' 抢先
      const dup = (v: number, axis: 'x' | 'y') =>
        boxes.some(({ rect }) => Math.abs(v - (axis === 'x' ? rect.x + rect.w / 2 : rect.y + rect.h / 2)) < FLOOR_EPS)
      const extra = elementSnapCandidates(o, manifest)
      for (const v of extra.xs) if (!dup(v, 'x')) pushX(v, { kind: 'other', panelId: o.id })
      for (const v of extra.ys) if (!dup(v, 'y')) pushY(v, { kind: 'other', panelId: o.id })
      for (const { kind, rect } of boxes) {
        const meta = { kind, panelId: o.id, rect }
        for (const e of [0, 1, 2] as const) {
          const vx = rect.x + (rect.w * e) / 2
          const vy = rect.y + (rect.h * e) / 2
          if (within(vx, o.x, o.x + o.w)) pushX(vx, { ...meta, edge: e })
          if (within(vy, o.y, o.y + o.h)) pushY(vy, { ...meta, edge: e })
        }
      }
    }
  }
  return { xs, ys }
}

/** 吸附后的反馈框：锚点所属框绕固定点 F 按倍数 s 缩放（面板缩放是显示缩放，框随 s 线性变） */
export function scaledAnchorRect(anchor: ScaleAnchor, fixed: { x: number; y: number }, s: number): Rect {
  const r = anchor.rect
  return { x: fixed.x + (r.x - fixed.x) * s, y: fixed.y + (r.y - fixed.y) * s, w: r.w * s, h: r.h * s }
}
