import type { ComponentType } from 'react'
import { AgentIcon } from '@/components/settings/AgentIcon'
import { Bot, CircleDashed, Layers2, ListChecks, Sparkles, GitCommitVertical, Wrench } from '@/components/ui/icons'
import type { IconComponent } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { cn } from '@/lib/utils'
import type { WaterfallIcon } from './types'

/** 键 → 图标集里的图标；codex / claude 走 AI 面板已有的厂商标（AgentIcon） */
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

/** 装一个 span 标记的方块：行里 22px、详情卡里 36px */
export function SpanIcon({ icon, size = 'sm' }: { icon: WaterfallIcon; size?: 'sm' | 'lg' }) {
  const box = size === 'sm' ? 'size-5.5 rounded-sm' : 'size-9 rounded-md'
  const glyph = size === 'sm' ? ICON_SIZE.sm : ICON_SIZE.md
  let content
  if (icon === 'codex' || icon === 'claude') {
    content = <AgentIcon iconKey={icon} size={glyph} glyphSize={glyph} />
  } else {
    const Glyph: ComponentType<{ size?: number }> = GENERIC[icon] ?? Layers2
    content = <Glyph size={glyph} />
  }
  return (
    <span
      aria-hidden
      data-trace-icon={icon}
      className={cn('flex shrink-0 items-center justify-center border border-border bg-surface text-ink-2', box)}
    >
      {content}
    </span>
  )
}
