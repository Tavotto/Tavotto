import { Bot, Sparkles, SquareTerminal, type LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'

/**
 * 编码 Agent 的图标。
 *
 * **只用本地矢量**（lucide 的线性图标 + 一个中性色块），不加载任何远程图片，
 * 也不内嵌各家的品牌素材——那是别人的商标，摆在我们的界面里既不合适也无法
 * 随他们改版跟进。区分靠形状与名称，名称本身就是可访问文本。
 *
 * `icon_key` 是后端注册表给的稳定键；表里没有的 key 落到通用图标，
 * **不会变成空白**（加第三个 Agent 时忘了配图不该表现成「图标不见了」）。
 */
const GLYPHS: Record<string, LucideIcon> = {
  codex: SquareTerminal,
  claude: Sparkles,
}

export function AgentIcon({
  iconKey,
  size = 36,
  className,
}: {
  iconKey: string
  /** 外框边长（列表 36、详情 40） */
  size?: number
  className?: string
}) {
  const Glyph = GLYPHS[iconKey] ?? Bot
  return (
    <span
      aria-hidden
      style={{ width: size, height: size }}
      className={cn(
        'flex shrink-0 items-center justify-center rounded-md border border-border bg-surface-2 text-ink-2',
        className,
      )}
    >
      <Glyph size={Math.round(size * 0.5)} strokeWidth={1.75} />
    </span>
  )
}
