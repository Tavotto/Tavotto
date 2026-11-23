import { hostedFailureMessage, hostedOwnKeyLabel, type HostedFailure } from '@/lib/hostedAi'
import { requestConnectAi } from '@/store/connectAiStore'
import { useUiStore } from '@/store/uiStore'

/**
 * 「Tavotto Agent」（内置 AI，ADR 0200 §八）没能开任务时给用户的那一条状态胶囊：一句话（额度 / 恢复日期 / 授权 / 暂时连不上）
 * + 一个动作「改用我自己的 Key」（打开连接 AI，自带 Key 永远免费可用）。语气是 info（不红、不常驻、不挤页面）。
 * 改图助手面板自己在输入框上方有同一套说明（`AiPanel`），这里只给没有那块位置的入口（AI 修复卡）。
 */
export function announceHostedFailure(f: HostedFailure): void {
  useUiStore.getState().setStatus(hostedFailureMessage(f), 'info', {
    action: { label: hostedOwnKeyLabel(), run: () => requestConnectAi() },
  })
}
