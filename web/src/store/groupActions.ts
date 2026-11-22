import { msg, type UiMessage } from '@/i18n'
import type { KeyId } from '@/lib/keymap'
import { canBeMask, gidOfKey, groupMasks, isGroupKey, prefixOf, unitKeyOf, unitMembers } from '@/lib/groupTree'
import { groupSelected, groupUnits, selectedUnits, ungroupSelected } from './actions'
import { useDocumentStore } from './documentStore'
import { finishActiveGesture } from './gestureCoordinator'
import { isolationScope, useIsolationStore } from './isolationStore'
import { activePathEdit } from './pathEditStore'
import { useSelectionStore } from './selectionStore'
import { useUiStore } from './uiStore'

/**
 * 嵌套组、组内编辑、剪切蒙版的**动作与命令表**（ADR 0191）。结构与 `pathfinderActions.PATHFINDER_COMMANDS` 相同：
 * 命令面板、右键菜单、快捷键读同一份。判据在 `lib/groupTree`（纯函数）；成组 / 取消成组的写入在 `actions.groupUnits /
 * ungroupSelected`（选择、移动、锁定共用的「单元」规则也在那边），这里只做蒙版与组内编辑。
 */

const hist = (key: string, values?: Record<string, unknown>): UiMessage => msg(`history.${key}`, values, 'workspace')
const say = (key: string) => useUiStore.getState().setStatus(msg(`status.${key}`, undefined, 'workspace'), 'info')
const objects = () => useDocumentStore.getState().doc.objects

/** 建剪切蒙版用哪个对象：选区里最上面那个，必须是这一层的直接成员（不在子组里）、能当蒙版；不行 = null */
function maskCandidate(): string | null {
  const ids = new Set(useSelectionStore.getState().ids)
  const objs = objects()
  const scope = isolationScope(objs)
  for (let i = objs.length - 1; i >= 0; i--) {
    const o = objs[i]
    if (!ids.has(o.id) || unitKeyOf(o, scope) == null) continue
    return unitKeyOf(o, scope) === o.id && canBeMask(o) ? o.id : null
  }
  return null
}

/** 选区能不能建剪切蒙版：这一层里至少两个单元，最上面的是路径或封闭形状 */
export function canMakeClipMask(): boolean {
  return !activePathEdit() && selectedUnits().length >= 2 && maskCandidate() != null
}

/**
 * 建立剪切蒙版（⌘7）：选区收成一个新组，最上面那个路径 / 封闭形状成为它的剪切路径——不画，只裁下面的成员
 * （图、文字、形状、子组都裁）。蒙版同时写 `hidden`：旧构建打开时不画它，内容照样都在。一次 commit。
 */
export function makeClipMask(): void {
  const units = selectedUnits()
  if (units.length < 2) return say('clipNeedTwo')
  const mask = maskCandidate()
  if (!mask) return say('clipNeedPathOnTop')
  const gid = groupUnits(hist('makeClipMask'), (d) => {
    const m = d.objects.find((o) => o.id === mask)
    if (!m) return
    m.clipMask = true
    m.hidden = true
  })
  if (gid) useUiStore.getState().setStatus(msg('status.clipMade', undefined, 'workspace'), 'done')
}

/** 选区涉及的剪切组：选中的组单元里带蒙版的，加上正在编辑的那一层（在剪切组里面编辑时） */
function clipGroupsInSelection(): string[] {
  const objs = objects()
  const masks = groupMasks(objs, { includeOff: true })
  const out = new Set(selectedUnits().filter(isGroupKey).map(gidOfKey).filter((g) => masks.has(g)))
  const scope = isolationScope(objs)
  if (scope && masks.has(scope) && useSelectionStore.getState().ids.length) out.add(scope)
  return [...out]
}

export const canReleaseClipMask = (): boolean => !activePathEdit() && clipGroupsInSelection().length > 0

/** 释放剪切蒙版（⌥⌘7）：蒙版变回普通可见对象，组还在（要拆组再 ⇧⌘G）。一次 commit */
export function releaseClipMask(): void {
  const gids = new Set(clipGroupsInSelection())
  if (!gids.size) return
  finishActiveGesture()
  const masks = groupMasks(objects(), { includeOff: true })
  const ids = new Set([...gids].map((g) => masks.get(g)!.id))
  useDocumentStore.getState().commit(hist('releaseClipMask'), (d) => {
    for (const o of d.objects) {
      if (!ids.has(o.id)) continue
      delete o.clipMask
      delete o.clipOff
      delete o.hidden
    }
  })
}

/**
 * 双击进组（组内编辑）：点中的对象在当前这一层里属于一个组 → 进到那个组里，选中它在新一层里的单元；返回 true。
 * 已经是这一层的直接成员 = false（调用方照旧做它的双击：改字、编辑锚点、进图内编辑……）。
 */
export function enterGroupAt(id: string): boolean {
  const objs = objects()
  const o = objs.find((x) => x.id === id)
  if (!o) return false
  const key = unitKeyOf(o, isolationScope(objs))
  if (!key || !isGroupKey(key)) return false
  const path = prefixOf(objs, gidOfKey(key))
  if (!path) return false
  finishActiveGesture()
  useIsolationStore.getState().enter(path)
  const next = unitKeyOf(o, path[path.length - 1])
  useSelectionStore.getState().set(next ? unitMembers(objs, next) : [id])
  return true
}

export const isolationActive = (): boolean => isolationScope() != null

/** 退出一层组内编辑（Esc）：选中刚退出的那个组（在上一层里它是一个整体） */
export function exitGroupLevel(): boolean {
  const left = useIsolationStore.getState().exitOne()
  if (!left) return false
  useSelectionStore.getState().set(unitMembers(objects(), `g:${left}`))
  return true
}

export function exitAllGroups(): void {
  useIsolationStore.getState().clear()
}

export interface GroupCommand {
  id: 'group' | 'ungroup' | 'make-clip-mask' | 'release-clip-mask'
  key: KeyId
  available: () => boolean
  run: () => void
}

/** 命令面板、右键菜单、快捷键读同一份 */
export const GROUP_COMMANDS: readonly GroupCommand[] = [
  { id: 'group', key: 'groupObjects', available: () => !activePathEdit() && selectedUnits().length >= 2, run: groupSelected },
  {
    id: 'ungroup',
    key: 'ungroupObjects',
    available: () => !activePathEdit() && selectedUnits().some(isGroupKey),
    run: ungroupSelected,
  },
  { id: 'make-clip-mask', key: 'makeClipMask', available: canMakeClipMask, run: makeClipMask },
  { id: 'release-clip-mask', key: 'releaseClipMask', available: canReleaseClipMask, run: releaseClipMask },
]

export const groupCommand = (id: GroupCommand['id']): GroupCommand => GROUP_COMMANDS.find((c) => c.id === id)!
