import type { CSSProperties } from 'react'
import { cn } from '@/lib/utils'
import { Check } from '../ui/icons'
import { ICON_SIZE } from '../ui/Icon'
import { rs } from './reviewText'

/** 画在界面上 / 画在纸上的两套外观（分开放：纸上只许纸墨，界面的 ink 不进同一段类名） */
const UI_MARK = { on: 'bg-ink text-surface', solid: 'bg-ink text-surface', off: 'inset-ring inset-ring-border-strong text-ink-2' }
const PAPER_MARK = { on: 'bg-shadow text-paper', solid: 'bg-shadow text-paper inset-ring inset-ring-paper', off: 'bg-paper inset-ring inset-ring-paper-ink-3 text-paper-ink-2' }

/**
 * 意见序号小圆：与画布圆点对应；整体意见「全」；已处理换 ✓；选中 = 近黑实心。
 * 审阅页签的意见条与助手里的「审阅意见」卡共用这一份（同一套编号样式，不各画一遍）。
 */
export function ReviewMark({
  n,
  overall = false,
  resolved = false,
  selected = false,
  onPaper = false,
  solid = false,
  className,
  style,
}: {
  n: number
  overall?: boolean
  resolved?: boolean
  selected?: boolean
  /** 画在图（纸）上：纸两套主题同值，小圆也不跟着主题反相——纸色底 + 纸墨字，选中用阴影色实心 */
  onPaper?: boolean
  /** 实心深墨圆标 + 反白数字（画在图上时再加 1px 白描边）：批注与意见缩略图上的编号用这一档 */
  solid?: boolean
  className?: string
  /** 落在缩略图上时的定位（画面比例坐标由调用方换成百分比） */
  style?: CSSProperties
}) {
  if (resolved)
    return (
      <span className={cn('inline-grid size-4 shrink-0 place-items-center text-ok', className)} style={style} data-review-check>
        <Check size={ICON_SIZE.sm} aria-label={rs('comment.done')} />
      </span>
    )
  return (
    <span
      aria-hidden
      className={cn(
        'inline-grid size-4 shrink-0 place-items-center rounded-full text-xs leading-none tabular-nums',
        (onPaper ? PAPER_MARK : UI_MARK)[solid ? 'solid' : selected ? 'on' : 'off'],
        className,
      )}
      style={style}
      data-review-mark={overall ? 'all' : n}
    >
      {overall ? rs('comment.allMark') : n > 9 ? '9+' : n}
    </span>
  )
}
