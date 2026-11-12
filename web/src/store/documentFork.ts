/**
 * 「存成副本」：把此刻这个窗口里的排版分成一份**新排版**（真分叉）。
 *
 * 新 documentId、新自动保存槽位、名字「原名 副本」；绑定了项目文件的写成原文件旁边的新文件
 * （重名加序号，绝不覆盖已有文件）；时间线 / 撤销历史不继承（它们按 documentId 分存、`switchDocument` 清栈）。
 * **原来那份一个字节不动**：本机自动保存槽位与项目文件都不碰——旧文档在换走之前被挂起
 * （`suspendAutosaveFor`），换走那一刻的冲刷因此不写；这个窗口里没存的改动随内存里这份一起进了副本。
 *
 * 换走之后 `switchDocument` 会 `announceDocOpen(新 id)`：presence 端着的 id 跟着换，原 id 不再由本窗口占。
 *
 * 另存为（`LayoutDialog` 的 `save` 形态）用同一个 `installFork`：它自己先写好用户起了名的那个文件，再装副本。
 */
import { msg, t as translate } from '@/i18n'
import { ApiError, REVISION_ABSENT, saveLayout, type SavedLayout } from '@/lib/api'
import { newId } from '@/lib/id'
import { rememberLayoutName, rememberLayoutRevision } from '@/lib/layoutRevision'
import type { ProjectDocument } from '@/types/document'
import {
  activeProjectFile,
  captureFigureSnapshotSave,
  flushAutosave,
  getWindowHold,
  isAutosaveSuspendedFor,
  mergeSavedFigureSnapshots,
  resumeAutosave,
  setProjectFile,
  suspendAutosaveFor,
  useDocumentStore,
  isDocumentPlaceholder,
} from './documentStore'
import { finishActiveGesture, finishFocusedRichText } from './gestureCoordinator'
import { useSelectionStore } from './selectionStore'
import { captureSaveContext, stillCurrent } from './saveContext'
import { useUiStore } from './uiStore'
import { fitStageNow } from './zoomToSelection'
import { currentProjectId } from '@/lib/session'

/**
 * 分叉没成时把原排版接回自动保存（只在挂起还是这一份的时候；只读窗口的挂起是它自己的，不能放开），
 * 并补一次冲刷——挂起期间防抖计时器被取消了，不补的话没存的改动要等下一次编辑才会再排队。
 */
export function resumeAutosaveAfterFork(docId: string): void {
  if (!isAutosaveSuspendedFor(docId) || getWindowHold()) return
  resumeAutosave()
  flushAutosave()
}

/** 项目里重名时最多试到「名 N」的 N（再多说明出了别的问题） */
const MAX_NAME_TRIES = 50

/** 「{{name}} 副本」：名字来自当前排版名，空名退回一个通用的 */
export const forkCopyName = (name: string): string =>
  translate('fork.copyName', { ns: 'workspace', name: name.trim() || translate('fork.untitled', { ns: 'workspace' }) })

/** 换走之后的收尾：选区清空、元素面板收起、重新取景（与别的换文档入口同一套） */
function afterSwitch(): void {
  useSelectionStore.getState().clear()
  useUiStore.getState().setElementPanel(null)
  fitStageNow()
}

/**
 * 把 `pd`（带着新名字的那一份内容）装成一份新排版并换过去；原排版不被写。
 * 返回新 documentId；装不进去返回 null（自动保存的挂起放回去）。
 */
export async function installFork(pd: ProjectDocument, beforeSwitch?: () => void): Promise<string | null> {
  const oldId = useDocumentStore.getState().documentId
  const id = newId('d')
  suspendAutosaveFor(oldId)
  // 紧挨着换文档调用：`switchDocument` 在这条路径上没有 await，换走（清只读、丢没取走的动作）就发生在这一步里
  beforeSwitch?.()
  const ok = await useDocumentStore.getState().switchDocument(pd, id)
  if (!ok) {
    // 只读窗口（`windowHold`）的挂起是它自己的，不能因为分叉没成就放开
    resumeAutosaveAfterFork(oldId)
    return null
  }
  afterSwitch()
  return id
}

export type ForkOutcome =
  | { ok: true; id: string; name: string; file: string | null }
  | { ok: false; reason: 'busy' | 'write_failed'; error?: unknown }
  /** 文件已写进项目（`file`），但写的途中切走了：此刻开着的另一份没被换掉 */
  | { ok: false; reason: 'switched'; file: string | null }

/**
 * 在项目里找一个没被占的名字写下去：`REVISION_ABSENT` = 「这个名字下我没读到过任何内容」，
 * 后端见到已有文件就回 409 `external_change`，换「名 2」「名 3」再试——任何已有文件都不会被覆盖。
 */
async function writeNewProjectFile(
  stem: string,
  pd: ProjectDocument,
): Promise<{ res: SavedLayout; name: string }> {
  for (let n = 1; n <= MAX_NAME_TRIES; n++) {
    const name = n === 1 ? stem : `${stem} ${n}`
    try {
      const res = await saveLayout(name, { ...pd, project: { ...pd.project, name } }, REVISION_ABSENT, {
        target: 'project',
      })
      return { res, name }
    } catch (e) {
      const taken = e instanceof ApiError && e.status === 409 && e.body.code === 'external_change'
      if (!taken) throw e
    }
  }
  throw new Error('fork_name_exhausted')
}

/**
 * 存成副本（不问名字）。失败不动当前排版、不留半截：项目文件没写成就整件不做，由调用方说一句并给出口。
 */
export async function forkCurrentDocument(opts: { beforeSwitch?: () => void } = {}): Promise<ForkOutcome> {
  if (isDocumentPlaceholder()) return { ok: false, reason: 'busy' }
  finishFocusedRichText()
  finishActiveGesture()
  const ctx = captureSaveContext()
  // 入口就把原排版摘出自动保存：写项目文件那段 await 里，防抖自动保存不能再写原 id 的本机槽位 / 磁盘
  // （原排版一个字节不动）。没分叉成就在每个失败出口接回去（`resumeAutosaveAfterFork`）
  suspendAutosaveFor(ctx.documentId)
  const state = useDocumentStore.getState()
  const stem = forkCopyName(state.projectMeta.name)
  const binding = activeProjectFile()
  const pj = currentProjectId()
  const pd = state.buildProject()
  let name = stem
  let file: string | null = null
  let written: { res: SavedLayout; name: string } | null = null
  if (binding) {
    const snapshots = captureFigureSnapshotSave(pd, pj)
    try {
      written = await writeNewProjectFile(stem, pd)
    } catch (error) {
      resumeAutosaveAfterFork(ctx.documentId)
      return { ok: false, reason: 'write_failed', error }
    }
    name = written.name
    file = written.res.file ?? null
    // 写的途中切走了：文件已经在项目里了（新名字、没覆盖任何东西），但不把此刻开着的另一份换掉
    if (!stillCurrent(ctx)) return { ok: false, reason: 'switched', file }
    // 回执里的快照引用合进内存里这份（再带进副本）；原排版已摘出自动保存，这次合并不会把它写回盘
    mergeSavedFigureSnapshots(snapshots, written.res.figure_snapshots)
  }
  const latest = useDocumentStore.getState().buildProject()
  const id = await installFork({ ...latest, project: { ...latest.project, name } }, opts.beforeSwitch)
  if (!id) return { ok: false, reason: 'busy' }
  if (written && pj && file) {
    rememberLayoutName(name, written.res.name)
    rememberLayoutRevision(written.res.name ?? name, written.res.revision, pj)
    setProjectFile(
      { projectId: pj, name: written.res.name ?? name, file, revision: written.res.revision, dirty: false },
      id,
    )
  }
  useUiStore.getState().setStatus(msg('fork.done', { name }, 'workspace'), 'done')
  return { ok: true, id, name, file }
}
