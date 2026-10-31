import { AgentIcon } from '@/components/settings/AgentIcon'
import { Bot, CircleDashed, GitCommitVertical, Layers2, ListChecks, Sparkles, Wrench } from '@/components/ui/icons'
import type { IconComponent } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { cn } from '@/lib/utils'
import type { WaterfallIcon } from './types'

/** 键 → 图标集里的图标（一律画实心孪生 `filled`）；codex / claude 走 AI 面板已有的厂商标（AgentIcon，本身无框） */
const GENERIC: Record<Exclude<WaterfallIcon, 'codex' | 'claude'>, IconComponent> = {
  task: ListChecks,
  builtin: Bot,
  agent: Bot,
  model: Sparkles,
  tool: Wrench,
  wait: CircleDashed,
  phase: Layers2,
  stage: GitCommitVertical,
}

/** 按种类上色（语义 token）：智能体 / 模型走 accent，工具 ink-2，等待 / 阶段 ink-3；失败行统一 danger */
const TINT: Record<WaterfallIcon, string> = {
  task: 'text-accent',
  codex: 'text-ink-2',
  claude: 'text-ink-2',
  builtin: 'text-accent',
  agent: 'text-accent',
  model: 'text-accent',
  tool: 'text-ink-2',
  wait: 'text-ink-3',
  phase: 'text-ink-3',
  stage: 'text-ink-3',
}

/** span 的种类标记：不带框的实心图标。行里 14px、详情卡里 16px */
export function SpanIcon({ icon, size = 'sm', failed }: { icon: WaterfallIcon; size?: 'sm' | 'lg'; failed?: boolean }) {
  const px = size === 'sm' ? ICON_SIZE.sm : ICON_SIZE.md
  const tone = failed ? 'text-danger' : TINT[icon]
  return (
    <span aria-hidden data-trace-icon={icon} className={cn('flex shrink-0 items-center justify-center', tone)}>
      {icon === 'codex' || icon === 'claude' ? (
        <AgentIcon iconKey={icon} size={px} glyphSize={px} className="text-current" />
      ) : (
        (() => {
          const Glyph = GENERIC[icon] ?? Layers2
          return <Glyph size={px} filled />
        })()
      )}
    </span>
  )
}
