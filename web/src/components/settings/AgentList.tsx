import { ChevronRight } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import type { AiAgentCaps } from '@/lib/api'
import { PRODUCT_NAME } from '@/lib/brand'
import { cn } from '@/lib/utils'
import { Toggle } from '../ui/Toggle'
import { AgentIcon } from './AgentIcon'
import { ag, AgentStateBadge, agentSubtitle } from './agentState'

/**
 * 编码 Agent 的清单：坐在「改图助手」那一组（`FieldGroup`）里的若干行。
 *
 * 每行只有 `[图标] 名称 状态 ……… [开关] ›`（ADR 0038）：路径、命令、检测来源、内部包名一个都不在一级页面上，
 * 全部归详情（那里可以复制）。**一行只有一个开关**（2026-10-07 设计审计 §9.1）：此前每行还有行首一颗「默认」
 * 单选——单选 + 开关 + 整行 + chevron 四种操作挤在一行；默认助手现在是组首一个 `Select`。
 *
 * 交互上一行有**两个**独立控件：覆盖整行的「打开详情」按钮，和它上面一层的启用开关。开关绝不能嵌在行按钮里
 * ——嵌套 button 在 HTML 里非法，浏览器会自行拆开 DOM，键盘与读屏的行为随之不可预期。
 *
 * 清单本身是组里的一个子项（`ul`，组给的 12 / 16 内边距由它自己收掉——`p-0`），行自己带 16 的左右内边距，
 * 行间一条左右各内缩 16 的 hairline（与组里别的行同一条线）。
 */
export function AgentList({
  agents,
  onOpen,
  onToggle,
  busyAgent,
}: {
  agents: AiAgentCaps[]
  onOpen: (id: string) => void
  onToggle: (id: string, enabled: boolean) => void
  /** 正在提交开关的那个 Agent（防重复点击） */
  busyAgent?: string | null
}) {
  return (
    <ul className="flex flex-col p-0">
      {agents.map((agent, i) => (
        <li
          key={agent.id}
          className={cn(
            'relative flex min-h-13 items-center gap-3 px-4',
            i > 0 && 'before:absolute before:inset-x-4 before:top-0 before:h-px before:bg-border',
          )}
        >
          {/*
            覆盖整行的点击区。放在 DOM 最前面 = Tab 先到它、再到开关，
            与视觉顺序一致。可访问名带上状态，读屏不必再去猜右边那个图标。

            `data-agent-open` 是 e2e 的稳定锚点（值 = Agent id）：可访问名
            里带着**会被文案改动重写**的那句话，用它定位等于每次改文案都
            重新下一次赌注（2026-09-07 就是这么红的）。
          */}
          <button
            type="button"
            data-agent-open={agent.id}
            onClick={() => onOpen(agent.id)}
            aria-label={ag('rowAria', { name: agent.display_name })}
            className={cn(
              'absolute inset-0 outline-none hover:bg-surface-hover focus-visible:focus-ring',
              // 第一行 / 最后一行的 hover 底跟着组的 12 圆角走，不在圆角外露出方角
              i === 0 && 'rounded-t-lg',
              i === agents.length - 1 && 'rounded-b-lg',
            )}
          />
          <AgentIcon iconKey={agent.icon_key} />
          <div className="pointer-events-none flex min-w-0 flex-1 flex-col justify-center gap-0.5 py-2">
            <div className="flex min-w-0 items-center gap-2">
              <span className="min-w-0 truncate text-base font-medium text-ink">{agent.display_name}</span>
              <AgentStateBadge state={agent.state} />
            </div>
            {agentSubtitle(agent) && (
              <p className="type-caption truncate">{agentSubtitle(agent)}</p>
            )}
          </div>
          {/* 开关浮在覆盖层之上；未安装 / 装坏了时禁用（开了也用不了） */}
          <div className="relative z-sticky flex shrink-0 items-center">
            <Toggle
              checked={agent.enabled && agent.installed}
              disabled={!agent.installed || busyAgent === agent.id}
              onChange={(v) => onToggle(agent.id, v)}
              aria-label={ag('toggleAria', { name: agent.display_name, product: PRODUCT_NAME })}
            />
          </div>
          <ChevronRight
            size={ICON_SIZE.xs}
            aria-hidden
            className="pointer-events-none shrink-0 text-ink-3"
          />
        </li>
      ))}
    </ul>
  )
}
