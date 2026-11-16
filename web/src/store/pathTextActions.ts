import { msg, t, type UiMessage } from '@/i18n'
import { toPathObject } from '@/lib/convertToPath'
import { groupPathOf, setGroupPath } from '@/lib/groupTree'
import { newId } from '@/lib/id'
import { arcPathOf, nearestOffset, type XY } from '@/lib/pathText'
import { canvasTextDefaults } from '@/lib/typography'
import type { CanvasObject, PathObject, PathSubpath, PathTextObject } from '@/types/document'
import { objectRotation } from '@/types/document'
import { useDocumentStore } from './documentStore'
import { finishActiveGesture } from './gestureCoordinator'
import { useSelectionStore } from './selectionStore'
import { useUiStore } from './uiStore'

/**
 * 路径文字（ADR 0190）的创建与起点。字排在**自带的一份路径**上（`PathTextObject.subpaths`），从一条路径 / 形状 /
 * 箭头生成时复制它的第一条子路径，并和它成一个新组（一起挪、一起缩放；双击组进去能单独改哪一个）。原来那条线照旧画着——
 * 科研图里路径文字多半是曲线旁的标签，线本身要留着；不想要线就把它删了，字不受影响。
 */

const hist = (key: string, values?: Record<string, unknown>): UiMessage => msg(`history.${key}`, values, 'workspace')

/** 能在上面放字的对象的路径几何（路径原样；形状 / 箭头按「转为路径」的同一份几何）。蒙版、路径文字、看不见 / 锁定的不算 */
export function pathSourceOf(o: CanvasObject): PathObject | null {
  if (o.hidden || o.locked || o.clipMask) return null
  if (o.type === 'path') return o
  if (o.type === 'shape' || o.type === 'arrow') return toPathObject(o)
  return null
}

const firstDrawable = (sps: readonly PathSubpath[]): PathSubpath | null => sps.find((sp) => sp.nodes.length >= 2) ?? null

/** 页面 mm 的点 → 对象框里（未旋转）的点 */
function toLocal(o: CanvasObject, pt: XY): XY {
  const deg = objectRotation(o)
  if (!deg) return [pt[0] - o.x, pt[1] - o.y]
  const cx = o.x + o.w / 2
  const cy = o.y + o.h / 2
  const r = (-deg * Math.PI) / 180
  const dx = pt[0] - cx
  const dy = pt[1] - cy
  return [cx + dx * Math.cos(r) - dy * Math.sin(r) - o.x, cy + dx * Math.sin(r) + dy * Math.cos(r) - o.y]
}

/** 文字工具点下的地方离哪条路径 / 形状的轮廓不超过 `tolMm`：最上面那个，和点在它上面的弧长比例 */
export function pathSourceAt(pt: XY, tolMm: number): { src: CanvasObject; offset: number } | null {
  const objs = useDocumentStore.getState().doc.objects
  for (let i = objs.length - 1; i >= 0; i--) {
    const o = objs[i]
    const p = pathSourceOf(o)
    const sp = p && firstDrawable(p.subpaths)
    if (!p || !sp) continue
    const hit = nearestOffset(arcPathOf({ subpaths: [sp] }, p.w, p.h), toLocal(p, pt))
    if (hit && hit.dist <= tolMm) return { src: o, offset: hit.offset }
  }
  return null
}

/** 在 `src` 上放一段路径文字（起点在 `offset`），与它成一个新组；选中并进入输入。回新对象的 id */
export function createPathText(src: CanvasObject, offset = 0): string | null {
  const p = pathSourceOf(src)
  const sp = p && firstDrawable(p.subpaths)
  if (!p || !sp) return null
  finishActiveGesture()
  const d = canvasTextDefaults()
  const id = newId('pt')
  const gid = newId('g')
  const prefix = groupPathOf(src)
  const obj: PathTextObject = {
    id,
    type: 'pathText',
    x: p.x,
    y: p.y,
    w: p.w,
    h: p.h,
    ...(p.rotationDeg ? { rotationDeg: p.rotationDeg } : {}),
    text: t('objectType.text'),
    sizePt: d.sizePt,
    bold: d.weight === 'bold',
    color: d.color,
    subpaths: [
      {
        closed: sp.closed,
        nodes: sp.nodes.map((n) => ({ p: n.p, ...(n.in ? { in: n.in } : {}), ...(n.out ? { out: n.out } : {}) })),
      },
    ],
    ...(offset > 0 ? { startOffset: Math.round(offset * 10000) / 10000 } : {}),
    lineId: src.id,
  }
  setGroupPath(obj, [...prefix, gid])
  useDocumentStore.getState().commit(hist('addPathText'), (doc) => {
    const at = doc.objects.findIndex((o) => o.id === src.id)
    if (at < 0) return
    setGroupPath(doc.objects[at], [...prefix, gid])
    doc.objects.splice(at + 1, 0, obj)
  })
  const ui = useUiStore.getState()
  if (ui.elementPanelId) ui.setElementPanel(null)
  useSelectionStore.getState().set([id])
  ui.setEditingText(id)
  return id
}

/** 右键「在路径上加文字」：选中的是一条能放字的路径 / 形状 / 箭头 */
export function pathTextTarget(): CanvasObject | null {
  const ids = useSelectionStore.getState().ids
  if (ids.length !== 1) return null
  const o = useDocumentStore.getState().doc.objects.find((x) => x.id === ids[0])
  return o && pathSourceOf(o) ? o : null
}

export const addPathTextToSelection = (): boolean => {
  const o = pathTextTarget()
  return !!o && createPathText(o, 0) != null
}

/** 页面 mm 的点投到路径文字的路径上：起点比例（沿字走的方向量——`arcPathOf` 已按 flip 反向，与后端同一口径） */
export function startOffsetAt(o: PathTextObject, pt: XY): number | null {
  const hit = nearestOffset(arcPathOf(o, o.w, o.h), toLocal(o, pt))
  return hit ? Math.round(hit.offset * 10000) / 10000 : null
}

/** 框里（未旋转）的点 → 页面 mm */
export function localToPage(o: CanvasObject, q: XY): XY {
  const deg = objectRotation(o)
  const px = o.x + q[0]
  const py = o.y + q[1]
  if (!deg) return [px, py]
  const cx = o.x + o.w / 2
  const cy = o.y + o.h / 2
  const r = (deg * Math.PI) / 180
  const dx = px - cx
  const dy = py - cy
  return [cx + dx * Math.cos(r) - dy * Math.sin(r), cy + dx * Math.sin(r) + dy * Math.cos(r)]
}

/** 就地输入提交：空白不落（保留原文字），换行并成空格 */
export function commitPathText(id: string, text: string): void {
  const clean = text.replace(/\s+/g, ' ').trim()
  const o = useDocumentStore.getState().doc.objects.find((x) => x.id === id)
  if (!o || o.type !== 'pathText' || !clean || clean === o.text) return
  useDocumentStore.getState().commit(msg('history.editText', undefined, 'inspector'), (doc) => {
    const x = doc.objects.find((q) => q.id === id)
    if (x && x.type === 'pathText') x.text = clean
  })
}

/* ------------------------------------ 离线（字与线的间隙） ------------------------------------ */

/**
 * 每段路径文字最近一次后端算出来的自动离线（pt）：画布预览的请求顺带带回来（`PathTextView`）。没写离线的对象，键盘微调
 * 与画布间隙柄从它起步；还没回来就是未知（不猜）。
 */
const autoOffsets = new Map<string, number>()
export const rememberAutoOffset = (id: string, pt: number): void => void autoOffsets.set(id, pt)
export const autoOffsetOf = (id: string): number | null => autoOffsets.get(id) ?? null

/** 此刻实际用的离线（pt）：手动写的，或自动值；自动值还不知道时 null */
export function resolvedOffset(o: PathTextObject): number | null {
  return typeof o.baselineOffsetPt === 'number' ? o.baselineOffsetPt : autoOffsetOf(o.id)
}

/** ⇧ 拖间隙柄时吸附的范围（pt）：离 0（贴线）或自动值这么近就吸过去 */
export const GAP_SNAP_PT = 1.5
/** 键盘微调一下（pt） */
export const GAP_STEP_PT = 0.5

/** 拖动 / 微调得到的离线：保留一位小数；⇧ 时吸到 0 或自动值（吸到自动 = 'auto'，字段删掉） */
export function snapGap(v: number, auto: number | null, shift: boolean): number | 'auto' {
  if (shift) {
    if (auto != null && Math.abs(v - auto) <= GAP_SNAP_PT) return 'auto'
    if (Math.abs(v) <= GAP_SNAP_PT) return 0
  }
  return Math.round(v * 10) / 10
}

/** 选区里能调间隙的路径文字（看得见、没锁定） */
function gapTargets(): PathTextObject[] {
  const ids = new Set(useSelectionStore.getState().ids)
  return useDocumentStore
    .getState()
    .doc.objects.filter((o): o is PathTextObject => ids.has(o.id) && o.type === 'pathText' && !o.hidden && !o.locked)
}

export const canNudgeGap = (): boolean => gapTargets().length > 0 && gapTargets().length === useSelectionStore.getState().ids.length

/** ⌥⇧↑ / ⌥⇧↓：选区全是路径文字时把离线加 / 减一步，一条历史。自动值还不知道的那段从 0 起不合适，跳过 */
export function nudgePathTextGap(dir: 1 | -1, step = GAP_STEP_PT): boolean {
  if (!canNudgeGap()) return false
  const next = new Map<string, number>()
  for (const o of gapTargets()) {
    const cur = resolvedOffset(o)
    if (cur != null) next.set(o.id, Math.round((cur + dir * step) * 10) / 10)
  }
  if (!next.size) return true
  useDocumentStore.getState().commit(hist('pathTextGap'), (doc) => {
    for (const x of doc.objects) if (x.type === 'pathText' && next.has(x.id)) x.baselineOffsetPt = next.get(x.id)
  })
  return true
}
