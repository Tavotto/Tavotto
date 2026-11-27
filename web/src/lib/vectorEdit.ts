/**
 * 改图助手改画布矢量对象（ADR 0212 第 6 期）的纯函数部分：选区判据、发给后端的内存快照、方案落位。
 * 几何与补丁都在后端（`/api/vector-edit/plan`，只算不写）；前端不重写补丁操作，只做三件事：
 *
 * 1. **判据**：选区里有矢量对象才走这条路（`vectorSelectionOf`）；
 * 2. **快照**：只发画布上的矢量对象 + 子图的外框（`snapshotOf`）——子图的脚本、数据、覆盖项、文件名都不出本机（ADR 0212 修订 §5）；
 * 3. **落位**：把「快照 + 方案」拼成与「并进 Agent 改动」同一种差异（`AgentCanvasDiff`），交给同一个 `planAgentMerge` 做字段级合并，
 *    新增 / 删除 / 重排 / 布局组都由它管（一份落位规则，不另写一套）。
 */
import type { AgentCanvasDiff, VectorEditResult } from '@/lib/api'
import { planAgentMerge, type MergedCanvas } from '@/lib/agentMerge'
import type { CanvasObject, FigureDocument, LayoutGroup } from '@/types/document'

/** 后端 `vectoredit.EDITABLE_TYPES` 的镜像：能被补丁改的画布矢量对象 */
export const VECTOR_TYPES: readonly string[] = ['text', 'arrow', 'shape', 'path']

export const isVectorObject = (o: Pick<CanvasObject, 'type'>): boolean => VECTOR_TYPES.includes(o.type)

/** 选区里的矢量对象 id（按选区顺序；不在这张画布上的、隐藏的也算——隐藏对象照样能被指令点名） */
export function vectorSelectionOf(objects: readonly CanvasObject[], ids: readonly string[]): string[] {
  const vec = new Set(objects.filter(isVectorObject).map((o) => o.id))
  return ids.filter((id) => vec.has(id))
}

/** 子图对象只留位置与变换：后端的锚点解析 / 对齐 / 核对要外框，不要它的脚本、数据与覆盖项 */
const PANEL_KEYS = ['id', 'type', 'x', 'y', 'w', 'h', 'rotation', 'flipH', 'flipV', 'crop', 'hidden', 'locked', 'groupId', 'groupPath', 'clipMask', 'clipOff'] as const

/** 一个对象在快照里的样子（矢量对象原样；子图只留外框；其余类型不进快照） */
export function projectObject(o: CanvasObject): CanvasObject | null {
  if (isVectorObject(o)) return structuredClone(o)
  if (o.type === 'panel') {
    const src = o as unknown as Record<string, unknown>
    const out: Record<string, unknown> = {}
    for (const k of PANEL_KEYS) if (src[k] !== undefined) out[k] = structuredClone(src[k])
    return out as unknown as CanvasObject
  }
  return null
}

export interface VectorSnapshot {
  /** 发给后端的画布（文档形态）：页面、投影过的对象、布局组 */
  canvas: { id: string; page: FigureDocument['page']; objects: CanvasObject[]; layoutGroups?: LayoutGroup[] }
  /** 快照里的对象 id（按画布顺序）：方案回来后核「点名的对象还是出发时那样」用 */
  byId: Map<string, CanvasObject>
}

export function snapshotOf(doc: Pick<FigureDocument, 'page' | 'objects' | 'layoutGroups'>, canvasId: string): VectorSnapshot {
  const objects = doc.objects.flatMap((o) => {
    const p = projectObject(o)
    return p ? [p] : []
  })
  return {
    canvas: {
      id: canvasId,
      page: structuredClone(doc.page),
      objects,
      ...(doc.layoutGroups?.length ? { layoutGroups: structuredClone(doc.layoutGroups) } : {}),
    },
    byId: new Map(objects.map((o) => [o.id, o])),
  }
}

/** 按 JSON 语义比（键序无关；`undefined` 与缺这个键同义） */
function canon(v: unknown): unknown {
  if (Array.isArray(v)) return v.map((x) => (x === undefined ? null : canon(x)))
  if (v && typeof v === 'object') {
    const out: Record<string, unknown> = {}
    for (const k of Object.keys(v as object).sort()) {
      const x = (v as Record<string, unknown>)[k]
      if (x !== undefined) out[k] = canon(x)
    }
    return out
  }
  return v
}
const same = (a: unknown, b: unknown): boolean => JSON.stringify(canon(a)) === JSON.stringify(canon(b))
export const sameJson = same

/** 一份方案是不是真的有东西要改（空 ops / 全被丢掉 = 没有） */
export function hasEdits(r: VectorEditResult): boolean {
  return (r.objects?.length ?? 0) > 0 || (r.deleted?.length ?? 0) > 0
}

/** 方案点名的对象 id：改后的（含新生成的）与被删的 */
export function namedIds(r: VectorEditResult): string[] {
  return [...new Set([...(r.objects ?? []).map((o) => o.id), ...(r.deleted ?? [])])]
}

/**
 * 「快照 + 方案」→ 与并进 Agent 改动同形的差异。快照里没有的 id = 新生成的（`added`）；有的 = 改（`changed`）；`deleted` 取快照里的原样。
 * 布局组只在与出发时不同才带（`planAgentMerge` 据此判「桌面这边还是基线」）。
 */
export function diffOf(snap: VectorSnapshot, r: VectorEditResult): AgentCanvasDiff {
  const added: CanvasObject[] = []
  const changed: { before: CanvasObject; after: CanvasObject }[] = []
  for (const o of r.objects ?? []) {
    const before = snap.byId.get(o.id)
    if (before) changed.push({ before, after: o })
    else added.push(o)
  }
  const deleted = (r.deleted ?? []).flatMap((id) => {
    const b = snap.byId.get(id)
    return b ? [b] : []
  })
  const baseOrder = snap.canvas.objects.map((o) => o.id)
  const base = snap.canvas.layoutGroups?.length ? snap.canvas.layoutGroups : null
  const after = r.layout_groups?.length ? r.layout_groups : null
  return {
    id: snap.canvas.id,
    added,
    changed,
    deleted,
    base_order: baseOrder,
    order: r.order ?? baseOrder,
    reordered: !!r.reordered,
    ...(same(base, after) ? {} : { layout_groups: { before: base, after } }),
  }
}

/** 方案点名的对象此刻还是不是出发时那样（子图只比外框那几个键）；变了 = 「图改过了」 */
export function baselineHolds(snap: VectorSnapshot, r: VectorEditResult, current: readonly CanvasObject[]): boolean {
  const now = new Map(current.map((o) => [o.id, o]))
  for (const id of namedIds(r)) {
    const before = snap.byId.get(id)
    if (!before) continue // 新生成的：出发时本来就没有
    const cur = now.get(id)
    const curP = cur ? projectObject(cur) : null
    if (!curP || !same(curP, before)) return false
  }
  return true
}

/**
 * 方案已经写进文档的样子（「已应用」的判据，**按对象状态、不按历史条目**）：被删的不在了、新生成的在、改过的对象上被补丁动过的那几个字段是方案给的值。
 * 不看历史条目是因为应用之后别的写入（文字量好尺寸后的回写）会把那一条历史换成合并后的新条目，认引用就认丢了；
 * 只看补丁动过的字段，回写的 w / h 不影响。
 */
export function landedHolds(snap: VectorSnapshot, r: VectorEditResult, current: readonly CanvasObject[]): boolean {
  const now = new Map(current.map((o) => [o.id, o as unknown as Record<string, unknown>]))
  if ((r.deleted ?? []).some((id) => now.has(id))) return false
  for (const after of r.objects ?? []) {
    const cur = now.get(after.id)
    if (!cur) return false
    const before = snap.byId.get(after.id) as unknown as Record<string, unknown> | undefined
    if (!before) continue // 新生成的：在就算（它的尺寸可能被量过回写）
    const a = after as unknown as Record<string, unknown>
    for (const k of new Set([...Object.keys(before), ...Object.keys(a)])) {
      if (!same(before[k], a[k]) && !same(cur[k], a[k])) return false
    }
  }
  return true
}

/** 此刻的文档上落这份方案：基线对得上才算，落位交给 `planAgentMerge`；null = 对不上（图改过了） */
export function landVector(snap: VectorSnapshot, r: VectorEditResult, doc: Pick<FigureDocument, 'objects' | 'layoutGroups'>): MergedCanvas | null {
  if (!baselineHolds(snap, r, doc.objects)) return null
  return planAgentMerge(doc.objects, doc.layoutGroups, diffOf(snap, r))
}

/** 方案里没过核对的条数（`checks` 里 `status === 'fail'`） */
export function failedChecks(r: VectorEditResult): number {
  return (r.checks ?? []).filter((c) => c.status === 'fail').length
}

/** 方案改了几个对象（改 + 新生成 + 删） */
export function touchedCount(r: VectorEditResult): number {
  return namedIds(r).length
}
