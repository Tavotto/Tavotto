import { msg, type UiMessage } from '@/i18n'
import type { KeyId } from '@/lib/keymap'
import {
  addAnchors,
  anchorKey,
  convertAnchors,
  deleteAnchors,
  parseAnchorKey,
  pathFromMm,
  pathToMm,
  type AnchorRef,
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

export function selectPenTool() {
  finishActiveGesture()
  useUiStore.getState().setTool('pen')
}

/** 命令表的一行。`id` 是稳定标识（命令面板文案 `dialogs:palette.commands.<id>`、右键菜单 `data-quick-item`） */
export interface PathCommand {
  id: 'pen-tool' | 'edit-anchors' | 'add-anchor' | 'delete-anchor' | 'convert-anchor'
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
]

export const pathCommand = (id: PathCommand['id']): PathCommand => PATH_COMMANDS.find((c) => c.id === id)!
