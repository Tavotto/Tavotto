import { msg } from '@/i18n'
import { backendErrorMsg } from '@/lib/api'
import { useAiStore, type AiSession } from '@/store/aiStore'
import { useRenderStore } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'

/** 助手面板上「撤销这次修改」：撤回脚本，再把这次撤销的后果写进渲染态与状态栏。 */
export async function revertSession(session: AiSession) {
  const born = useAiStore.getState().generation
  let landed: boolean
  try {
    landed = await useAiStore.getState().revert(session.id)
  } catch (e) {
    // 脚本在这次修改之后又变过（`ai_revert_conflict`）等：说出口，不装作已回滚——
    // 但失败也是 A 的后果，换过项目就不在 B 的状态栏上说（#589）
    if (born === useAiStore.getState().generation)
      useUiStore.getState().setStatus(backendErrorMsg(e), 'error')
    return
  }
  // 撤销期间换了项目：它的后果（标脏、提示）属于 A，不许写进 B 的渲染态与状态栏（#589）
  if (!landed) return
  // 回滚后 worker 会话同样失效，重建让画布自动回到改动前的样子
  if (session.fileId) useRenderStore.getState().markStale([session.fileId])
  useUiStore.getState().setStatus(msg('session.revertedStatus', undefined, 'ai'))
}
