import type { HTMLAttributes } from 'react'
import { Fragment } from 'react'
import { useTranslation } from 'react-i18next'
import { keyCaps } from '@/lib/keyCaps'
import { cn } from '@/lib/utils'

/**
 * 键帽。全产品只有这一种，两档尺寸：
 *   * `sm`（默认）：16px 内联小片，xs 圆角、surface-2 底、**没有边框**——设置页 / 说明
 *     文字里提到一个按键时用。有边框 + 28px 高就长得像一颗按钮（Session 6 之前常规页
 *     「快捷键」旁的那个 `?` 被读成帮助按钮）。
 *   * `md`：22px 的键帽（hairline + 2px 底边，像一颗真键），只给快捷键速查表——那里键帽
 *     本身就是内容，一列键帽要能一眼扫过去。
 */
export function Kbd({
  size = 'sm',
  className,
  ...props
}: HTMLAttributes<HTMLElement> & { size?: 'sm' | 'md' }) {
  return (
    <kbd
      {...props}
      className={cn(
        // 系统字体 + 等宽数字，不用等宽字体：11px 的 SF Mono 比正文重一档，一句话里两种字（2026-09-15 审计 B05）
        'inline-flex items-center justify-center text-xs leading-none tabular-nums',
        size === 'sm'
          ? 'h-4 min-w-4 rounded-xs bg-surface-2 px-1 text-ink-3'
          : 'h-[22px] min-w-[23px] rounded-xs border border-border border-b-2 border-b-border-strong bg-surface-2 px-1.5 font-medium text-ink-2',
        className,
      )}
    />
  )
}

/**
 * 一串键位画成一个个键帽（`⇧⌘S` → ⇧ ⌘ S；` / ` 隔开的「或」之间留一个斜杠）。
 * 命令面板与快捷键速查表共用；文本照旧是键位表里那一串，拆法在 `lib/keyCaps`。
 */
export function KeyCaps({ keys, className }: { keys: string; className?: string }) {
  const { t } = useTranslation('shortcuts')
  const groups = keyCaps(keys)
  return (
    <span className={cn('inline-flex flex-wrap items-center justify-end gap-x-1 gap-y-1', className)}>
      {groups.map((caps, gi) => (
        <Fragment key={gi}>
          {gi > 0 && (
            <>
              {/* 视觉是「/」，读屏读「或」：两组键帽之间是替代关系，不是一串连着按的键 */}
              <span aria-hidden className="text-xs text-ink-3">
                /
              </span>
              <span className="sr-only">{t('or')}</span>
            </>
          )}
          <span className="inline-flex items-center gap-0.5">
            {caps.map((k, ki) => (
              <Kbd key={ki} size="md">
                {k}
              </Kbd>
            ))}
          </span>
        </Fragment>
      ))}
    </span>
  )
}
