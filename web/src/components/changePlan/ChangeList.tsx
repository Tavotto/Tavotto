import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { Checkbox } from '../ui/Checkbox'
import { cp, whoOf, type GroupView, type UnhandledView } from './changePlanModel'

/**
 * 修改方案弹窗右侧的改动清单：按意见分组，每组一个勾选（一组 = 一条意见的全部改动，`atomic` 组本来就只能整组要或不要）；
 * 没改的意见是一行灰短横 + 一句原因，不假装处理了。选中 / 悬停浮成卡片（selected 底），不画左侧竖线；与左侧舞台的细框联动（`activeId`）。
 */
export function ChangeList({
  groups,
  unhandled,
  unchecked,
  onToggle,
  activeId,
  onActive,
}: {
  groups: GroupView[]
  unhandled: UnhandledView[]
  unchecked: Record<string, boolean>
  onToggle: (key: string) => void
  activeId: string | null
  onActive: (key: string | null) => void
}) {
  useTranslation('ai')
  const total = groups.filter((g) => !unchecked[g.key]).reduce((a, g) => a + g.boxes.length, 0)
  return (
    <div className="flex min-h-0 flex-1 flex-col" data-change-plan-list>
      <p className="shrink-0 px-4 pb-1.5 pt-4 type-meta">{cp('dialog.changes', { count: total })}</p>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-3">
        <ul className="flex flex-col gap-0.5">
          {groups.map((g) => (
            <GroupRow key={g.key} g={g} checked={!unchecked[g.key]} active={g.key === activeId} onToggle={onToggle} onActive={onActive} />
          ))}
        </ul>
        {unhandled.length > 0 && (
          <ul className="mt-2 flex flex-col gap-0.5 border-t border-border pt-2">
            {unhandled.map((u) => (
              <li key={u.id} data-change-plan-unhandled={u.id} title={u.reason} className="-mx-2 flex items-start gap-2.5 rounded-lg px-2 py-2">
                <span aria-hidden className="flex h-5 w-[15px] shrink-0 items-center justify-center">
                  <span className="h-px w-2 bg-ink-faint" />
                </span>
                <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <span className="truncate text-sm text-ink-3">{u.text}</span>
                  <span className="type-meta">{[u.reviewer, u.reason].filter(Boolean).join(' · ')}</span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

function GroupRow({
  g,
  checked,
  active,
  onToggle,
  onActive,
}: {
  g: GroupView
  checked: boolean
  active: boolean
  onToggle: (key: string) => void
  onActive: (key: string | null) => void
}) {
  const who = whoOf(g.reviewers)
  const places = g.boxes.length
  return (
    <li>
      <label
        title={cp('dialog.checkTip')}
        data-change-group={g.key}
        data-active={active || undefined}
        onPointerEnter={() => onActive(g.key)}
        onPointerLeave={() => onActive(null)}
        onFocus={() => onActive(g.key)}
        onBlur={() => onActive(null)}
        className={cn(
          '-mx-2 flex items-start gap-2.5 rounded-lg px-2 py-2 transition-colors duration-fast',
          active ? 'bg-selected' : 'hover:bg-surface-hover',
        )}
      >
        <Checkbox checked={checked} onChange={() => onToggle(g.key)} aria-label={g.summary} className="mt-0.5 size-[15px] [&>input:checked]:border-ink-2 [&>input:checked]:bg-ink-2" />
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className={cn('text-sm', checked ? 'text-ink' : 'text-ink-3')}>{g.summary}</span>
          <span className="type-meta">{who ? cp('dialog.groupMeta', { who, count: places }) : cp('dialog.groupMetaNoWho', { count: places })}</span>
        </span>
      </label>
    </li>
  )
}
