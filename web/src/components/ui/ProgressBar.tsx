import type { HTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

/**
 * 细进度条（4px）：包管理作业、更新下载、修复下载共用这一份。
 *
 * - `pct` 是数就是确定态：填充宽度 = pct%，`aria-valuenow` 照报。
 * - `pct === null` 是不确定态：一段 1/3 宽的填充来回扫（`animate-sweep`，与画布冷启动那条
 *   同一个 token）。**不做假百分比**——拿不到真数就不报 `aria-valuenow`。
 *   此前四处各写一份 `animate-pulse` 的定宽块：呼吸的块读不出「还在动」，而轨道是
 *   `surface-2` 放在 `surface-2` 的面板里，整条轨道看不见（2026-10-07 设计审计 P0）。
 * - 轨道是 `border` 那一档（ink 12% 叠在任何底上都看得见），填充 `ink`——蓝色不做大块背景。
 * - 减少动态效果时不扫，静止的那一段仍然说明「在进行」。
 */
export function ProgressBar({
  pct,
  label,
  className,
  ...props
}: Omit<HTMLAttributes<HTMLDivElement>, 'role' | 'children'> & {
  pct: number | null
  label: string
}) {
  return (
    <div
      {...props}
      role="progressbar"
      aria-label={label}
      aria-valuenow={pct ?? undefined}
      aria-valuemin={0}
      aria-valuemax={100}
      data-progress-indeterminate={pct === null || undefined}
      className={cn('h-1 overflow-hidden rounded-full bg-border', className)}
    >
      {pct === null ? (
        <div className="h-full w-1/3 rounded-full bg-ink animate-sweep motion-reduce:animate-none" />
      ) : (
        <div className="h-full bg-ink" style={{ width: `${pct}%` }} />
      )}
    </div>
  )
}
