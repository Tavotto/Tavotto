import { useState } from 'react'
import { t as translate } from '@/i18n'
import { dirTail } from '@/lib/pathDisplay'
import { cn } from '@/lib/utils'
import { CopyButton } from './CopyButton'

const st = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 设置页里显示一个目录（审计 T40）。
 *
 * 末级目录的判据用 `lib/pathDisplay.dirTail`——写回确认框（T34）显示备份目录
 * 用的是同一句话，两处不许各写各的。
 *
 * 改动前是一行 `truncate` 的绝对路径：末尾被截掉，而末尾正是能认出「这是
 * 哪个目录」的那一段（`…/Application Support/Tavotto/tuto…`）。用户既看不出
 * 是哪儿，也没法核实。
 *
 * 现在：**默认只给末级目录**（人认得出的那一级），完整路径按需展开，旁边
 * 一个复制按钮。展开是真的展开——`break-all` 全文可见，不再有第二次截断。
 */
export function PathValue({
  path,
  name,
  className,
}: {
  path?: string | null
  /** 这是谁的路径（可达名用：「显示 <name> 的完整路径」） */
  name: string
  className?: string
}) {
  const [open, setOpen] = useState(false)
  if (!path) return <span className="text-xs text-ink-3">—</span>
  return (
    <span className={cn('flex min-w-0 flex-col gap-0.5', className)}>
      <span className="flex min-w-0 items-center gap-1">
        <button
          type="button"
          aria-expanded={open}
          aria-label={st('project.showFullPath', { name })}
          onClick={() => setOpen((v) => !v)}
          className={cn(
            'flex h-6 min-w-0 items-center gap-1 rounded-sm px-1 text-xs text-ink-2',
            'outline-none hover:bg-ink/[.045] hover:text-ink focus-visible:focus-ring',
          )}
        >
          <svg
            width="9"
            height="9"
            viewBox="0 0 11 11"
            aria-hidden
            className={cn('shrink-0 text-ink-3 transition-transform', open && 'rotate-90')}
          >
            <path d="M4 2.5 L7.5 5.5 L4 8.5" fill="none" stroke="currentColor" strokeWidth="1.4" />
          </svg>
          <span className="min-w-0 truncate font-mono">{dirTail(path)}</span>
        </button>
        <CopyButton text={path} label={st('project.copyPath', { name })} />
      </span>
      {open && (
        <span className="break-all pl-1 font-mono text-[11px] leading-snug text-ink-3">{path}</span>
      )}
    </span>
  )
}
