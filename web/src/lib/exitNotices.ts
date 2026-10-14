import {
  getEngineExited,
  onEngineExited,
  restartEngine,
  takePreviousExit,
} from '@/lib/desktop'
import { useDiagSendStore } from '@/store/diagSendStore'
import { useExitNoticeStore } from '@/store/exitNoticeStore'

/**
 * 退出相关两张通知轨卡片的接线（ADR 0129）。App 挂载时调一次，返回取消函数。
 *
 * - **引擎意外退出**：订阅壳的事件，并回读一次（事件可能早于监听）。不看发送开关：
 *   这是修复卡，引擎没了谁都需要「重新启动」。
 * - **上次没有正常退出**：必须在 `diagSendStore.load()` **完成之后**才取（`takePreviousExit` 是取走即清空，
 *   发送入口关着时取走等于白白吞掉一次提示，所以先判能力再取）。`capability === null` 一律不取、不登记。
 *   取到才登记卡片；卡片只打开现有确认框，**这里不调 prepare / start**。
 */
export function initExitNotices(loaded: Promise<void>): () => void {
  let disposed = false
  let unlisten: (() => void) | null = null
  const store = useExitNoticeStore.getState()

  // **先等监听装好，再回读一次当前状态**：反过来的话，读到 null 之后、监听装好之前引擎恰好退出，事件没人接、
  // 读到的状态也已过期，修复卡要等重载页面才出现。两路写同一个 store，重复写入幂等。
  // 监听装好前已卸载：只撤销监听，不回读。
  void onEngineExited((kind) => store.setEngineExit(kind))
    .then(async (off) => {
      if (disposed) {
        off()
        return
      }
      unlisten = off
      const kind = await getEngineExited()
      if (kind && !disposed) store.setEngineExit(kind)
    })
    .catch(() => {})
  void loaded.then(async () => {
    if (useDiagSendStore.getState().capability === null) return
    // 取走即清空：开发态 StrictMode 的第一轮已卸载也要登记，不然这一次提示就丢了（showPrevious 幂等）
    const previous = await takePreviousExit()
    if (previous) useExitNoticeStore.getState().showPrevious(previous)
  })

  return () => {
    disposed = true
    unlisten?.()
  }
}

/** 「重新启动」：幂等（转圈期间再点无效）。成功时壳会把页面换成新引擎的，这里不必善后；没动手就恢复按钮。 */
export async function restartEngineFromCard(): Promise<void> {
  const s = useExitNoticeStore.getState()
  if (s.restarting) return
  s.setRestarting(true)
  const acted = await restartEngine()
  // 壳动了手就等页面被换掉；没动手（已在重启 / 出错）把按钮还给用户
  if (!acted) useExitNoticeStore.getState().setRestarting(false)
}
