import { msg, type UiMessage } from '@/i18n'
import type { KeyId } from '@/lib/keymap'
import { newId } from '@/lib/id'
import {
  addAnchors,
  anchorKey,
  averageAnchors,
  closeSubpath,
  convertAnchors,
  cutAtAnchors,
  deleteAnchors,
  endOf,
  joinSubpaths,
  rotatePoint,
  unrotatePoint,
  parseAnchorKey,
  pathFromMm,
  pathToMm,
  type AnchorRef,
  type MmNode,
  type MmSubpath,
} from '@/lib/pathEdit'
import type { PathObject } from '@/types/document'
import { useDocumentStore } from './documentStore'
import { finishActiveGesture } from './gestureCoordinator'
import { activePathEdit, canEditAnchors, usePathEditStore } from './pathEditStore'
import { useSelectionStore } from './selectionStore'
import { useUiStore } from './uiStore'

/**
 * 路径编辑的**动作与命令表**（ADR 0185 §五 M1：每个动作注册成命令，命令面板、右键菜单、快捷键共用同一份）。
 * 几何全部在 `lib/pathEdit`（纯函数）；这里只做三件事：先收掉进行中的手势（离散动作）、算出新对象、**一次 commit**。
 */

const hist = (key: string, values?: Record<string, unknown>): UiMessage => msg(`history.${key}`, values, 'workspace')

const refsOf = (keys: readonly string[]): AnchorRef[] =>
  keys.map(parseAnchorKey).filter((a): a is AnchorRef => a != null)

/**
 * 把一次锚点编辑落成一条历史：`edit` 拿编辑空间的子路径、回新的子路径与该选上的锚点。锚点删光 = 对象一起删掉。
 * 包围盒在同一次 commit 里按曲线实际范围重算（`pathFromMm`）。
 */
export function applyPathEdit(
  pathId: string,
  label: UiMessage,
  edit: (subs: MmSubpath[]) => { subs: MmSubpath[]; select?: AnchorRef[] },
): boolean {
  const base = useDocumentStore.getState().doc.objects.find((o) => o.id === pathId)
  if (!canEditAnchors(base)) return false
  const { subs, select } = edit(pathToMm(base))
  const next = pathFromMm(base, subs)
  useDocumentStore.getState().commit(label, (d) => {
    const i = d.objects.findIndex((o) => o.id === pathId)
    if (i < 0) return
    if (next) d.objects[i] = next
    else d.objects.splice(i, 1)
  })
  if (!next) {
    usePathEditStore.getState().exit()
    useSelectionStore.getState().clear()
  } else if (select) {
    usePathEditStore.getState().setAnchors(select.map(anchorKey))
  }
  return true
}

/** 进入直接选择（锚点编辑）：选中它、只选它 */
export function editAnchors(id?: string) {
  const target = id ?? useSelectionStore.getState().ids[0]
  const o = useDocumentStore.getState().doc.objects.find((x) => x.id === target)
  if (!canEditAnchors(o)) return
  finishActiveGesture()
  useSelectionStore.getState().set([o.id])
  usePathEditStore.getState().enter(o.id)
}

export function exitAnchorEdit() {
  usePathEditStore.getState().exit()
}

/** 添加锚点（+）：选了锚点 = 每个选中锚点后面那一段的中点；没选 = 每一段的中点。曲线不变 */
export function addAnchorPoints() {
  const ed = activePathEdit()
  if (!ed) return
  finishActiveGesture()
  applyPathEdit(ed.path.id, hist('addAnchors'), (subs) => {
    const r = addAnchors(subs, refsOf(ed.anchors))
    return { subs: r.subs, select: r.added }
  })
}

/** 删除锚点（− / Delete）；删光了连路径一起删 */
export function deleteAnchorPoints() {
  const ed = activePathEdit()
  if (!ed || !ed.anchors.length) return
  finishActiveGesture()
  applyPathEdit(ed.path.id, hist('deleteAnchors', { count: ed.anchors.length }), (subs) => ({
    subs: deleteAnchors(subs, refsOf(ed.anchors)),
    select: [],
  }))
}

/** 转换锚点（⇧C）：全是平滑点 → 尖角，否则 → 平滑 */
export function convertAnchorPoints() {
  const ed = activePathEdit()
  if (!ed || !ed.anchors.length) return
  finishActiveGesture()
  applyPathEdit(ed.path.id, hist('convertAnchors'), (subs) => ({
    subs: convertAnchors(subs, refsOf(ed.anchors)),
    select: refsOf(ed.anchors),
  }))
}

/**
 * 剪刀：在选中的锚点处断开（相邻两个都选了 = 剪掉它们之间那段，`lib/pathEdit.cutAtAnchors`）。
 * 断出来的第一段留在原对象里（同一个 id，接着编辑），其余每段各成一个新的路径对象、样式照搬，紧挨着原对象往上放。一次 commit。
 */
export function cutPathAtAnchors() {
  const ed = activePathEdit()
  if (!ed || !ed.anchors.length) return
  finishActiveGesture()
  const base = ed.path
  const pieces = cutAtAnchors(pathToMm(base), refsOf(ed.anchors))
  const keep: MmSubpath[] = []
  const extra: MmSubpath[] = []
  for (const ps of pieces) {
    if (!ps.length) continue
    keep.push(ps[0])
    extra.push(...ps.slice(1))
  }
  const next = pathFromMm(base, keep)
  const added = extra
    .map((sp) => pathFromMm(base, [sp]))
    .filter((o): o is PathObject => o != null)
    .map((o) => ({ ...o, id: newId('p'), name: undefined, groupId: base.groupId }))
  if (!added.length && JSON.stringify(next?.subpaths) === JSON.stringify(base.subpaths)) return
  useDocumentStore.getState().commit(hist('cutPath'), (d) => {
    const i = d.objects.findIndex((o) => o.id === base.id)
    if (i < 0) return
    if (next) d.objects.splice(i, 1, next, ...added)
    else d.objects.splice(i, 1, ...added)
  })
  usePathEditStore.getState().setAnchors([])
}

/** 两条路径的一对端点：选区里的两个路径对象各自开放子路径的端点里，离得最近的那一对（页面上量） */
function nearestEnds(a: PathObject, b: PathObject) {
  const ends = (o: PathObject) =>
    pathToMm(o).flatMap((sp, si) =>
      sp.closed || sp.nodes.length < 2
        ? []
        : (['start', 'end'] as const).map((e) => ({ si, e, at: rotatePoint(o, sp.nodes[e === 'start' ? 0 : sp.nodes.length - 1].p) })),
    )
  let best: { a: { si: number; e: 'start' | 'end' }; b: { si: number; e: 'start' | 'end' }; d: number } | null = null
  for (const x of ends(a))
    for (const y of ends(b)) {
      const d = Math.hypot(x.at[0] - y.at[0], x.at[1] - y.at[1])
      if (!best || d < best.d) best = { a: x, b: y, d }
    }
  return best
}

/** 选区里正好两个能编辑的路径对象（不在锚点编辑里） */
const twoPaths = (): [PathObject, PathObject] | null => {
  if (activePathEdit()) return null
  const ids = useSelectionStore.getState().ids
  if (ids.length !== 2) return null
  const objs = ids.map((id) => useDocumentStore.getState().doc.objects.find((o) => o.id === id))
  return objs.every(canEditAnchors) ? (objs as [PathObject, PathObject]) : null
}

/** 锚点编辑里选着的两个端点（开放子路径的首 / 尾） */
const twoEnds = () => {
  const ed = activePathEdit()
  if (!ed || ed.anchors.length !== 2) return null
  const subs = pathToMm(ed.path)
  const [a, b] = refsOf(ed.anchors)
  const ea = a && endOf(subs, a)
  const eb = b && endOf(subs, b)
  if (!ea || !eb) return null
  if (a.sp === b.sp && ea === eb) return null
  return { ed, subs, a, b, ea, eb }
}

/**
 * 连接（⌘J）：
 * - 锚点编辑里选着两个端点：同一条子路径的两端 = 闭合它；两条子路径的端点 = 连成一条；
 * - 不在编辑里、选着两条路径：取离得最近的那对端点连起来，合成一个对象（留主选那个的 id 与样式），另一个删掉。
 * 两端重合就合成一个锚点，否则补一段直线。一次 commit。
 */
export function joinPaths() {
  const pair = twoEnds()
  if (pair) {
    const { ed, a, b, ea, eb } = pair
    finishActiveGesture()
    applyPathEdit(ed.path.id, hist('joinPaths'), (subs) => {
      if (a.sp === b.sp) {
        subs[a.sp] = closeSubpath(subs[a.sp])
        return { subs, select: [] }
      }
      const first = ea === 'end' ? a : b
      const second = first === a ? b : a
      const joined = joinSubpaths(subs[first.sp], first === a ? ea : eb, subs[second.sp], second === a ? ea : eb)
      const out = subs.filter((_, i) => i !== second.sp)
      out[out.indexOf(subs[first.sp])] = joined
      return { subs: out, select: [] }
    })
    return
  }
  const two = twoPaths()
  if (!two) return
  const ids = useSelectionStore.getState().ids
  const keepObj = two.find((o) => o.id === ids[ids.length - 1])!
  const other = two.find((o) => o !== keepObj)!
  const near = nearestEnds(keepObj, other)
  if (!near) return
  finishActiveGesture()
  // 另一条的锚点换进保留那条的编辑空间（页面上不动）
  const toKeep = (q: MmNode['p']) => unrotatePoint(keepObj, rotatePoint(other, q))
  const moved: MmSubpath[] = pathToMm(other).map((sp) => ({
    closed: sp.closed,
    nodes: sp.nodes.map((n) => ({ p: toKeep(n.p), in: toKeep(n.in), out: toKeep(n.out), smooth: n.smooth })),
  }))
  const subs = pathToMm(keepObj)
  const joined = joinSubpaths(subs[near.a.si], near.a.e, moved[near.b.si], near.b.e)
  // a 的端点在 a 的「起点」时，joinSubpaths 把 a 倒过来接——方向变了也只是同一条线
  subs[near.a.si] = joined
  const next = pathFromMm(keepObj, [...subs, ...moved.filter((_, i) => i !== near.b.si)])
  if (!next) return
  useDocumentStore.getState().commit(hist('joinPaths'), (d) => {
    const i = d.objects.findIndex((o) => o.id === keepObj.id)
    if (i >= 0) d.objects[i] = next
    d.objects = d.objects.filter((o) => o.id !== other.id)
  })
  useSelectionStore.getState().set([keepObj.id])
}

/** 平均（⌥⌘J）：选中的锚点挪到它们的平均位置 */
export function averageAnchorPoints() {
  const ed = activePathEdit()
  if (!ed || ed.anchors.length < 2) return
  finishActiveGesture()
  applyPathEdit(ed.path.id, hist('averageAnchors'), (subs) => ({
    subs: averageAnchors(subs, refsOf(ed.anchors)),
    select: refsOf(ed.anchors),
  }))
}

export function selectPenTool() {
  finishActiveGesture()
  useUiStore.getState().setTool('pen')
}

/** 命令表的一行。`id` 是稳定标识（命令面板文案 `dialogs:palette.commands.<id>`、右键菜单 `data-quick-item`） */
export interface PathCommand {
  id:
    | 'pen-tool'
    | 'edit-anchors'
    | 'add-anchor'
    | 'delete-anchor'
    | 'convert-anchor'
    | 'cut-path'
    | 'join-paths'
    | 'average-anchors'
  /** 快捷键（`lib/keymap` 的 id）；没有 = 只从菜单 / 命令面板进 */
  key?: KeyId
  /** 此刻能不能做（不能做的不出现：命令面板不列、右键菜单不摆、快捷键不消费） */
  available: () => boolean
  run: () => void
}

const singlePath = (): PathObject | null => {
  const ids = useSelectionStore.getState().ids
  if (ids.length !== 1) return null
  const o = useDocumentStore.getState().doc.objects.find((x) => x.id === ids[0])
  return canEditAnchors(o) ? o : null
}

export const PATH_COMMANDS: readonly PathCommand[] = [
  { id: 'pen-tool', key: 'toolPen', available: () => true, run: selectPenTool },
  {
    id: 'edit-anchors',
    available: () => !!singlePath() && !activePathEdit(),
    run: () => editAnchors(),
  },
  { id: 'add-anchor', key: 'addAnchor', available: () => !!activePathEdit(), run: addAnchorPoints },
  {
    id: 'delete-anchor',
    key: 'deleteAnchor',
    available: () => !!activePathEdit()?.anchors.length,
    run: deleteAnchorPoints,
  },
  {
    id: 'convert-anchor',
    key: 'convertAnchor',
    available: () => !!activePathEdit()?.anchors.length,
    run: convertAnchorPoints,
  },
  { id: 'cut-path', available: () => !!activePathEdit()?.anchors.length, run: cutPathAtAnchors },
  { id: 'join-paths', key: 'joinPaths', available: () => !!twoEnds() || !!(twoPaths() && nearestEnds(...twoPaths()!)), run: joinPaths },
  {
    id: 'average-anchors',
    key: 'averageAnchors',
    available: () => (activePathEdit()?.anchors.length ?? 0) >= 2,
    run: averageAnchorPoints,
  },
]

export const pathCommand = (id: PathCommand['id']): PathCommand => PATH_COMMANDS.find((c) => c.id === id)!
