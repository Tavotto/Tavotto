import {
  AlertTriangle,
  CheckCircle2,
  CircleDashed,
  KeyRound,
  MinusCircle,
  type LucideIcon,
} from 'lucide-react'
import { t as translate } from '@/i18n'
import { PRODUCT_NAME } from '@/lib/brand'
import type { AiAgentCaps, AiAgentUiState } from '@/lib/api'

/** 本节文案在 dialogs:settings.agents.* 下 */
export const ag = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.agents.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 状态 → 图标 + 颜色。**颜色只是佐证，判据是图形与文字**：
 * 每一档都有自己的图标形状和自己的一句话，色觉障碍与灰度截图下同样读得出。
 *
 * 「未安装」刻意用中性灰 + 虚线圆——它不是错误，是一件还没做的事；
 * 危险色只留给 `broken`（找到了安装却根本启动不了），警示色只留给
 * `needs_auth`（CLI 明确说要登录）。
 */
const PRESENTATION: Record<AiAgentUiState, { icon: LucideIcon; tone: string }> = {
  detecting: { icon: CircleDashed, tone: 'text-ink-3' },
  ready: { icon: CheckCircle2, tone: 'text-ink' },
  installed: { icon: CheckCircle2, tone: 'text-ink-2' },
  needs_auth: { icon: KeyRound, tone: 'text-warn' },
  broken: { icon: AlertTriangle, tone: 'text-danger' },
  not_installed: { icon: CircleDashed, tone: 'text-ink-3' },
  disabled: { icon: MinusCircle, tone: 'text-ink-3' },
}

export const stateLabel = (state: AiAgentUiState): string => ag(`state.${state}`)

export function AgentStateBadge({
  state,
  className,
}: {
  state: AiAgentUiState
  className?: string
}) {
  const { icon: Icon, tone } = PRESENTATION[state] ?? PRESENTATION.not_installed
  return (
    <span className={`flex items-center gap-1 text-xs ${tone} ${className ?? ''}`}>
      <Icon size={12} strokeWidth={2} aria-hidden />
      {stateLabel(state)}
    </span>
  )
}

/**
 * 行的第二行说明：装了就说清「哪个版本、在哪儿」，没装就说清下一步。
 * 完整路径过长时靠 CSS 省略，`title` 与详情页给全值。
 */
export function agentSubtitle(agent: AiAgentCaps): string {
  if (agent.installed) {
    return [agent.version, agent.executable_path].filter(Boolean).join(' · ')
  }
  if (agent.state === 'broken') return ag('subtitle.broken')
  return ag('subtitle.notInstalled', { product: PRODUCT_NAME })
}
