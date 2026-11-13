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
import { currentProjectId } from '@/lib/session'
import { requestTakeover, setTakeoverHandler } from '@/lib/docPresence'
import { useDiagSendStore } from './diagSendStore'
import { forkCurrentDocument, forkCopyName } from './documentFork'
import { takeWindowEntries } from './documentRestore'
import { finishActiveGesture, finishFocusedRichText } from './gestureCoordinator'
import {
  getWindowHold,
  holdHasUnconfirmedContent,
  reloadFromDisk,
  saveNow,
  setWindowHold,
  useDocumentStore,
} from './documentStore'
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
  // 进行中的输入 / 拖动先收尾：只读之后事务收尾（`endTxn`）不过写入口的门，不收的话它会在只读期间落进内存
  finishFocusedRichText()
  finishActiveGesture()
  setWindowHold({ role: 'contested', docId: s.documentId })
}

/**
 * 持有方收到接管请求：先把没存的改动存好，再让出（变成「被接管」，只读）。返回改动存成了没有。
 * 存不进去也照样让出——这个窗口里的改动留在内存里，卡上的「存成副本」能把它们存成新排版，不丢。
 * 已经只读的窗口不回话（返回 null，见上面）。
 */
async function yieldToOtherWindow(): Promise<boolean | null> {
  // 已经只读的（后开的、已让出的、正在让出的）不是编辑者：不替编辑者回话（三个窗口时，
  // 请求方若认了它的 ack 就会在真正的编辑者存盘完成之前载入，拿到旧底）
  if (getWindowHold()) return null
  const docId = useDocumentStore.getState().documentId
  // 进行中的输入 / 拖动先收尾，让它们进这一次存盘（只读之后事务收尾不过门，不收会落进内存而不被存）
  finishFocusedRichText()
  finishActiveGesture()
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
    // 存盘没确认成功：记在编辑权上，接管 / 拿回来时据此把内存里这份保护起来（见 `holdHasUnconfirmedContent`）
    setWindowHold({ role: 'yielded', docId, ...(flushed ? {} : { unsaved: true }) })
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
  const pj0 = currentProjectId()
  setBusy(true)
  try {
    await requestTakeover()
    // 等的这几秒里用户换了排版 / 权属变了：不再动此刻的文档
    const now = getWindowHold()
    if (!now || now.docId !== hold.docId || useDocumentStore.getState().documentId !== hold.docId) return false
    // 这个窗口手里有没有「不确定已落盘」的内容：让出时存盘没成功、保存状态卡在失败 / 冲突、`dirty`、
    // 只读期间内存被改过——任何一个都不能当成已落盘，必须进恢复槽位（`dirty === false` 不是落盘的证据）
    const keepMine = holdHasUnconfirmedContent()
    const entries = takeWindowEntries()
    const ok = await reloadFromDisk({ keepMine })
    if (!ok) {
      // 读盘期间用户换了排版 / 项目（响应已被丢弃）：不是读盘失败，不吓人；记住的动作是对旧排版说的，丢掉
      const again = getWindowHold()
      if (
        !again ||
        again.docId !== hold.docId ||
        useDocumentStore.getState().documentId !== hold.docId ||
        currentProjectId() !== pj0
      ) {
        entries.drop()
        return false
      }
      entries.putBack()
      useUiStore.getState().setStatus(msg('save.reloadFailed', undefined, 'workspace'), 'error', {
        owner: OWNER,
        action: feedbackAction(),
      })
      return false
    }
    // 等待期间又记住的动作：放开之前再取一次（放开会丢掉没被取走的），按先后顺序一起执行
    const late = takeWindowEntries()
    setWindowHold(null)
    entries.run()
    late.run()
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
    // 分叉等待期间又记住的动作：在换文档（会清掉只读、丢没取走的）之前再取一次
    const later: { taken: ReturnType<typeof takeWindowEntries> | null } = { taken: null }
    const out = await forkCurrentDocument({
      beforeSwitch: () => {
        later.taken = takeWindowEntries()
      },
    })
    if (out.ok) {
      // 分叉换了文档，编辑权已随旧排版清掉；记住的动作按先后顺序落进副本
      entries.run()
      later.taken?.run()
      return true
    }
    later.taken?.putBack()
    entries.putBack()
    const retry: StatusAction = {
      label: msg('docBanner.retry', undefined, 'workspace'),
      run: () => {
        useUiStore.getState().clearStatusOwnedBy(OWNER)
        void saveCopyHere()
      },
    }
    if (out.reason === 'write_failed') {
      useUiStore.getState().setStatus(
        msg(
          'save.projectFailed',
          { file: forkCopyName(useDocumentStore.getState().projectMeta.name), reason: backendErrorText(out.error) },
          'workspace',
        ),
        'error',
        { owner: OWNER, action: retry },
      )
    } else if (out.reason === 'busy') {
      // 还没准备好 / 没换成：说一句，给一个能走通的出口
      useUiStore
        .getState()
        .setStatus(msg('fork.failed', undefined, 'workspace'), 'error', { owner: OWNER, action: retry })
    } else if (out.reason === 'switched') {
      // 文件已经写进项目了，只是写的途中切走了、此刻开着的是另一份：点名文件，别让人以为没存
      useUiStore.getState().setStatus(msg('fork.switched', { file: out.file ?? '' }, 'workspace'), 'info')
    }
    return false
  } finally {
    setBusy(false)
  }
}
