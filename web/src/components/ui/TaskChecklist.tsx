import { useId, useState, type HTMLAttributes, type ReactNode } from 'react'

import { cn } from '@/lib/utils'
import { ICON_SIZE } from './Icon'
import { ChevronDown, CircleCheck, CircleDashed } from './icons'
import { Spinner } from './Spinner'

export type TaskStepState = 'done' | 'active' | 'pending'

export interface TaskStep {
  /** 稳定 id：写进 `data-task-step`，用例与诊断认它 */
  id: string
  label: ReactNode
  state: TaskStepState
  /** 进行中那一步右侧的小胶囊：**只放真数字**（「12 / 25 MB」「第 2 轮」），拿不到就不传 */
  meta?: ReactNode
}

/**
 * 任务清单（2026-11-25 用户拍板「照视频的设计」，替掉准备 / 修复里的进度条）：
 * 灰底圆角块，标题行「转圈 · 这件事在干什么 · 完成数/总数 ▾」，下面一开始就列全所有步骤——
 * 做完的灰色实心勾 + 删除线**连勾一起**划掉，进行中的转圈 + 可选的数字胶囊，没开始的点状圈。
 *
 * - **高度从头到尾不变**：步骤一开始就全列出来，运行中只换图标 / 颜色 / 删除线，不插行、不撑大（用户：「不喜欢突然有东西撑大」）。
 *   收起只在用户点 ▾ 时发生。
 * - 全做完：标题行的转圈换成实心勾、计数变 ink。
 * - 步骤状态由调用方从后端事实翻译过来，这里不判断；没开始的字仍是 ink-3（可读字 ≥4.5:1，ink-faint 只给图标）。
 * - 不是 live region：一句话的播报由宿主自己的标题负责，这里只是结构（`<ol>` + 进行中那步 `aria-current="step"`）。
 */
export function TaskChecklist({
  title,
  steps,
  busyIcon,
  bare = false,
  row = false,
  className,
  ...rest
}: Omit<HTMLAttributes<HTMLDivElement>, 'title'> & {
  /** 标题行那句话（`bare` 时不画，可不给） */
  title?: ReactNode
  steps: TaskStep[]
  /** 标题行进行中的图标（默认 `Spinner`；`null` = 留空位不画——宿主别处已有在动的标记时，免得一屏几样东西一起转）；步骤行仍是转圈 */
  busyIcon?: ReactNode
  /** 只要步骤、不要标题行（例如修改方案卡：清单在缩略图下面、占正文的位置）：不能收起，行距收紧一档 */
  bare?: boolean
  /** 步骤排成一行（只配 `bare`）：修改方案弹窗重改进行中，清单在图下面只占一行，不挤图的高度 */
  row?: boolean
}) {
  const inline = bare && row
  const [open, setOpen] = useState(true)
  const listId = useId()
  const total = steps.length
  const done = steps.filter((s) => s.state === 'done').length
  const all = total > 0 && done === total
  return (
    <div
      {...rest}
      data-task-checklist
      data-task-all={all || undefined}
      className={cn('flex flex-col rounded-lg bg-surface-hover px-3.5', bare ? 'py-1.5' : open ? 'pb-2 pt-2.5' : 'py-2', className)}
    >
      {!bare && (
        <button
          type="button"
          data-task-toggle
          aria-expanded={open}
          aria-controls={listId}
          onClick={() => setOpen((v) => !v)}
          className="group flex h-6 w-full items-center gap-2 rounded-sm text-left outline-none focus-visible:focus-ring"
        >
          <span className="flex h-4 w-4 shrink-0 items-center justify-center" aria-hidden>
            {all ? (
              <CircleCheck size={ICON_SIZE.sm} className="text-ink" />
            ) : (
              (busyIcon === undefined ? <Spinner size={ICON_SIZE.sm} className="text-ink" /> : busyIcon)
            )}
          </span>
          <span className="min-w-0 flex-1 truncate text-base font-medium text-ink" data-task-title>
            {title}
          </span>
          <span className={cn('text-sm tabular-nums', all ? 'font-medium text-ink' : 'text-ink-3')} data-task-count>
            {done}/{total}
          </span>
          <ChevronDown
            size={ICON_SIZE.xs}
            aria-hidden
            className={cn('-ml-1 text-ink-3 transition-transform group-hover:text-ink-2 motion-reduce:transition-none', !open && '-rotate-90')}
          />
        </button>
      )}
      <ol id={listId} hidden={!bare && !open} className={cn('flex', inline ? 'flex-row flex-wrap items-center gap-x-4' : 'flex-col', !bare && 'pt-2')} data-task-steps data-task-row={inline || undefined}>
        {steps.map((s) => (
          <li
            key={s.id}
            data-task-step={s.id}
            data-step-state={s.state}
            aria-current={s.state === 'active' ? 'step' : undefined}
            className={cn('flex items-center gap-2', inline ? 'h-5' : bare ? 'h-6' : 'h-7')}
          >
            {/* 删除线挂在「图标 + 文字」整组上：做完时从左往右连勾一起划掉（用户 11-25） */}
            <span
              className={cn(
                'relative flex min-w-0 items-center gap-2',
                'after:pointer-events-none after:absolute after:inset-x-0 after:top-1/2 after:h-px after:origin-left after:bg-ink-3',
                'after:transition-transform after:duration-slow motion-reduce:after:transition-none',
                s.state === 'done' ? 'after:scale-x-100' : 'after:scale-x-0',
              )}
            >
              <span className="flex h-4 w-4 shrink-0 items-center justify-center" aria-hidden>
                {s.state === 'done' ? (
                  <CircleCheck size={ICON_SIZE.sm} className="text-ink-faint" />
                ) : s.state === 'active' ? (
                  <Spinner size={ICON_SIZE.sm} className="text-ink" />
                ) : (
                  <CircleDashed size={ICON_SIZE.sm} className="text-ink-faint" />
                )}
              </span>
              <span className={cn('min-w-0 truncate text-sm', s.state === 'active' ? 'font-medium text-ink' : 'text-ink-3')}>
                {s.label}
              </span>
            </span>
            {s.state === 'active' && s.meta != null && (
              <span
                className="ml-auto shrink-0 whitespace-nowrap rounded-full bg-surface px-2 py-0.5 text-xs tabular-nums leading-4 text-ink-3"
                data-task-meta
              >
                {s.meta}
              </span>
            )}
          </li>
        ))}
      </ol>
    </div>
  )
}
