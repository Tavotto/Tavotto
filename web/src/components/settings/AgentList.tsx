import { ChevronRight } from 'lucide-react'
import { ICON_SIZE } from '@/components/ui/Icon'
import type { AiAgentCaps } from '@/lib/api'
import { PRODUCT_NAME } from '@/lib/brand'
import { cn } from '@/lib/utils'
import { Button } from '../ui/Button'
import { Toggle } from '../ui/Toggle'
import { AgentIcon } from './AgentIcon'
import { ag, AgentStateBadge, agentSubtitle } from './agentState'

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
  defaultId = null,
  onSetDefault,
}: {
  agents: AiAgentCaps[]
  onOpen: (id: string) => void
  onToggle: (id: string, enabled: boolean) => void
  /** 正在提交开关的那个 Agent（防重复点击） */
  busyAgent?: string | null
  /** 此刻实际作为默认的那个（首选不可用时是回退到的那个）；null = 一个都不可用 */
  defaultId?: string | null
  /** 「默认」按钮：把这一行设为默认编码 Agent。不给就不画这颗按钮 */
  onSetDefault?: (id: string) => void
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

            `data-agent-open` 是 e2e 的稳定锚点（值 = Agent id）：可访问名
            里带着**会被文案改动重写**的那句话，用它定位等于每次改文案都
            重新下一次赌注（2026-09-07 就是这么红的）。
          */}
          <button
            type="button"
            data-agent-open={agent.id}
            onClick={() => onOpen(agent.id)}
            aria-label={ag('rowAria', { name: agent.display_name })}
            className="absolute inset-0 rounded-md outline-none hover:bg-ink/[.025] focus-visible:focus-ring"
          />
          <AgentIcon iconKey={agent.icon_key} />
          {/*
            一行只回答用户此刻的问题：**这个能不能用、去哪儿配**——名称 + 状态
            + 启用开关 + 进详情（ADR 0038；审计 T44 把版本号也移走了：它在列表
            与详情上重复了一遍，而列表上的那份没有任何可操作性）。路径、命令、
            检测来源同样只在详情里。
          */}
          <div className="pointer-events-none flex min-w-0 flex-1 flex-col justify-center gap-0.5 py-2">
            <div className="flex min-w-0 items-center gap-3">
              <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink">
                {agent.display_name}
              </span>
              <AgentStateBadge state={agent.state} className="shrink-0 whitespace-nowrap" />
            </div>
            {agentSubtitle(agent) && (
              <p className="truncate text-xs text-ink-3">{agentSubtitle(agent)}</p>
            )}
          </div>
          {/* 开关浮在覆盖层之上；未安装 / 装坏了时禁用（开了也用不了）。
              「默认」按钮就在行里（2026-09-11 用户反馈：不再单独一行下拉框）：
              当前默认的那颗是按下态，只有可用的 Agent 才能被设为默认 */}
          <div className="relative z-10 flex shrink-0 items-center gap-1.5">
            {onSetDefault && (
              <Button
                size="sm"
                variant={agent.id === defaultId ? 'primary' : 'outline'}
                aria-pressed={agent.id === defaultId}
                aria-label={ag('setDefaultAria', { name: agent.display_name })}
                disabled={!agent.usable}
                onClick={() => onSetDefault(agent.id)}
              >
                {ag('defaultButton')}
              </Button>
            )}
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
            className="pointer-events-none shrink-0 text-ink-faint"
          />
        </li>
      ))}
    </ul>
  )
}
