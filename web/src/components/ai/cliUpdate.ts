import { t as translate } from '@/i18n'
import type { AiAgentId, AiCliUpdate } from '@/lib/api'
import { useAgentInstallStore } from '@/store/agentInstallStore'
import { useAiStore } from '@/store/aiStore'
import { installErrorText } from '@/components/settings/connectAi/useAgentInstall'

/**
 * 「CLI 比模型清单旧」（2026-11-04：Codex 桌面版先升级、刷新了共用的模型目录，Tavotto 启动的旧 CLI 选到新型号 404）。
 * 判据与怎么更新都在后端（`ai_bridge.cli_update_plan`）；这里只把 `action` / `source` 翻成一句话、把按钮接到 `POST …/update`。
 */
const ai = (key: string, opts?: Record<string, unknown>) => translate(key, { ns: 'ai', ...opts })

/** 点一下就能更新（改用本机的新版 / 应用内装新版） */
export const updatableInApp = (u: AiCliUpdate | null | undefined): u is AiCliUpdate =>
  u != null && (u.action === 'switch' || u.action === 'install')

/** 只能自己更新时，按 CLI 是从哪儿装的给一句怎么做（来源闭集见后端 `ai_agents.SOURCES`；不认识的走通用一句） */
const MANUAL_BY_SOURCE: Record<string, string> = {
  homebrew: 'cliUpdate.how.homebrew',
  npm_global: 'cliUpdate.how.npm',
  path: 'cliUpdate.how.npm',
  package_binary: 'cliUpdate.how.npm',
  chatgpt_bundle: 'cliUpdate.how.chatgpt',
  codex_desktop_bundled: 'cliUpdate.how.desktop',
  windows_store: 'cliUpdate.how.desktop',
  managed: 'cliUpdate.how.managed',
}
export const manualHow = (u: AiCliUpdate): string => ai(MANUAL_BY_SOURCE[u.source ?? ''] ?? 'cliUpdate.how.other')

/** 起一次更新；完成后强制重新探测（`cli_update` 随之消失，失败卡的主按钮回到「重试」） */
export function startCliUpdate(agent: AiAgentId): void {
  useAgentInstallStore.getState().beginUpdate(agent, () => useAiStore.getState().loadCaps(true))
}

/** 这个 Agent 的更新进行到哪了（与安装共用一份进度：应用内装新版就是一次托管安装） */
export const useCliUpdateRunning = (agent: AiAgentId | null | undefined): boolean =>
  useAgentInstallStore((s) => (agent ? s.byAgent[agent]?.status === 'running' : false))

/** 最近一次更新 / 安装以失败收场时的一句话（与设置页同一份翻译 `installErrorText`；开始下一次、或成功后消失）。
 *  注意：设置页发起的安装写同一份进度，失败也会在这儿露出，可以接受。 */
export function useCliUpdateError(agent: AiAgentId | null | undefined): string | null {
  const entry = useAgentInstallStore((s) => (agent ? s.byAgent[agent] : undefined))
  const product = useAiStore((s) => s.caps?.agents.find((a) => a.id === agent)?.display_name) ?? agent ?? ''
  return entry?.status === 'error' ? (entry.message ?? installErrorText(entry.code, product)) : null
}
