import { cn } from '@/lib/utils'
import { Check, CircleDashed } from '../ui/icons'
import { ICON_SIZE } from '../ui/Icon'
import { Spinner } from '../ui/Spinner'

export type TodoMarkState = 'done' | 'active' | 'pending'

/**
 * 待办行行首的状态图标（ADR 0214 建议待办卡的那一套）：做完 = 绿勾、在做 = 转圈、没开始 = 点状圈，占勾选框同一格（15px）。
 * 修改方案弹窗按批注重改进行中的批注行也用它（只画「没开始」那一态：后端只报整体阶段，不假装逐条在做）。
 */
export function TodoMark({ state, className }: { state: TodoMarkState; className?: string }) {
  return (
    <span className={cn('flex size-[15px] shrink-0 items-center justify-center', className)} aria-hidden data-todo-progress={state}>
      {state === 'done' ? <Check size={ICON_SIZE.sm} className="text-ok" /> : state === 'active' ? <Spinner size={ICON_SIZE.sm} className="text-ink" /> : <CircleDashed size={ICON_SIZE.sm} className="text-ink-faint" />}
    </span>
  )
}
