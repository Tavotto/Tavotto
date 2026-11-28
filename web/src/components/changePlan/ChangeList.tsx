import { Fragment, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { PlanNote } from '@/store/changePlanStore'
import { cn } from '@/lib/utils'
import { ReviewMark } from '../review/ReviewMark'
import { Button, IconButton } from '../ui/Button'
import { Checkbox } from '../ui/Checkbox'
import { Check, Pencil, X } from '../ui/icons'
import { ICON_SIZE } from '../ui/Icon'
import { cp, whoOf, type GroupView, type NoteResult, type UnhandledView } from './changePlanModel'
import { TodoMark } from './TodoMark'

/**
 * 修改方案弹窗右侧的改动清单：按意见分组，每组一个勾选（一组 = 一条意见的全部改动，`atomic` 组本来就只能整组要或不要）；
 * 没改的意见是一行灰短横 + 一句原因，不假装处理了。选中 / 悬停浮成卡片（selected 底），不画左侧竖线；与左侧舞台的细框联动（`activeId`）。
 */
export function ChangeList({
  heading,
  muted = false,
  groups,
  unhandled,
  unchecked,
  onToggle,
  activeId,
  onActive,
  onAction,
  noteResults,
}: {
  /** 清单头的整句（一键排版的卡用它说「排版 · N 张图」）；缺省 = 「改动 · N 处」 */
  heading?: string
  /** 清单头淡一级（矢量编辑方案重算失败期间：它说的还是上次算出的旧摘要） */
  muted?: boolean
  groups: GroupView[]
  unhandled: UnhandledView[]
  unchecked: Record<string, boolean>
  onToggle: (key: string) => void
  activeId: string | null
  onActive: (key: string | null) => void
  /** 没处理的行带的动作被点了（矢量编辑方案：「按我说的加」） */
  onAction?: (u: UnhandledView) => void
  /**
   * 这一版是按批注重改出来的：每条批注的结果（`noteResults`），按批注顺序列在「这版按批注改的」那块——改成了的就是它那一组的行
   * （照旧能勾选），没改成的一行灰短横 + 一句原因（不再在底部没处理列表里重复）
   */
  noteResults?: NoteResult[]
}) {
  useTranslation('ai')
  const total = groups.filter((g) => !unchecked[g.key]).reduce((a, g) => a + g.boxes.length, 0)
  // 「顺带留出边距」整份方案只说一次（后端把同一次留边距并进了每个造成出界的组）：挂在清单里第一个勾着的这类组上
  const ordered = [...groups.filter((g) => g.fromNote), ...groups.filter((g) => !g.fromNote)]
  const roomKey = (ordered.find((g) => g.room && !unchecked[g.key]) ?? ordered.find((g) => g.room))?.key
  const row = (g: GroupView) => <GroupRow key={g.key} g={g} room={g.key === roomKey} checked={!unchecked[g.key]} active={g.key === activeId} onToggle={onToggle} onActive={onActive} />
  const byKey = new Map(groups.map((g) => [g.key, g]))
  // 只有没改成的批注在顶部那块写了原因：底部不再重复；改成了的（有组）若另有一条没处理的原因，仍照常列在底部
  const resultIds = new Set(noteResults?.filter((r) => !r.done).map((r) => r.id))
  const resultKeys = new Set(noteResults?.flatMap((r) => (r.groupKey ? [r.groupKey] : [])))
  // 逐条结果之外还有按批注改的组（对不上的老条目）：照旧列在这块的末尾
  const restNoteGroups = groups.filter((g) => g.fromNote && !resultKeys.has(g.key))
  const rest = unhandled.filter((u) => !resultIds.has(u.id))
  return (
    <div className="flex min-h-0 flex-1 flex-col" data-change-plan-list>
      <p className={cn('shrink-0 px-4 pb-1.5 pt-4 type-meta transition-opacity duration-fast', muted && 'opacity-50')}>{heading ?? cp('dialog.changes', { count: total })}</p>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-3">
        {/* 续改的版本：「按批注改的」置顶，用一条轻分隔线与按意见改的隔开；按批注重改出的版本逐条标结果（改成了 = 它那一组，没改成 = 一句原因） */}
        {(noteResults?.length || restNoteGroups.length > 0) && (
          <ul className="mb-2 flex flex-col gap-0.5 border-b border-border pb-2" data-change-plan-note-groups>
            {noteResults?.length ? (
              <li className="type-meta pb-0.5" data-change-plan-note-tally>
                {cp('dialog.noteResults', { done: noteResults.filter((r) => r.done).length, count: noteResults.length })}
              </li>
            ) : null}
            {noteResults?.map((r) => {
              const g = r.groupKey ? byKey.get(r.groupKey) : undefined
              if (g) return <Fragment key={r.id}>{row(g)}</Fragment>
              return (
                <li key={r.id} data-change-plan-note-result={r.id} data-note-done={r.done || undefined} title={r.text} className="-mx-2 flex items-start gap-2.5 rounded-lg px-2 py-2">
                  {r.done ? (
                    <TodoMark state="done" className="mt-0.5" />
                  ) : (
                    <span aria-hidden className="flex h-5 w-[15px] shrink-0 items-center justify-center">
                      <span className="h-px w-2 bg-ink-faint" />
                    </span>
                  )}
                  <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                    <span className="truncate text-sm text-ink-3">{r.text}</span>
                    {r.reason && <span className="type-meta">{r.reason}</span>}
                  </div>
                </li>
              )
            })}
            {restNoteGroups.map(row)}
          </ul>
        )}
        <ul className="flex flex-col gap-0.5">
          {groups.filter((g) => !g.fromNote).map(row)}
        </ul>
        {rest.length > 0 && (
          <ul className="mt-2 flex flex-col gap-0.5 border-t border-border pt-2">
            {rest.map((u) => (
              <li key={u.id} data-change-plan-unhandled={u.id} title={u.reason || u.text} className="-mx-2 flex items-start gap-2.5 rounded-lg px-2 py-2">
                <span aria-hidden className="flex h-5 w-[15px] shrink-0 items-center justify-center">
                  <span className="h-px w-2 bg-ink-faint" />
                </span>
                <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <span className={u.action ? 'text-sm text-ink-3' : 'truncate text-sm text-ink-3'}>{u.text}</span>
                  {(u.reviewer || u.reason) && <span className="type-meta">{[u.reviewer, u.reason].filter(Boolean).join(' · ')}</span>}
                  {u.action && onAction && (
                    <Button variant="secondary" size="sm" className="mt-1 w-fit rounded-full" onClick={() => onAction(u)} data-change-plan-confirm-item>
                      {cp('vector.confirmButton')}
                    </Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

/** 图内改动清单每组最多直接列几行，其余折成「还有 N 处」 */
const MAX_LINES = 4

/** 一个值：颜色带小色块（`swatchOnly`：只画色块，色值放悬停说明里） */
function Val({ v, swatchOnly = false }: { v: { text: string; color?: string }; swatchOnly?: boolean }) {
  const only = swatchOnly && !!v.color
  return (
    <span className="inline-flex items-center gap-1 text-ink-2">
      {v.color && <span aria-hidden className="size-2.5 rounded-sm inset-ring inset-ring-ink/25" style={{ backgroundColor: v.color }} />}
      {only ? <span className="sr-only">{v.text}</span> : v.text}
    </span>
  )
}

function GroupRow({
  g,
  room,
  checked,
  active,
  onToggle,
  onActive,
}: {
  g: GroupView
  /** 这一行说「顺带留出边距」那一句（整份方案只说一次） */
  room: boolean
  checked: boolean
  active: boolean
  onToggle: (key: string) => void
  onActive: (key: string | null) => void
}) {
  const who = whoOf(g.reviewers)
  const places = g.boxes.length
  const [open, setOpen] = useState(false)
  const shown = open ? g.lines : g.lines?.slice(0, MAX_LINES)
  const hidden = (g.lines?.length ?? 0) - (shown?.length ?? 0)
  // 第二行灰字：谁 · N 处改动（逐行列出了改动、又没有审阅者时不再重复说 N 处）+ 留边距那一句
  const count = g.fromNote ? (g.lines?.length ? cp('dialog.groupMetaNoteBare') : cp('dialog.groupMetaNote', { count: places })) : who ? cp('dialog.groupMeta', { who, count: places }) : g.lines?.length ? '' : cp('dialog.groupMetaNoWho', { count: places })
  const meta = [count, room ? cp('dialog.roomNote') : ''].filter(Boolean).join(' · ')
  return (
    <li>
      <label
        title={g.fixed ? g.detail : cp('dialog.checkTip')}
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
        {g.fixed ? (
          <span aria-hidden className="mt-0.5 flex size-[15px] shrink-0 items-center justify-center text-ink-3">
            <Check size={ICON_SIZE.xs} />
          </span>
        ) : (
          <Checkbox checked={checked} onChange={() => onToggle(g.key)} aria-label={g.summary} className={cn('mt-0.5 size-[15px] [&>input:checked]:border-ink-2 [&>input:checked]:bg-ink-2', g.note && 'opacity-60')} />
        )}
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className={cn('text-sm', checked ? 'text-ink' : 'text-ink-3')}>{g.summary}</span>
          {shown?.map((l, i) => (
            <span key={i} data-change-line title={l.title} className={cn('type-meta flex flex-wrap items-center gap-x-1.5', !checked && 'opacity-50')}>
              <span>{l.label}</span>
              {l.after && (
                <>
                  {l.before && <Val v={l.before} swatchOnly={l.compact} />}
                  {l.before && <span aria-hidden>→</span>}
                  <Val v={l.after} swatchOnly={l.compact} />
                </>
              )}
              {l.extra && <span className="text-ink-3">{l.extra}</span>}
            </span>
          ))}
          {hidden > 0 && (
            <button
              type="button"
              data-change-more
              onClick={(e) => {
                e.preventDefault()
                setOpen(true)
              }}
              className="type-meta w-fit text-left text-ink-3 outline-none hover:text-ink-2 focus-visible:focus-ring"
            >
              {cp('dialog.moreLines', { count: hidden })}
            </button>
          )}
          {g.note && (
            <span data-change-note className="type-meta opacity-70">
              {g.note}
            </span>
          )}
          {g.metaText !== '' && meta && (
            <span className="type-meta" data-change-plan-group-meta>
              {meta}
            </span>
          )}
        </span>
      </label>
    </li>
  )
}

/**
 * 「批注」模式下右栏的批注列表：「批注 · N」，每条是编号、写在哪一处、内容；点一条回到图上那一处的批注框（改），行尾 × 删除。
 * 一条都没有时只剩居中的一句灰字；底部一个「整体批注」（不指哪一处的话）。
 */
export function NoteList({
  notes,
  onOpenNote,
  onRemove,
  onOverall,
  error,
}: {
  notes: PlanNote[]
  onOpenNote: (n: PlanNote) => void
  onRemove: (id: string) => void
  onOverall: () => void
  /** 上一次重改没成的那一句灰字 */
  error?: string
}) {
  useTranslation('ai')
  return (
    <div className="flex min-h-0 flex-1 flex-col" data-change-plan-notes>
      {notes.length === 0 ? (
        <p className="flex flex-1 items-center justify-center px-8 text-center type-caption">{cp('dialog.annotateHint')}</p>
      ) : (
        <>
          <p className="shrink-0 px-4 pb-1.5 pt-4 type-meta">{cp('dialog.notes', { count: notes.length })}</p>
          <ul className="flex min-h-0 flex-1 flex-col overflow-y-auto px-4 pb-3">
            {notes.map((n, i) => (
              <li key={n.id} className="group relative border-b border-border last:border-b-0">
                <button
                  type="button"
                  data-change-plan-note={n.id}
                  title={cp('dialog.noteEdit')}
                  onClick={() => onOpenNote(n)}
                  className="-mx-2 flex w-[calc(100%+1rem)] items-start gap-2.5 rounded-lg px-2 py-2.5 pr-9 text-left outline-none transition-colors duration-fast hover:bg-surface-hover focus-visible:focus-ring"
                >
                  <ReviewMark n={i + 1} overall={n.anchor.kind === 'overall'} solid className="mt-0.5" />
                  <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                    <span className="type-meta truncate">{n.label}</span>
                    <span className="break-words text-sm text-ink">{n.text}</span>
                  </span>
                </button>
                <IconButton
                  label={cp('dialog.noteRemove')}
                  iconSize="xs"
                  side="left"
                  className="absolute right-0 top-2.5 text-ink-3 opacity-0 transition-opacity duration-fast hover:text-ink focus-visible:opacity-100 group-hover:opacity-100"
                  onClick={() => onRemove(n.id)}
                >
                  <X size={ICON_SIZE.xs} />
                </IconButton>
              </li>
            ))}
          </ul>
        </>
      )}
      {error && (
        <p className="shrink-0 px-4 pb-2 type-meta" data-change-plan-revise-error>
          {error}
        </p>
      )}
      <div className="shrink-0 border-t border-border px-4 py-2">
        <Button variant="ghost" size="sm" className="-ml-2.5" onClick={onOverall} data-change-plan-note-overall>
          <Pencil size={ICON_SIZE.sm} aria-hidden />
          {cp('dialog.noteOverall')}
        </Button>
      </div>
    </div>
  )
}

/**
 * 按批注重改进行中的右栏：这次发出的批注一条一行（编号与舞台上钉的标记一致 · 写在哪一处 · 原话），行尾是待办卡那套「没开始」的点状圈。
 * 后端只报整体阶段、没有逐条进度：清单头说「正在按这 N 条批注改」，不逐条打勾、不逐条转圈；新版出来后结果在对比视图右栏顶部逐条标。
 */
export function RevisingNoteList({ notes }: { notes: PlanNote[] }) {
  useTranslation('ai')
  return (
    <div className="flex min-h-0 flex-1 flex-col" data-change-plan-revising-notes>
      <p className="shrink-0 px-4 pb-1.5 pt-4 type-meta">{cp('dialog.revisingNotes', { count: notes.length })}</p>
      <ul className="flex min-h-0 flex-1 flex-col overflow-y-auto px-4 pb-3">
        {notes.map((n, i) => (
          <li key={n.id} data-change-plan-revising-note={n.id} className="-mx-2 flex items-start gap-2.5 border-b border-border px-2 py-2.5 last:border-b-0">
            <ReviewMark n={i + 1} overall={n.anchor.kind === 'overall'} solid className="mt-0.5" />
            <span className="flex min-w-0 flex-1 flex-col gap-0.5">
              <span className="type-meta truncate">{n.label}</span>
              <span className="break-words text-sm text-ink-2">{n.text}</span>
            </span>
            <TodoMark state="pending" className="mt-0.5" />
          </li>
        ))}
      </ul>
    </div>
  )
}
