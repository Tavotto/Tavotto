import { msg, type UiMessage } from '@/i18n'
import type { KeyId } from '@/lib/keymap'
import {
  canOutlineStroke,
  makeCompound,
  offsetPaths,
  operands,
  outlineStrokes,
  releaseCompound,
  reversePath,
  runPathfinder,
  simplifyPaths,
  type PathfinderOp,
  type PathfinderPlan,
} from '@/lib/pathfinder'
import { loadVecops, useVecopsStore, type VecopsKernel } from '@/lib/vecops/kernel'
import type { PathObject } from '@/types/document'
import { useDocumentStore } from './documentStore'
import { finishActiveGesture } from './gestureCoordinator'
import { activePathEdit } from './pathEditStore'
import { useSelectionStore } from './selectionStore'
import { enterShapeBuilder, shapeBuilderActive } from './shapeBuilder'
import { useUiStore } from './uiStore'

/**
 * 路径查找器、复合路径、偏移路径、轮廓化描边、反转路径方向的**动作与命令表**（ADR 0185 §五 M2）。结构与
 * `pathActions.PATH_COMMANDS` 相同：命令面板、右键菜单、属性栏那一排按钮、快捷键读同一份。几何与落位规则在
 * `lib/pathfinder`（纯函数）；这里只做：收掉进行中的手势 → 按需加载内核 → 算 → **一次 commit** → 选中结果。
 *
 * 要内核的命令在内核加载不了时（内嵌画布、环境不让编译 WASM）照样列出来，但灰掉、停上去说原因（`blockedReason`），
 * 不弹错。算出来是空的（比如相交的两个对象根本不重叠）→ 说一句、什么都不删、不进历史。
 */

const hist = (key: string, values?: Record<string, unknown>): UiMessage => msg(`history.${key}`, values, 'workspace')
const say = (key: string) => useUiStore.getState().setStatus(msg(`status.${key}`, undefined, 'workspace'), 'info')

const selectedIds = () => useSelectionStore.getState().ids
const objects = () => useDocumentStore.getState().doc.objects

/** 选区里参与运算的对象有几个（全都能参与才算；锚点编辑里不算） */
function operandCount(): number {
  if (activePathEdit()) return 0
  const ids = selectedIds()
  return ids.length && operands(objects(), ids) ? ids.length : 0
}

/** 把一个落位方案写进文档：删原对象、在 `at` 处插入结果、选中结果。一次 commit */
function applyPlan(label: UiMessage, plan: PathfinderPlan): void {
  const remove = new Set(plan.remove)
  useDocumentStore.getState().commit(label, (d) => {
    d.objects = d.objects.filter((o) => !remove.has(o.id))
    d.objects.splice(plan.at, 0, ...plan.add)
  })
  useSelectionStore.getState().set(plan.add.map((o) => o.id))
}

/** 要内核的动作：先收手势、再加载（第一次会等几十毫秒）；加载不了 = 说一句 */
async function withKernel(fn: (k: VecopsKernel) => void): Promise<void> {
  finishActiveGesture()
  const k = await loadVecops()
  if (!k) {
    say('pathfinderUnavailable')
    return
  }
  // 等内核下载期间用户可能又开了一轮手势（拖动、连续改属性）：落笔前再收一次，否则本次 commit 会并进它的事务
  finishActiveGesture()
  fn(k)
}

export function pathfinderSelected(op: PathfinderOp): Promise<void> {
  const ids = [...selectedIds()]
  return withKernel((k) => {
    const plan = runPathfinder(k, op, objects(), ids)
    if (!plan) return say('pathfinderEmpty')
    applyPlan(hist(`pathfinder.${op}`), plan)
  })
}

export function makeCompoundPath(): void {
  const plan = makeCompound(objects(), selectedIds())
  if (!plan) return
  finishActiveGesture()
  applyPlan(hist('makeCompound'), plan)
}

const singleCompound = (): PathObject | null => {
  if (activePathEdit()) return null
  const ids = selectedIds()
  if (ids.length !== 1) return null
  const o = objects().find((x) => x.id === ids[0])
  return o && o.type === 'path' && !o.locked && !o.hidden && o.subpaths.length >= 2 ? o : null
}

export function releaseCompoundPath(): void {
  const o = singleCompound()
  const plan = o && releaseCompound(objects(), o.id)
  if (!plan) return
  finishActiveGesture()
  applyPlan(hist('releaseCompound'), plan)
}

/** 偏移距离（mm）：属性栏「偏移路径」那一格改它，命令面板 / 右键菜单用上一次的值；只在本次会话里记 */
let offsetMm = 1
export const getOffsetMm = () => offsetMm
export const setOffsetMm = (v: number) => {
  if (Number.isFinite(v) && v !== 0) offsetMm = v
}

export function offsetSelected(deltaMm = offsetMm): Promise<void> {
  const ids = [...selectedIds()]
  return withKernel((k) => {
    const made = offsetPaths(k, objects(), ids, deltaMm)
    if (!made) return say('pathfinderEmpty')
    useDocumentStore.getState().commit(hist('offsetPath'), (d) => {
      for (const { after, add } of made) {
        const i = d.objects.findIndex((o) => o.id === after)
        if (i >= 0) d.objects.splice(i + 1, 0, add)
      }
    })
    useSelectionStore.getState().set(made.map((m) => m.add.id))
  })
}

export function outlineSelected(): Promise<void> {
  const ids = [...selectedIds()]
  return withKernel((k) => {
    const made = outlineStrokes(k, objects(), ids)
    if (!made) return say('pathfinderEmpty')
    const pick: string[] = []
    useDocumentStore.getState().commit(hist('outlineStroke'), (d) => {
      for (const { replace, extra } of made) {
        const i = d.objects.findIndex((o) => o.id === replace.id)
        if (i < 0) continue
        d.objects.splice(i, 1, replace, ...(extra ? [extra] : []))
      }
    })
    for (const { replace, extra } of made) pick.push(extra ? extra.id : replace.id)
    useSelectionStore.getState().set(pick)
  })
}

const selectedPaths = (): PathObject[] => {
  if (activePathEdit()) return []
  const ids = new Set(selectedIds())
  return objects().filter((o): o is PathObject => ids.has(o.id) && o.type === 'path' && !o.locked && !o.hidden)
}

export function reverseSelectedPaths(): void {
  const paths = selectedPaths()
  if (!paths.length) return
  finishActiveGesture()
  const ids = new Set(paths.map((p) => p.id))
  useDocumentStore.getState().commit(hist('reversePath'), (d) => {
    d.objects = d.objects.map((o) => (ids.has(o.id) && o.type === 'path' ? reversePath(o) : o))
  })
}

/** 简化选中的路径（容差 `SIMPLIFY_TOLERANCE_PT`）：同 id 原位替换，一次 commit；一个锚点都省不掉 = 说一句 */
export function simplifySelected(): Promise<void> {
  const ids = [...selectedIds()]
  return withKernel((k) => {
    const made = simplifyPaths(k, objects(), ids)
    if (!made) return say('simplifyNothing')
    const byId = new Map(made.map((p) => [p.id, p]))
    useDocumentStore.getState().commit(hist('simplifyPath'), (d) => {
      d.objects = d.objects.map((o) => byId.get(o.id) ?? o)
    })
  })
}

export interface PathfinderCommand {
  id:
    | 'pathfinder-unite'
    | 'pathfinder-minus-front'
    | 'pathfinder-intersect'
    | 'pathfinder-exclude'
    | 'pathfinder-divide'
    | 'pathfinder-trim'
    | 'make-compound'
    | 'release-compound'
    | 'offset-path'
    | 'outline-stroke'
    | 'reverse-path'
    | 'simplify-path'
    | 'shape-builder'
  key?: KeyId
  /** 要不要 WASM 内核（要的话内核加载不了时灰掉） */
  kernel: boolean
  available: () => boolean
  run: () => void
}

const twoOrMore = () => operandCount() >= 2
const anyOperand = () => operandCount() >= 1
const op = (id: PathfinderCommand['id'], o: PathfinderOp): PathfinderCommand => ({
  id,
  kernel: true,
  available: twoOrMore,
  run: () => void pathfinderSelected(o),
})

export const PATHFINDER_COMMANDS: readonly PathfinderCommand[] = [
  op('pathfinder-unite', 'unite'),
  op('pathfinder-minus-front', 'minusFront'),
  op('pathfinder-intersect', 'intersect'),
  op('pathfinder-exclude', 'exclude'),
  op('pathfinder-divide', 'divide'),
  op('pathfinder-trim', 'trim'),
  { id: 'make-compound', key: 'makeCompound', kernel: false, available: twoOrMore, run: makeCompoundPath },
  { id: 'release-compound', key: 'releaseCompound', kernel: false, available: () => !!singleCompound(), run: releaseCompoundPath },
  { id: 'offset-path', kernel: true, available: anyOperand, run: () => void offsetSelected() },
  {
    id: 'outline-stroke',
    kernel: true,
    available: () => {
      if (activePathEdit()) return false
      const ids = new Set(selectedIds())
      const picked = objects().filter((o) => ids.has(o.id))
      return picked.length > 0 && picked.length === ids.size && picked.every(canOutlineStroke)
    },
    run: () => void outlineSelected(),
  },
  {
    id: 'shape-builder',
    key: 'shapeBuilder',
    kernel: true,
    available: () => twoOrMore() && !shapeBuilderActive(),
    run: () => void enterShapeBuilder(),
  },
  {
    id: 'simplify-path',
    kernel: true,
    available: () => selectedPaths().length > 0 && selectedPaths().length === selectedIds().length,
    run: () => void simplifySelected(),
  },
  { id: 'reverse-path', kernel: false, available: () => selectedPaths().length > 0 && selectedPaths().length === selectedIds().length, run: reverseSelectedPaths },
]

export const pathfinderCommand = (id: PathfinderCommand['id']): PathfinderCommand =>
  PATHFINDER_COMMANDS.find((c) => c.id === id)!

/** 这个命令此刻为什么点不了（只有「内核加载不了」这一种要说；选区不对的直接不出现） */
export function blockedReason(c: PathfinderCommand): UiMessage | null {
  if (!c.kernel) return null
  const st = useVecopsStore.getState()
  if (st.status !== 'unavailable') return null
  return msg(st.reason === 'embedded' ? 'pathfinder.blockedEmbedded' : 'pathfinder.blockedFailed', undefined, 'workspace')
}
