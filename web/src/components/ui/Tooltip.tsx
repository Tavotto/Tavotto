import * as RT from '@radix-ui/react-tooltip'
import type { ReactElement, ReactNode } from 'react'
import { cn } from '@/lib/utils'

export const TooltipProvider = ({ children }: { children: ReactNode }) => (
  <RT.Provider delayDuration={420} skipDelayDuration={280}>
    {children}
  </RT.Provider>
)

interface TipProps {
  label: ReactNode
  shortcut?: string
  side?: 'top' | 'bottom' | 'left' | 'right'
  /**
   * `dark`（默认）：按钮名一类的短提示。`light`：说明一件事的小气泡——白底黑字、浮层阴影（与菜单同一套 `bg-surface` + `shadow-pop`），
   * 多行时按行排（期刊没列出的格式、期刊的分辨率分档、「按期刊调整」逐条改动）。
   */
  tone?: 'dark' | 'light'
  children: ReactElement
}

export function Tip({ label, shortcut, side = 'bottom', tone = 'dark', children }: TipProps) {
  return (
    <RT.Root>
      <RT.Trigger asChild>{children}</RT.Trigger>
      <RT.Portal>
        <RT.Content
          data-tooltip
          data-tooltip-tone={tone}
          side={side}
          sideOffset={6}
          className={cn(
            // pointer-events-none：气泡只是说明，不是控件。聚焦触发的气泡会停在
            // 触发器下方、盖住下一排按钮（弹层自动聚焦第一个分段项时实测撞见），
            // 不让它吃点击。Radix 的定位外壳也得一起放行：index.css 里那条
            // `[data-radix-popper-content-wrapper]:has([role='tooltip'])`
            'pointer-events-none z-tooltip flex items-center gap-2',
            tone === 'light'
              ? 'max-w-72 whitespace-pre-line rounded-md bg-surface px-2.5 py-1.5 text-xs leading-relaxed text-ink shadow-pop'
              : 'rounded-sm bg-ink px-2 py-1 text-sm text-surface',
            'origin-[var(--radix-tooltip-content-transform-origin)]',
            'data-[state=delayed-open]:animate-pop-in',
            // instant-open = 连续划过同组按钮时的即时切换：再播一次进场会闪，只淡入
            'data-[state=instant-open]:animate-fade-in',
            // 关闭后立即卸载 Radix 的事件层：退场动画会让它继续截获 Escape，
            // 使刚获得焦点的编辑器收不到这一下键。
          )}
        >
          <span data-tooltip-label>{label}</span>
          {shortcut && (
            <span className="text-xs tabular-nums text-surface/60">{shortcut}</span>
          )}
        </RT.Content>
      </RT.Portal>
    </RT.Root>
  )
}
