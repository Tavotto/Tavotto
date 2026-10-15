import { setAiSubmissionLedger } from '@/store/aiStore'
import { AI_DRAFT_CHANGED_EVENT, markSubmitted, removeAccepted } from './aiDraft'

/**
 * 把草稿存储（后端）注册成 aiStore 的提交账本（ADR 0132）。**只被助手面板 import**——有助手面板的宿主
 * （桌面 / 浏览器模式）才会带上它；Codex 内嵌画布与 playground 的包里没有这一行，
 * aiStore 在那里的账本是空的（也没人会调 `start`）。
 */
setAiSubmissionLedger({
  markSubmitted,
  // 确认之后后端按「草稿戳没变」清了草稿（或没清）：通知助手面板对账一次，输入框以后端为准（`useAiDraft` 听这个事件）
  removeAccepted: async (...a) => {
    await removeAccepted(...a)
    window.dispatchEvent(new Event(AI_DRAFT_CHANGED_EVENT))
  },
})
