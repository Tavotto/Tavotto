import { Fragment, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useShallow } from 'zustand/react/shallow'
import { MAX_ADD_TODO, MAX_TODOS, canUndoApplied, declinedGroups, pendingConfirm, scriptBroken, scriptDeclined, todoChecked, todoLocked, todoQueued, todosOf, useChangePlanStore, type PlanEntry } from '@/store/changePlanStore'
import { useDocumentStore } from '@/store/documentStore'
import { listJoin } from '@/i18n/format'
import { formatAxisLimit } from '@/lib/logPower'
import { cn } from '@/lib/utils'
import { Card } from '../ui/Card'
import { Button } from '../ui/Button'
import { Checkbox } from '../ui/Checkbox'
import { Check, ChevronDown } from '../ui/icons'
import { ICON_SIZE } from '../ui/Icon'
import { ThinkingOrb } from '../ui/ThinkingOrb'
import { Plus } from '../ui/icons'
import type { ChangePlanTodo } from '@/lib/api'
import { cp, effectiveStatus, todoFailReason, todoPhase, todoScriptCardShown, todosDone } from './changePlanModel'
import { FigureThumb } from './FigureThumb'
import { TodoMark } from './TodoMark'
import { useImageAspect, usePanelThumbSrc, usePlanPanel, usePlanSource, useSelectionPreview } from './usePlanFigure'

/** 与修改方案卡同一个画框的最宽比例（再宽的图上下留纸，卡不再变矮） */
const MAX_THUMB_ASPECT = 2
/** 缩略图画面最高（px）：清单才是这张卡的主角，图只作参照 */
const THUMB_MAX_H = 128

/**
 * 助手给建议时的「建议」卡（ADR 0214 §三、§四）：缩略图（勾着的图内修改之后的样子）+ 待办清单（一行 = 勾选框 + 做什么，下面一行小灰字说为什么）+
 * 主按钮「改这 N 项」+ 次级「看效果」。点主按钮：勾着的图内待办一次 commit，勾着的脚本待办接着自动出脚本方案、过检查直接应用；
 * 落定之后收成一行「已按建议改了 N 项 · 撤销」，没改的几项写在下面一行灰字里。
 * 图内待办的勾选就是方案的逐组勾选（`unchecked`），缩略图 / 对比弹窗跟着它走；脚本待办的勾选记在 `todoOff`。
 */
/** 换图的淡入时长（ms；与 `duration-[180ms]` 同步）：减弱动效时不淡入，直接换 */
const FADE_MS = 180

/**
 * 缩略图换图时交叉淡入（勾选变了、预览换成另一张）：旧图留在底下，新图加载好后在上面 180ms ease-out 淡入，之后成为底图。
 * 新图一直没加载出来（失败 / 测试环境）也在 1.5 s 后换过去，不卡住。
 */
function FadeSwap({ src }: { src: string | null | undefined }) {
  const [base, setBase] = useState(src)
  const [shown, setShown] = useState(false)
  const timer = useRef<number>(0)
  useEffect(() => {
    if (src === base) return
    if (!src || !base) {
      setBase(src)
      return
    }
    setShown(false)
    timer.current = window.setTimeout(() => setBase(src), 1500)
    return () => window.clearTimeout(timer.current)
  }, [src, base])
  const swapping = !!src && !!base && src !== base
  return (
    <>
      {base && <img src={base} alt="" draggable={false} className="absolute inset-0 size-full rounded-md object-contain" />}
      {swapping && (
        <img
          key={src}
          src={src}
          alt=""
          draggable={false}
          onLoad={() => {
            setShown(true)
            window.clearTimeout(timer.current)
            timer.current = window.setTimeout(() => setBase(src), FADE_MS)
          }}
          className={cn('absolute inset-0 size-full rounded-md object-contain opacity-0 transition-opacity duration-[180ms] ease-out motion-reduce:transition-none', shown && 'opacity-100')}
        />
      )}
    </>
  )
}

/**
 * 一行待办的文字部分：做什么（墨色）+ 细分隔 + 为什么（灰），整行一行、超出省略，全文在悬停里；
 * 新加的行尾一个小圆点（悬停写「新加」）。要改脚本的不再各挂标签，由清单里的分组小标题统一说。
 */
function TodoText({ t, dim, fresh }: { t: ChangePlanTodo; dim?: boolean; fresh?: boolean }) {
  return (
    <span className="flex min-w-0 flex-1 items-center gap-2" title={t.why ? `${t.label} · ${t.why}` : t.label}>
      {/* 太长时右端渐隐，不是一排硬省略号（agy 审 2026-11-27）；全文在悬停的 title 里 */}
      <span className="min-w-0 flex-1 overflow-hidden whitespace-nowrap text-sm [mask-image:linear-gradient(90deg,#000_calc(100%-20px),transparent)]">
        <span className={dim ? 'text-ink-3' : 'text-ink'}>{t.label}</span>
        {t.why && (
          <>
            <span className="mx-1.5 text-ink-faint" aria-hidden>
              ·
            </span>
            <span className="type-meta">{t.why}</span>
          </>
        )}
      </span>
      {fresh && <span className="size-1.5 shrink-0 rounded-full bg-ink-faint" title={cp('todo.added')} aria-label={cp('todo.added')} data-suggestion-new-tag />}
    </span>
  )
}

/** 「想一想」那一行：提交之后是小球 + 用户写的那句话，新一版回来后换成新的几行 */
function AddingLine({ text }: { text?: string }) {
  return (
    <p className="-mx-1.5 flex h-7 items-center gap-2.5 px-1.5 text-sm text-ink-3" role="status" data-suggestion-adding>
      <span className="flex size-[15px] shrink-0 items-center justify-center" aria-hidden>
        <ThinkingOrb form="solving" />
      </span>
      <span className="min-w-0 flex-1 truncate">{text}</span>
    </p>
  )
}

/** 脚底的输入框（顶掉按钮行，自动聚焦；回车提交、Esc 取消、失焦且没写字就收回） */
function AddTodoInput({ onSubmit, onClose }: { onSubmit: (text: string) => void; onClose: () => void }) {
  const [draft, setDraft] = useState('')
  return (
    <div className="flex min-h-7 flex-1 items-center gap-2">
      <span className="flex size-[15px] shrink-0 items-center justify-center text-ink-3" aria-hidden>
        <Plus size={ICON_SIZE.sm} />
      </span>
      <input
        autoFocus
        value={draft}
        maxLength={MAX_ADD_TODO}
        placeholder={cp('todo.addPlaceholder')}
        aria-label={cp('todo.add')}
        data-suggestion-add-input
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.nativeEvent.isComposing) return // 输入法选字的回车不算提交
          if (e.key === 'Escape') {
            e.stopPropagation()
            onClose()
          } else if (e.key === 'Enter' && draft.trim()) {
            e.preventDefault()
            onSubmit(draft.trim())
            onClose()
          }
        }}
        onBlur={() => !draft.trim() && onClose()}
        className="h-7 min-w-0 flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-ink-3"
      />
    </div>
  )
}

export function SuggestionCard({ entry, latest = true }: { entry: PlanEntry; latest?: boolean }) {
  useTranslation('ai')
  const plan = entry.plan
  const panel = usePlanPanel(entry)
  const status = effectiveStatus(entry, panel?.overrides ?? null, usePlanSource(panel))
  useDocumentStore((s) => s.past.at(-1))
  useDocumentStore((s) => s.future.at(-1))
  // 已经开始做的脚本待办的方案卡（按开始顺序）；`useShallow`：每次取出的是新数组，按元素比较
  const found = useChangePlanStore(useShallow((s) => (entry.todoRun?.scripts ?? []).map((x) => s.entries[x.key])))
  const scriptRuns = (entry.todoRun?.scripts ?? []).flatMap((x, i) => (found[i] ? [{ todo: x.todo, entry: found[i] }] : []))
  const scriptEntries = scriptRuns.map((x) => x.entry)
  const scriptEntry = scriptEntries.at(-1)
  const locked = useChangePlanStore((s) => todoLocked(entry, s.entries))
  const store = useChangePlanStore.getState
  const [listOpen, setListOpen] = useState(false)
  const [addOpen, setAddOpen] = useState(false)
  const thumb = usePanelThumbSrc(panel, !entry.beforeSrc)
  const beforeSrc = entry.beforeSrc ?? thumb
  const todos = todosOf(entry)
  const groups = plan?.groups ?? []
  const keysOn = groups.filter((g) => !entry.unchecked[g.key]).length
  const partial = groups.length > 0 && keysOn < groups.length
  const asked = status === 'ready' ? pendingConfirm(entry) : undefined
  // 脚本方案改了画出来的数据：先问一句，问句与两个按钮就在这张卡的脚底（回答记在那张脚本方案上）
  const dataAsk = scriptEntry?.status === 'ready' ? pendingConfirm(scriptEntry) : undefined
  const selection = useSelectionPreview(entry, status === 'ready' && (partial || declinedGroups(entry).length > 0))
  // 勾着的图内修改之后的样子：全勾 = 方案的全量图；勾了一部分 = 按勾选现拼；一组都没勾 = 改前
  const shown = groups.length === 0 || keysOn === 0 ? beforeSrc : partial ? (selection.url ?? entry.afterSrc) : entry.afterSrc
  const aspect = useImageAspect(entry.afterSrc ?? beforeSrc)

  const figureApplied = status === 'applied'
  const phase = todoPhase(entry, scriptEntries, figureApplied)
  const done = todosDone(entry, scriptRuns, figureApplied)
  const on = todos.filter((t) => todoChecked(entry, t))
  const count = on.length
  const stale = status === 'stale'
  const busy = phase === 'running' || phase === 'asking'
  const frozen = locked || busy || stale

  if (!plan || status === 'discarded') return null

  // 脚本待办没做成的：原因并进这张卡（一条一句），不再在下面另出一张失败卡要用户收起；越界那张带「恢复」的卡仍单独出现
  const failNotes = scriptRuns.flatMap((x) => {
    if (!scriptBroken(x.entry) || scriptDeclined(x.entry) || todoScriptCardShown(x.entry)) return []
    const t = todos.find((y) => y.key === x.todo)
    return t ? [{ key: t.key, label: t.label, reason: todoFailReason(x.entry), full: typeof x.entry.failure?.params?.agent_reply === 'string' ? x.entry.failure.params.agent_reply : undefined }] : []
  })
  const failLines = (cls: string) =>
    failNotes.map((n) => (
      <p key={n.key} className={cls} title={n.full} data-suggestion-fail={n.key}>
        {cp('todo.failed', { item: n.label, reason: n.reason })}
      </p>
    ))

  // 落定：收成一行（点它展开已改的几项，只读）；没改的几项一行灰字，与第一行的字对齐（不可点——再改请重新说一句）
  if (phase === 'done') {
    // 没做成的那几条在下面各有一句原因，这里不再重复列
    const undone = todos.filter((t) => !done.includes(t.key) && !failNotes.some((n) => n.key === t.key)).map((t) => t.label)
    const landed = todos.filter((t) => done.includes(t.key))
    const canUndo = (figureApplied && !!panel && canUndoApplied(entry, panel.overrides)) || scriptEntries.some((x) => x.status === 'applied')
    return (
      <Card appearance="subtle" padding="sm" data-change-plan-card="suggestion-done" data-change-plan-suggestion>
        <div className="flex items-start gap-1.5 px-1.5 py-0.5">
          <div className="flex min-w-0 flex-1 flex-col">
            <button
              type="button"
              aria-expanded={listOpen}
              onClick={() => setListOpen((v) => !v)}
              className="group flex min-h-7 w-full items-center gap-1.5 rounded-sm text-left outline-none focus-visible:focus-ring"
              data-suggestion-done-toggle
            >
              <Check size={ICON_SIZE.sm} className="shrink-0 text-ok" />
              <span className="min-w-0 text-sm text-ink-2" data-suggestion-done>
                {cp('todo.done', { count: done.length })}
              </span>
              <ChevronDown size={ICON_SIZE.xs} aria-hidden className={cn('shrink-0 text-ink-3 transition-transform group-hover:text-ink-2 motion-reduce:transition-none', !listOpen && '-rotate-90')} />
            </button>
            {listOpen && (
              <ul className="flex flex-col gap-0.5 pb-1 pl-[22px]" data-suggestion-landed>
                {landed.map((t) => (
                  <li key={t.key} className="flex h-7 items-center gap-2.5" data-suggestion-landed-row={t.key}>
                    <Check size={ICON_SIZE.sm} className="shrink-0 text-ok" />
                    <TodoText t={t} />
                  </li>
                ))}
              </ul>
            )}
            {undone.length > 0 && (
              <p className="pb-0.5 pl-[22px] text-xs text-ink-3" data-suggestion-skipped>
                {cp('todo.skipped', { items: listJoin(undone) })}
              </p>
            )}
            {failLines('pb-0.5 pl-[22px] text-xs text-ink-3 text-pretty')}
            {entry.notice && (
              <p className="pb-0.5 pl-[22px] text-xs text-ink-3" data-change-plan-notice={entry.notice}>
                {cp(entry.notice === 'apply_failed' ? 'card.applyFailed' : 'card.undoFailed')}
              </p>
            )}
          </div>
          {canUndo && !scriptEntries.some((x) => x.scriptBusy) && (
            <Button variant="ghost" className="shrink-0 text-ink-3 hover:text-ink" onClick={() => void store().undoTodos(entry.key)} data-change-plan-undo>
              {cp('card.undo')}
            </Button>
          )}
        </div>
      </Card>
    )
  }

  // 一行待办此刻的进度（只在执行中画）：图内待办 = 已一次 commit 应用；脚本待办 = 脚本方案在生成 / 应用（转圈）、等回答（点状圈）、应用了（勾）、
  // 还排着没开始 = `queued`（仍是勾选框，可以勾掉跳过）；做过但没成的 = null（灰着的空框，算没改）
  const queuedKeys = todoQueued(entry)
  const rowState = (t: ChangePlanTodo): 'done' | 'active' | 'pending' | 'queued' | null => {
    if (!busy || !todoChecked(entry, t)) return null
    if (t.kind === 'figure') return figureApplied ? 'done' : 'active'
    const mine = scriptRuns.find((x) => x.todo === t.key)?.entry
    if (!mine) {
      if (queuedKeys.includes(t.key)) return 'queued'
      // 开始过、但那张脚本卡已被收起（条目不在了）= 没改；只有还在发出去的路上的那一条才转圈
      return entry.todoRun?.scripts.some((x) => x.todo === t.key) ? null : 'active'
    }
    if (mine.status === 'applied') return 'done'
    if (scriptBroken(mine) || scriptDeclined(mine)) return null
    return pendingConfirm(mine) ? 'pending' : 'active'
  }
  // 排着没开始的脚本待办执行中也能勾掉 / 勾回来；其余的一律锁着
  const rowLocked = (t: ChangePlanTodo) => (busy && rowState(t) === 'queued' ? false : frozen)
  const adding = entry.adding !== undefined
  const ordered = [...todos.filter((t) => t.kind !== 'script'), ...todos.filter((t) => t.kind === 'script')]
  const canAdd = !frozen && !adding && status === 'ready' && !entry.todoRun && todos.length < MAX_TODOS
  const doneNow = on.filter((t) => rowState(t) === 'done').length

  return (
    <Card appearance="raised" padding="sm" className="bg-field" data-change-plan-card="suggestion" data-change-plan-suggestion>
      <div className="flex min-h-7 items-center justify-between gap-2 pb-1.5 pl-1.5 pt-0.5">
        <span className="flex items-baseline gap-2">
          <span className="text-base font-medium text-ink">{cp('todo.title')}</span>
          <span className="type-meta tabular-nums" data-suggestion-count>
            {cp('todo.count', { count: todos.length })}
          </span>
        </span>
        {groups.length > 0 && !stale && (
          <Button variant="ghost" size="sm" className="text-xs text-ink-3 hover:text-ink" onClick={() => store().openDialog(entry.key)} data-change-plan-view>
            {cp('todo.compare')}
          </Button>
        )}
      </div>
      <div className="mx-auto w-full" style={{ maxWidth: `${Math.round(THUMB_MAX_H * Math.min(aspect, MAX_THUMB_ASPECT)) + 12}px` }}>
        <FigureThumb src={null} alt={cp('card.thumbAlt')} aspect={Math.min(aspect, MAX_THUMB_ASPECT)}>
          <span className={cn('absolute inset-0', stale && 'opacity-50')} data-suggestion-thumb={shown ?? undefined}>
            <FadeSwap src={shown} />
          </span>
        </FigureThumb>
      </div>
      <div className="flex flex-col gap-1.5 px-1.5 pb-1 pt-2" data-change-plan-body>
        <ul className="flex flex-col" aria-label={cp('todo.listLabel')} data-suggestion-list>
          {ordered.map((t, i) => {
            const checked = todoChecked(entry, t)
            const st = rowState(t)
            return (
              <Fragment key={t.key}>
                {t.kind === 'script' && ordered[i - 1]?.kind !== 'script' && (
                  <li className="type-meta mt-1.5 flex h-6 items-end border-t border-border pb-0.5" title={cp('todo.scriptTip')} data-suggestion-script-section>
                    {cp('todo.scriptTag')}
                  </li>
                )}
                <li>
                  <label
                    data-suggestion-todo={t.key}
                    data-todo-state={st ?? undefined}
                    className={cn('-mx-1.5 flex h-7 items-center gap-2.5 rounded-md px-1.5 transition-colors duration-fast', rowLocked(t) ? '' : 'hover:bg-surface-hover')}
                  >
                    {st && st !== 'queued' ? (
                      <TodoMark state={st} />
                    ) : (
                      <Checkbox checked={checked} disabled={rowLocked(t)} onChange={() => store().toggleTodo(entry.key, t.key)} aria-label={t.label} className="size-[15px] [&>input:checked]:border-ink-2 [&>input:checked]:bg-ink-2" />
                    )}
                    <TodoText t={t} dim={!checked} fresh={!!t.added && !entry.todoRun} />
                  </label>
                </li>
              </Fragment>
            )
          })}
        </ul>
        {adding && <AddingLine text={entry.addingText} />}
        {!busy && failLines('text-xs text-ink-3 text-pretty')}
        {entry.addError && (
          <p className="text-xs text-ink-3" data-suggestion-add-error>
            {cp('todo.addFailed')}
          </p>
        )}
        {entry.notice && (
          <p className="text-xs text-ink-3" data-change-plan-notice={entry.notice}>
            {cp(entry.notice === 'apply_failed' ? 'card.applyFailed' : 'card.undoFailed')}
          </p>
        )}
        {/* 底部一行与按钮同高：执行中换成一句话，卡片底边不跳 */}
        <div className="flex min-h-7 items-center gap-2 pt-0.5" data-suggestion-actions>
          {stale ? (
            <>
              <span className="text-sm text-ink-3">{cp('card.stale')}</span>
              <Button variant="secondary" size="md" className="rounded-full" onClick={() => void store().regenerate(entry.key)} data-change-plan-regenerate>
                {cp('card.regenerate')}
              </Button>
            </>
          ) : dataAsk && scriptEntry ? (
            <>
              <span className="min-w-0 flex-1 text-sm text-ink-2" data-change-plan-confirm-data-ask>
                {cp('card.dataChangedAsk')}
              </span>
              <Button variant="primary" size="md" className="rounded-full" onClick={() => store().answerConfirm(scriptEntry.key, dataAsk.key, 'yes')} data-change-plan-confirm-data="yes">
                {cp('card.dataChangedYes')}
              </Button>
              <Button variant="secondary" size="md" className="rounded-full" onClick={() => store().answerConfirm(scriptEntry.key, dataAsk.key, 'no')} data-change-plan-confirm-data="no">
                {cp('card.dataChangedNo')}
              </Button>
            </>
          ) : busy ? (
            <p className="flex items-center gap-2 text-sm text-ink-2" role="status" data-suggestion-running={phase}>
              <ThinkingOrb form="solving" />
              <span>{cp('todo.runningN', { done: doneNow, total: on.length })}</span>
            </p>
          ) : asked?.confirm?.kind === 'log_lower_limit' ? (
            <>
              <span className="min-w-0 flex-1 text-sm text-ink-2">{cp('card.logLimitAsk', { axis: cp(asked.confirm.axis === 'x' ? 'card.axisX' : 'card.axisY'), to: formatAxisLimit(asked.confirm.to, true) })}</span>
              <Button variant="primary" size="md" className="rounded-full" onClick={() => store().answerConfirm(entry.key, asked.key, 'yes')} data-change-plan-confirm-log="yes">
                {cp('card.logLimitYes')}
              </Button>
              <Button variant="secondary" size="md" className="rounded-full" onClick={() => store().answerConfirm(entry.key, asked.key, 'no')} data-change-plan-confirm-log="no">
                {cp('card.logLimitNo')}
              </Button>
            </>
          ) : addOpen && canAdd ? (
            <AddTodoInput onSubmit={(text) => void store().addTodo(entry.key, text)} onClose={() => setAddOpen(false)} />
          ) : (
            <>
              {canAdd && (
                <Button variant="ghost" size="icon-sm" className="-ml-1.5 text-ink-3 hover:text-ink" title={cp('todo.add')} aria-label={cp('todo.add')} onClick={() => setAddOpen(true)} data-suggestion-add>
                  <Plus size={ICON_SIZE.sm} />
                </Button>
              )}
              <Button variant={latest ? 'primary' : 'secondary'} size="md" className="rounded-full" disabled={count === 0} onClick={() => void store().applyTodos(entry.key)} data-suggestion-apply>
                {count === 0 ? cp('todo.none') : cp('todo.apply', { count })}
              </Button>
            </>
          )}
        </div>
      </div>
    </Card>
  )
}
