/**
 * 同一份排版在两个窗口开着（ADR 0181）：谁在编辑、怎么交接。
 *
 * 角色（`documentStore.windowHold`）：后开的窗口 `contested`、被接管的窗口 `yielded`（中间态 `yielding`）。
 * 这里是**动作**：进入只读、接管（两个方向同一个协议）、让出、存成副本，以及登记「收到接管请求时该做什么」。
 * 只读的执行（写入口的门 + 自动保存挂起）在 `documentStore`；动作记住在 `documentRestore`（`heldWindow`）；
 * 消息在 `lib/docPresence`（BroadcastChannel，尽力而为）。
 */
import { create } from 'zustand'
import { msg } from '@/i18n'
import { backendErrorText } from '@/lib/api'
import { requestTakeover, setTakeoverHandler } from '@/lib/docPresence'
import { useDiagSendStore } from './diagSendStore'
import { forkCurrentDocument, forkCopyName } from './documentFork'
import { takeWindowEntries } from './documentRestore'
import { getWindowHold, reloadFromDisk, saveNow, setWindowHold, useDocumentStore } from './documentStore'
import { useUiStore, type StatusAction } from './uiStore'

/** 卡上的按钮正在忙（接管等对面存盘最长几秒）：防连点、按钮置忙 */
export const useDocWindowStore = create<{ busy: boolean }>(() => ({ busy: false }))
const setBusy = (busy: boolean) => useDocWindowStore.setState({ busy })

const OWNER = 'docWindow'

function feedbackAction(): StatusAction | undefined {
  if (useDiagSendStore.getState().capability === null) return undefined
  return {
    label: msg('settings.diagnostics.send.cardButton', undefined, 'dialogs'),
    run: () => useDiagSendStore.getState().setOpen(true),
  }
}

/** 收到「另一个窗口已端着这份」（`tavotto:doc-conflict`）：这个后开的窗口先只读，等用户选 */
export function enterContested(docId?: string): void {
  const s = useDocumentStore.getState()
  if (docId && docId !== s.documentId) return
  if (getWindowHold()) return
  setWindowHold({ role: 'contested', docId: s.documentId })
}

/**
 * 持有方收到接管请求：先把没存的改动存好，再让出（变成「被接管」，只读）。返回改动存成了没有。
 * 存不进去也照样让出——这个窗口里的改动留在内存里，卡上的「存成副本」能把它们存成新排版，不丢。
 * 已经只读的（后开的窗口、或已让出的）没有未存的改动，直接回「存成了」。
 */
async function yieldToOtherWindow(): Promise<boolean> {
  if (getWindowHold()) return true
  const docId = useDocumentStore.getState().documentId
  // `yielding`：不再收新编辑，但还要写盘
  setWindowHold({ role: 'yielding', docId })
  let flushed = false
  try {
    const state = await saveNow()
    flushed = state === 'saved' || state === 'clean'
  } catch {
    flushed = false
  }
  // 存盘期间用户换了排版：编辑权随旧排版走了，不替新排版设只读
  if (getWindowHold()?.docId === docId && useDocumentStore.getState().documentId === docId) {
    setWindowHold({ role: 'yielded', docId })
  }
  return flushed
}

/** App 挂载时登记（返回卸载函数）：这个窗口收到接管请求时做的事 */
export function startDocWindow(): () => void {
  setTakeoverHandler(yieldToOtherWindow)
  return () => setTakeoverHandler(null)
}

/**
 * 「在这里编辑」/「拿回来编辑」：请求对面存好并让出 → 载入盘上最新版 → 放开编辑 → 执行记住的操作。
 * 对面限时内没回（已关 / 卡死 / 旧版标签页）= 直接接管载入最新版；对面存不进去也照样接管（它的改动留在它那边）。
 * 载入失败：保持只读、说一句并给「发给我们看看」，动作放回去。
 */
export async function takeOverHere(): Promise<boolean> {
  const hold = getWindowHold()
  if (!hold || hold.role === 'yielding' || useDocWindowStore.getState().busy) return false
  setBusy(true)
  try {
    await requestTakeover()
    // 等的这几秒里用户换了排版 / 权属变了：不再动此刻的文档
    const now = getWindowHold()
    if (!now || now.docId !== hold.docId || useDocumentStore.getState().documentId !== hold.docId) return false
    // 这个窗口自己有没存下的改动（刚才让出时存盘失败）才需要保护；只读期间没有新编辑
    const keepMine = useDocumentStore.getState().dirty
    const entries = takeWindowEntries()
    const ok = await reloadFromDisk({ keepMine })
    if (!ok) {
      entries.putBack()
      useUiStore.getState().setStatus(msg('save.reloadFailed', undefined, 'workspace'), 'error', {
        owner: OWNER,
        action: feedbackAction(),
      })
      return false
    }
    setWindowHold(null)
    entries.run()
    return true
  } finally {
    setBusy(false)
  }
}

/**
 * 「存成副本」：把此刻窗口里的排版分成一份新排版（`documentFork`），分完放开编辑并执行记住的操作。
 * 项目文件没写成：整件不做、保持只读，一句话 + 「重试保存」；动作放回去。
 */
export async function saveCopyHere(): Promise<boolean> {
  if (useDocWindowStore.getState().busy) return false
  setBusy(true)
  try {
    const entries = takeWindowEntries()
    const out = await forkCurrentDocument()
    if (out.ok) {
      // 分叉换了文档，编辑权已随旧排版清掉；记住的动作落进副本
      entries.run()
      return true
    }
    entries.putBack()
    if (out.reason === 'write_failed') {
      useUiStore.getState().setStatus(
        msg(
          'save.projectFailed',
          { file: forkCopyName(useDocumentStore.getState().projectMeta.name), reason: backendErrorText(out.error) },
          'workspace',
        ),
        'error',
        {
          owner: OWNER,
          action: {
            label: msg('docBanner.retry', undefined, 'workspace'),
            run: () => {
              useUiStore.getState().clearStatusOwnedBy(OWNER)
              void saveCopyHere()
            },
          },
        },
      )
    }
    return false
  } finally {
    setBusy(false)
  }
}
