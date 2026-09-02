import { ChevronRight } from 'lucide-react'
import type { AiAgentCaps } from '@/lib/api'
import { PRODUCT_NAME } from '@/lib/brand'
import { cn } from '@/lib/utils'
import { Toggle } from '../ui/Toggle'
import { AgentIcon } from './AgentIcon'
import { ag, AgentStateBadge, agentSubtitle, agentVersionLabel } from './agentState'

/**
 * 编码 Agent 的分组列表。
 *
 * **一个容器、若干行**，不是一堆各自带边框的小卡片——两个 Agent 时卡片还看得
 * 过去，第三个一加就变成一片碎盒子。区域边框一圈、行间一条细分隔线，
 * 层级靠留白与字号，持久表面不上阴影（web/AGENTS.md 的视觉纪律）。
 *
 * 每行默认只有 `[图标] 名称   版本号   状态`（ADR 0038）：路径、命令、检测
 * 来源、内部包名一个都不在一级页面上，全部归详情（那里可以复制）。
 *
 * 交互上一行有**两个**独立控件：覆盖整行的「打开详情」按钮，和它上面一层的
 * 启用开关。开关绝不能嵌在行按钮里——嵌套 button 在 HTML 里非法，浏览器会
 * 自行拆开 DOM，键盘与读屏的行为随之不可预期。
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
    <ul className="overflow-hidden rounded-md border border-border bg-surface">
      {agents.map((agent, i) => (
        <li
          key={agent.id}
          className={cn('relative flex min-h-12 items-center gap-3 px-3',
            i > 0 && 'border-t border-border')}
        >
          {/*
            覆盖整行的点击区。放在 DOM 最前面 = Tab 先到它、再到开关，
            与视觉顺序一致。可访问名带上状态，读屏不必再去猜右边那个图标。
          */}
          <button
            type="button"
            onClick={() => onOpen(agent.id)}
            aria-label={ag('rowAria', { name: agent.display_name })}
            className="absolute inset-0 rounded-md outline-none hover:bg-ink/[.025] focus-visible:focus-ring"
          />
          <AgentIcon iconKey={agent.icon_key} />
          {/*
            一行只有三件事实：名称 · 版本号 · 状态（ADR 0038）。路径、命令、
            检测来源全在详情里——「Tavotto 会自动发现……」那种解释也不在这儿。
          */}
          <div className="pointer-events-none flex min-w-0 flex-1 flex-col justify-center gap-0.5 py-2">
            <div className="flex min-w-0 items-center gap-3">
              <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink">
                {agent.display_name}
              </span>
              {agent.installed && agentVersionLabel(agent.version) && (
                <span
                  data-agent-version
                  className="max-w-32 shrink-0 truncate font-mono text-xs text-ink-3"
                  aria-label={ag('versionAria', { version: agentVersionLabel(agent.version) })}
                >
                  {agentVersionLabel(agent.version)}
                </span>
              )}
              <AgentStateBadge state={agent.state} className="shrink-0 whitespace-nowrap" />
            </div>
            {agentSubtitle(agent) && (
              <p className="truncate text-xs text-ink-3">{agentSubtitle(agent)}</p>
            )}
          </div>
          {/* 开关浮在覆盖层之上；未安装 / 装坏了时禁用（开了也用不了） */}
          <div className="relative z-10 flex shrink-0 items-center gap-1">
            <Toggle
              checked={agent.enabled && agent.installed}
              disabled={!agent.installed || busyAgent === agent.id}
              onChange={(v) => onToggle(agent.id, v)}
              aria-label={ag('toggleAria', { name: agent.display_name, product: PRODUCT_NAME })}
            />
          </div>
          <ChevronRight
            size={14}
            aria-hidden
            className="pointer-events-none shrink-0 text-ink-faint"
          />
        </li>
      ))}
    </ul>
  )
}
