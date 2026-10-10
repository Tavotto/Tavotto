import { t as translate } from '@/i18n'
import { PRODUCT_NAME } from '@/lib/brand'
import type { AiAgentCaps, AiAgentUiState } from '@/lib/api'
import type { StatusTone } from '../ui/Notice'
import { StatusPill } from '../ui/StatusPill'

/** 本节文案在 dialogs:settings.agents.* 下 */
export const ag = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.agents.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 状态 → `StatusPill` 的语气（2026-10-07 设计审计 §9.1）。**每一档都有字**：此前「可用」只有一颗绿点、别的状态
 * 有字——一种状态一种写法，灰度截图与色觉障碍下「可用」读不出来。颜色只是佐证，判据是那句话。
 *
 * 「未安装」刻意用中性——它不是错误，是一件还没做的事；危险色只留给 `broken`（找到了安装却根本启动不了），
 * 警示色只留给 `needs_auth`（CLI 明确说要登录）。
 */
const TONE: Record<AiAgentUiState, StatusTone> = {
  detecting: 'neutral',
  ready: 'ok',
  installed: 'neutral',
  needs_auth: 'warn',
  broken: 'danger',
  not_installed: 'neutral',
  disabled: 'neutral',
}

export const stateLabel = (state: AiAgentUiState): string => ag(`state.${state}`)

export function AgentStateBadge({
  state,
  className,
}: {
  state: AiAgentUiState
  className?: string
}) {
  // `data-agent-state` 给判据用：一行里还有 Agent 自己的品牌图标，判状态认它，不认图形
  return (
    <StatusPill data-agent-state={state} tone={TONE[state] ?? 'neutral'} dot className={className}>
      {stateLabel(state)}
    </StatusPill>
  )
}

/**
 * 版本号——**只有号**。CLI 的 `--version` 打出来的是 `codex-cli 0.42.0` /
 * `2.0.14 (Claude Code)` 这类带内部包名的行，一级列表上只该出现数字部分
 * （内部包名不是用户要认的东西，ADR 0038）。抽不出数字就回原文（诊断材料，
 * 不翻），空就回 null 让调用方决定显示什么。
 */
export function agentVersionLabel(version: string | null | undefined): string | null {
  if (!version) return null
  const m = /\d+(?:\.\d+)+(?:[-+.][0-9A-Za-z.-]+)?/.exec(version)
  // 抽不出版本号就**不显示**：`--version` 的第一行有时是 shim 的报错
  // （带完整路径），那正是一级页面不该出现的东西；原文留在详情里
  return m ? m[0] : null
}

/**
 * 行的第二行说明：**没装 / 装坏了才有**——装好的那一行只有名称、版本、状态，
 * 路径与命令归详情（ADR 0038；此前这里放的是 `版本 · 完整路径`）。
 */
export function agentSubtitle(agent: AiAgentCaps): string | null {
  if (agent.installed) return null
  if (agent.state === 'broken') return ag('subtitle.broken')
  return ag('subtitle.notInstalled', { product: PRODUCT_NAME })
}
