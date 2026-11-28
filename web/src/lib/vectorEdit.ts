/**
 * 改图助手改画布矢量对象（ADR 0212 第 6 期）的纯函数部分：选区判据、发给后端的内存快照、方案落位。
 * 几何与补丁都在后端（`/api/vector-edit/plan`，只算不写）；前端不重写补丁操作，只做三件事：
 *
 * 1. **判据**：选区里有矢量对象才走这条路（`vectorSelectionOf`）；
 * 2. **快照**：画布上的矢量对象 + 子图的外框（`snapshotOf`）；子图另带后端实测要的输入（`fileId` / `fileKind` / 未存盘的覆盖项，冻结 / 运行时只带布尔）。
 *    脚本、数据与图的内容不带；这份快照只发给本机后端，发给所选模型的内容由后端的提示另定（`docs/privacy.md`）；
 * 3. **落位**：把「快照 + 方案」拼成与「并进 Agent 改动」同一种差异（`AgentCanvasDiff`），交给同一个 `planAgentMerge` 做字段级合并，
 *    新增 / 删除 / 重排 / 布局组都由它管（一份落位规则，不另写一套）。
 */
import { t as translate } from '@/i18n'
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

/** 子图对象只留位置与变换，另加后端实测要的输入（ADR 0208 第 ③ 期 3b）：`fileId` / `fileKind` / `overrides`（还没存盘的样式补丁，量的就是用户眼前的图）。
 * 脚本、数据、图的内容都不带；冻结 / 运行时的子图只带「有没有」的布尔（后端据此退到只有外框），不带内容 */
const PANEL_KEYS = ['id', 'type', 'x', 'y', 'w', 'h', 'rotation', 'flipH', 'flipV', 'crop', 'hidden', 'locked', 'groupId', 'groupPath', 'groupNames', 'clipMask', 'clipOff'] as const

const MEASURE_KEYS = ['fileId', 'fileKind', 'overrides'] as const
/** 只带布尔：这张子图是不是冻结拼合的（`figureSnapshot` / `figureOrigin`）、是不是运行时素材（`source`） */
const MEASURE_FLAGS = ['figureSnapshot', 'figureOrigin', 'source'] as const

/** 一个对象在快照里的样子（矢量对象原样；子图只留外框；其余类型不进快照） */
export function projectObject(o: CanvasObject): CanvasObject | null {
  if (isVectorObject(o)) return structuredClone(o)
  if (o.type === 'panel') {
    const src = o as unknown as Record<string, unknown>
    const out: Record<string, unknown> = {}
    for (const k of PANEL_KEYS) if (src[k] !== undefined) out[k] = structuredClone(src[k])
    for (const k of MEASURE_KEYS) if (src[k] !== undefined) out[k] = structuredClone(src[k])
    for (const k of MEASURE_FLAGS) if (src[k] != null) out[k] = true
    return out as unknown as CanvasObject
  }
  return null
}

export interface VectorSnapshot {
  /** 发给后端的画布（文档形态）：页面、投影过的对象、布局组 */
  canvas: { id: string; page: FigureDocument['page']; objects: CanvasObject[]; layoutGroups?: LayoutGroup[] }
  /** 快照里的对象 id（按画布顺序）：方案回来后核「点名的对象还是出发时那样」用 */
  byId: Map<string, CanvasObject>
  /** 子图对象 id → 场景里的短号（P1、P2…，与后端 `unmeasured` 的键同序）：说「没量到」的那张图用 */
  panelRefs: Map<string, string>
  /** 子图对象 id → 界面叫法（「第 N 张图」，按画布上可见子图的顺序，与场景里的短号 P1、P2 同序）：清单里不出现 id / 短号 */
  panelLabels: Map<string, string>
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
    panelRefs: new Map(doc.objects.filter((o) => o.type === 'panel' && !o.hidden).map((o, i) => [o.id, `P${i + 1}`] as const)),
    panelLabels: new Map(
      doc.objects
        .filter((o) => o.type === 'panel' && !o.hidden)
        .map((o, i) => [o.id, translate('changePlan.vector.panelN', { ns: 'ai', n: i + 1 })] as const),
    ),
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
  // 只改层级的方案（「移到最前」）被动对象的字段不变、改动记在 `order` / `reordered` 与 `changes` 里：也算有改动
  return (r.objects?.length ?? 0) > 0 || (r.deleted?.length ?? 0) > 0 || !!r.reordered
}

/** 方案点名的对象 id：改后的（含新生成的）、被删的，以及新对象的位置所依据的子图（`depends_on`：它们被挪过 = 新对象会落错地方） */
export function namedIds(r: VectorEditResult): string[] {
  return [...new Set([...(r.objects ?? []).map((o) => o.id), ...(r.deleted ?? []), ...(r.changes ?? []).map((c) => c.id), ...(r.changes ?? []).flatMap((c) => c.depends_on ?? [])])]
}

/** 把 `ids` 里属于 `among` 的按原顺序取出来（对象顺序的比较只看双方都认得的那些，别处新加的对象不碍事） */
const orderAmong = (ids: readonly string[], among: ReadonlySet<string>): string[] => ids.filter((id) => among.has(id))

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
  // 方案改了层级时，出发时那些对象的上下顺序也得没被动过（否则落位的顺序合并会冲突）
  if (r.reordered) {
    const base = snap.canvas.objects.map((o) => o.id)
    const have = new Set(current.map((o) => o.id))
    const baseSet = new Set(base.filter((id) => have.has(id)))
    if (!same(orderAmong(current.map((o) => o.id), new Set(base)), orderAmong(base, baseSet))) return false
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
  // 改了层级的方案：当前画布上这些对象的顺序与方案给的顺序一致才算写进去了
  if (r.reordered && r.order) {
    const want = new Set(r.order)
    const cur = orderAmong(current.map((o) => o.id), want)
    const exp = orderAmong(r.order, new Set(cur))
    if (!same(cur, exp)) return false
  }
  return true
}

/** 此刻的文档上落这份方案：基线对得上才算，落位交给 `planAgentMerge`；null = 对不上（图改过了） */
export function landVector(snap: VectorSnapshot, r: VectorEditResult, doc: Pick<FigureDocument, 'objects' | 'layoutGroups'>): MergedCanvas | null {
  if (!baselineHolds(snap, r, doc.objects)) return null
  return planAgentMerge(doc.objects, doc.layoutGroups, diffOf(snap, r))
}

/**
 * 对比图的取景：被动到的对象（改前 ∪ 改后的框）加一圈留白，整张画布上只占一小块时才裁（占大半就用整页，裁了也看不出差别）。
 * 前后两张用同一个框；返回 null = 用整页。框用未旋转的包围盒，留白够大，旋转的对象也不会被切。
 */
export function previewFrame(
  page: { w: number; h: number },
  before: readonly CanvasObject[],
  after: readonly CanvasObject[],
  r: VectorEditResult,
): { x: number; y: number; w: number; h: number } | null {
  const named = new Set(namedIds(r))
  const boxes = [...before, ...after].filter((o) => named.has(o.id)).map((o) => [o.x, o.y, o.x + o.w, o.y + o.h] as const)
  if (!boxes.length) return null
  const x0 = Math.min(...boxes.map((b) => b[0]))
  const y0 = Math.min(...boxes.map((b) => b[1]))
  const x1 = Math.max(...boxes.map((b) => b[2]))
  const y1 = Math.max(...boxes.map((b) => b[3]))
  const pad = Math.max(8, 0.3 * Math.max(x1 - x0, y1 - y0))
  const x = Math.max(0, x0 - pad)
  const y = Math.max(0, y0 - pad)
  const w = Math.min(page.w, x1 + pad) - x
  const h = Math.min(page.h, y1 + pad) - y
  return w * h > 0.6 * page.w * page.h ? null : { x, y, w, h }
}

/** 把一份画布挪到取景框的原点、页面缩成取景框大小：整版预览只画框里的那一块（对象原样，不改文档） */
export function cropToFrame<T extends { page: { w: number; h: number }; objects: CanvasObject[] }>(doc: T, f: { x: number; y: number; w: number; h: number } | null): T {
  if (!f) return doc
  return { ...doc, page: { ...doc.page, w: f.w, h: f.h }, objects: doc.objects.map((o) => ({ ...o, x: o.x - f.x, y: o.y - f.y })) }
}

/** 方案里没过核对的条数（`checks` 里 `status === 'fail'`） */
export function failedChecks(r: VectorEditResult): number {
  return (r.checks ?? []).filter((c) => c.status === 'fail').length
}

/**
 * 方案改了几个对象（改 + 新生成 + 删）。**不含** `depends_on` 的子图：它们只是新对象的位置依据、进基线核对（`namedIds`），没有被改；
 * 摘要与弹窗标题的数要和清单对得上。
 */
export function touchedCount(r: VectorEditResult): number {
  return new Set([...(r.objects ?? []).map((o) => o.id), ...(r.deleted ?? []), ...(r.changes ?? []).map((c) => c.id)]).size
}
