import type { CanvasObject, FigureDocument, LayoutGroup } from '@/types/document'
import { panelAspectLocked } from '@/types/document'

/**
 * 布局组重排：纯计算，不碰 store。
 *
 * 锚点 = 未固定成员当前包围盒的左上角，重排只重新分配组内相对位置——
 * 整组拖到哪里，排布就在哪里展开；固定（layoutPinned）的成员原地不动。
 */

export interface ReflowPatch {
  id: string
  x: number
  y: number
  w?: number
  h?: number
}

/** 组的有效成员（按 order 排序；不在文档里的 id 忽略，新成员补在最后） */
export function groupMembers(doc: FigureDocument, g: LayoutGroup): CanvasObject[] {
  const inGroup = doc.objects.filter((o) => o.groupId === g.id && !o.hidden)
  const rank = new Map(g.order.map((id, i) => [id, i]))
  return inGroup
    .slice()
    .sort((a, b) => (rank.get(a.id) ?? 1e9) - (rank.get(b.id) ?? 1e9))
}

const bounds = (objs: CanvasObject[]) => {
  const x = Math.min(...objs.map((o) => o.x))
  const y = Math.min(...objs.map((o) => o.y))
  return { x, y }
}

/** 统一尺寸后的成员宽高（面板锁比例时另一边联动） */
function uniformSize(
  o: CanvasObject,
  uniform: LayoutGroup['uniform'],
  target: number,
): { w: number; h: number } {
  if (!uniform) return { w: o.w, h: o.h }
  const keepAspect = o.type === 'panel' && panelAspectLocked(o)
  if (uniform === 'width') {
    const w = target
    const h = keepAspect ? o.h * (w / o.w) : o.h
    return { w, h }
  }
  const h = target
  // 文字高度由内容决定，等高不动它
  if (o.type === 'text') return { w: o.w, h: o.h }
  const w = keepAspect ? o.w * (h / o.h) : o.w
  return { w, h }
}

/** 计算重排后的每个成员位置（含统一尺寸）；成员不足 2 个返回空 */
export function reflowPatches(doc: FigureDocument, g: LayoutGroup): ReflowPatch[] {
  const all = groupMembers(doc, g)
  const movable = all.filter((o) => !o.layoutPinned && !o.locked)
  if (movable.length < 2) return []

  const origin = bounds(movable)
  const uniformTarget =
    g.uniform === 'width'
      ? Math.max(...movable.map((o) => o.w))
      : g.uniform === 'height'
        ? Math.max(...movable.filter((o) => o.type !== 'text').map((o) => o.h))
        : 0

  const sized = movable.map((o) => ({
    o,
    ...uniformSize(o, g.uniform ?? null, uniformTarget),
  }))

  const out: ReflowPatch[] = []
  const push = (o: CanvasObject, x: number, y: number, w: number, h: number) => {
    out.push({
      id: o.id,
      x,
      y,
      ...(Math.abs(w - o.w) > 1e-6 || Math.abs(h - o.h) > 1e-6 ? { w, h } : {}),
    })
  }

  if (g.kind === 'row') {
    const rowH = Math.max(...sized.map((s) => s.h))
    let cx = origin.x
    for (const s of sized) {
      const y =
        g.align === 'center'
          ? origin.y + (rowH - s.h) / 2
          : g.align === 'end'
            ? origin.y + rowH - s.h
            : origin.y
      push(s.o, cx, y, s.w, s.h)
      cx += s.w + g.gap
    }
  } else if (g.kind === 'col') {
    const colW = Math.max(...sized.map((s) => s.w))
    let cy = origin.y
    for (const s of sized) {
      const x =
        g.align === 'center'
          ? origin.x + (colW - s.w) / 2
          : g.align === 'end'
            ? origin.x + colW - s.w
            : origin.x
      push(s.o, x, cy, s.w, s.h)
      cy += s.h + g.gap
    }
  } else {
    // grid：列宽 = 该列最宽成员，行高 = 该行最高成员
    const cols = Math.max(1, Math.min(g.cols ?? 2, sized.length))
    const colW: number[] = Array(cols).fill(0)
    const rowH: number[] = []
    sized.forEach((s, i) => {
      const c = i % cols
      const r = Math.floor(i / cols)
      colW[c] = Math.max(colW[c], s.w)
      rowH[r] = Math.max(rowH[r] ?? 0, s.h)
    })
    const colX: number[] = []
    let acc = origin.x
    for (let c = 0; c < cols; c++) {
      colX[c] = acc
      acc += colW[c] + g.gap
    }
    const rowY: number[] = []
    acc = origin.y
    for (let r = 0; r < rowH.length; r++) {
      rowY[r] = acc
      acc += rowH[r] + g.gap
    }
    sized.forEach((s, i) => {
      const c = i % cols
      const r = Math.floor(i / cols)
      const x =
        g.align === 'center'
          ? colX[c] + (colW[c] - s.w) / 2
          : g.align === 'end'
            ? colX[c] + colW[c] - s.w
            : colX[c]
      push(s.o, x, rowY[r], s.w, s.h)
    })
  }

  // 已经就位的成员不需要 patch（避免空 commit 与重排循环）
  const byId = new Map(movable.map((o) => [o.id, o]))
  return out.filter((p) => {
    const o = byId.get(p.id)!
    return (
      Math.abs(p.x - o.x) > 0.01 ||
      Math.abs(p.y - o.y) > 0.01 ||
      (p.w != null && Math.abs(p.w - o.w) > 0.01) ||
      (p.h != null && Math.abs(p.h - o.h) > 0.01)
    )
  })
}

/** 成员尺寸签名：自动重排的触发依据（位置变化不触发，避免和手动拖拽打架） */
export function sizeSignature(doc: FigureDocument, g: LayoutGroup): string {
  return groupMembers(doc, g)
    .map((o) => `${o.id}:${o.w.toFixed(2)}x${o.h.toFixed(2)}:${o.layoutPinned ? 1 : 0}`)
    .join('|')
}
