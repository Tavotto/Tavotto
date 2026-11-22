import { msg, type UiMessage } from '@/i18n'
import type { KeyId } from '@/lib/keymap'
import { applyTransform, canSkew, type Axis, type TransformOp } from '@/lib/transform'
import { movableTargets, warnBlockedGroups } from './actions'
import { useDocumentStore } from './documentStore'
import { finishActiveGesture } from './gestureCoordinator'
import { activePathEdit } from './pathEditStore'
import { useSelectionStore } from './selectionStore'
import { useTransformPrefs } from './transformPrefs'
import { useUiStore } from './uiStore'

/**
 * 变换面板的写入（ADR 0185 M4 第二批）：整体变换选区（参考点 = 选区看得见的范围上的那一点，剪切组按蒙版算）、镜像、
 * 倾斜、再次变换（⌥⌘D）。几何全在 `lib/transform`；这里只做：收手势 → 挑能动的对象（锁定的跳过、组里有锁定成员整组跳过）
 * → **一次 commit**（同 id、同数组位置）→ 记下这次变换给 ⌥⌘D。
 */

const hist = (key: string, values?: Record<string, unknown>): UiMessage => msg(`history.${key}`, values, 'workspace')
const say = (key: string, values?: Record<string, unknown>) =>
  useUiStore.getState().setStatus(msg(`status.${key}`, values, 'workspace'), 'info')

const HISTORY: Record<TransformOp['kind'], string> = {
  move: 'transformMove',
  scale: 'transformScale',
  rotate: 'transformRotate',
  skew: 'transformSkew',
  reflect: 'transformReflect',
}

/** 把一次变换作用到选区上；动了至少一个对象返回 true。`remember = false` 时不覆盖「上一次变换」（再次变换自己用） */
export function transformSelection(op: TransformOp, remember = true): boolean {
  const ids = useSelectionStore.getState().ids
  if (!ids.length || activePathEdit()) return false
  finishActiveGesture()
  const { objects: targets, blockedGroups } = movableTargets(ids)
  warnBlockedGroups(blockedGroups, targets.length > 0)
  if (!targets.length) return false
  const doc = useDocumentStore.getState().doc
  const { objects: next, skipped } = applyTransform(targets, op, doc.objects, useTransformPrefs.getState().scaleStrokes)
  if (skipped === targets.length) {
    say(op.kind === 'skew' ? 'skewUnsupported' : 'rotatePanelUnsupported')
    return false
  }
  const byId = new Map(next.map((o) => [o.id, o]))
  useDocumentStore.getState().commit(hist(HISTORY[op.kind]), (d) => {
    d.objects = d.objects.map((o) => byId.get(o.id) ?? o)
  })
  if (skipped) say(op.kind === 'skew' ? 'skewSkipped' : 'rotatePanelSkipped', { count: skipped })
  if (remember) useTransformPrefs.getState().remember(op)
  return true
}

/** 镜像（以当前参考点为轴） */
export const reflectSelection = (axis: Axis) =>
  transformSelection({ kind: 'reflect', axis, rp: useTransformPrefs.getState().refPoint })

/** 水平倾斜 `deg` 度（−89…89；0 不动） */
export function skewSelection(deg: number): boolean {
  const d = Math.max(-89, Math.min(89, deg))
  if (!d) return false
  return transformSelection({ kind: 'skew', deg: d, rp: useTransformPrefs.getState().refPoint })
}

/** 选区里有没有能倾斜的（路径 / 形状 / 箭头） */
export function selectionCanSkew(): boolean {
  const ids = new Set(useSelectionStore.getState().ids)
  return useDocumentStore.getState().doc.objects.some((o) => ids.has(o.id) && !o.hidden && canSkew(o))
}

/** 再次变换（⌥⌘D）：把上一次的变换按同样的参考点作用到现在的选区 */
export function transformAgain(): boolean {
  const op = useTransformPrefs.getState().lastOp
  if (!op) return false
  return transformSelection(op, false)
}

export interface TransformCommand {
  id: 'transform-again' | 'reflect-horizontal' | 'reflect-vertical'
  key?: KeyId
  available: () => boolean
  run: () => void
}

const hasSelection = () => useSelectionStore.getState().ids.length > 0 && !activePathEdit()

/** 命令面板、快捷键读同一份 */
export const TRANSFORM_COMMANDS: readonly TransformCommand[] = [
  {
    id: 'transform-again',
    key: 'transformAgain',
    available: () => hasSelection() && useTransformPrefs.getState().lastOp != null,
    run: () => void transformAgain(),
  },
  { id: 'reflect-horizontal', available: hasSelection, run: () => void reflectSelection('h') },
  { id: 'reflect-vertical', available: hasSelection, run: () => void reflectSelection('v') },
]
