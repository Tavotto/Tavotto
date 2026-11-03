import type { CSSProperties, HTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

/**
 * 细进度条（铅笔排线，11px）：包管理作业、准备卡、更新下载、修复下载、导出进度共用这一份。
 * 画法全在 `index.css` 的 `.progress-track`（整像素 5px 斜线瓦片做 mask，任何缩放都锐利）。
 *
 * - `pct` 是数就是确定态：已完成的一段宽度 = pct%（吸附到整条斜线），`aria-valuenow` 照报。
 * - `pct === null` 是不确定态：整片排线以 2.2s 线性慢速斜向流动。**不做假百分比**——拿不到真数就不报
 *   `aria-valuenow`。减少动态效果时静止，排线本身仍说明「在进行」。
 *   此前四处各写一份呼吸（Tailwind 的 pulse 动画）的定宽块：呼吸的块读不出「还在动」
 *   （2026-10-07 设计审计 P0）。
 * - 蓝色不做大块背景：底排线 ink 15%、已完成 ink-3，纸白底 + 发丝框保证放在任何面板里都看得见。
 * - 画布冷启动那条（PanelView 内的 2px 扫条，叠在图上）不是这个组件，仍用 `animate-sweep`。
 */
export function ProgressBar({
  pct,
  label,
  className,
  style,
  ...props
}: Omit<HTMLAttributes<HTMLDivElement>, 'role' | 'children'> & {
  pct: number | null
  label: string
}) {
  const clamped = pct === null ? null : Math.min(100, Math.max(0, pct))
  const vars = { '--progress-w': `${clamped ?? 0}%` } as CSSProperties
  return (
    <div
      {...props}
      role="progressbar"
      aria-label={label}
      aria-valuenow={pct ?? undefined}
      aria-valuemin={0}
      aria-valuemax={100}
      data-progress-indeterminate={pct === null || undefined}
      style={{ ...vars, ...style }}
      className={cn('progress-track', className)}
    >
      <div className="progress-base" />
      <div className="progress-fill" />
    </div>
  )
}
