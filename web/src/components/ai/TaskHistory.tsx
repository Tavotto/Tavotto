import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Info, Pin, RotateCcw, RotateCcwClock, Trash2, X } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { msg, t as translate } from '@/i18n'
import { formatTime } from '@/i18n/format'
import {
  agentById,
  agentDisplayName,
  aiRevert,
  backendErrorMsg,
  backendErrorText,
  deleteAiHistory,
  fetchAiHistory,
  pinAiHistory,
  type AiHistoryEntry,
} from '@/lib/api'
import { parseRenames } from '@/lib/overrideIdentity'
import { currentProjectId } from '@/lib/session'
import { cn } from '@/lib/utils'
import { rebindRenamedIdentities } from '@/store/aiRenameRebind'
import { useAiStore } from '@/store/aiStore'
import { useUiStore } from '@/store/uiStore'
import { Button, IconButton } from '../ui/Button'
import { EmptyState } from '../ui/EmptyState'
import { SearchInput } from '../ui/SearchInput'
import { Select } from '../ui/Select'
import { statusLabel } from './transcriptModel'
import { AgentBadge, HistoryDetailsCard, HoverPopover } from './historyRowCards'
import { TraceDialogHost } from './TraceDetails'
import { useTraceDialogStore } from '@/store/traceDialogStore'
import { Popover } from '../ui/Popover'

const ai = (key: string, values?: Record<string, unknown>) => translate(key, { ns: 'ai', ...(values ?? {}) })

/** 「全部状态」的哨兵：Radix Select 不接受空串作为 Item 的值 */
const ALL_STATUSES = '__all__'

/** 历史筛选下拉里的状态集合（会话状态 + 只在历史里出现的 interrupted） */
const HISTORY_STATUSES = ['running', 'done', 'failed', 'timeout', 'cancelled', 'reverted', 'interrupted']

const PAGE = 20

/**
 * 面板顶部的「任务历史」钮 + 它的弹层 + 执行记录卡宿主。宿主挂在弹层**外面**：从某一行点「查看执行记录」时，
 * 弹层（连同悬浮详情卡 / 模型卡）先收起、行卸载，宽卡由这里的宿主画——不会被弹层压住，也不随那一行消失。
 */
export function TaskHistoryPopover() {
  const [open, setOpen] = useState(false)
  return (
    <>
      <Popover
        width={320}
        align="end"
        open={open}
        onOpenChange={setOpen}
        trigger={
          <IconButton
            data-ai-history-trigger
            label={ai('panel.taskHistory')}
            tip={!open}
            iconSize="sm"
            className="text-ink-2"
          >
            <RotateCcwClock size={ICON_SIZE.sm} />
          </IconButton>
        }
      >
        <TaskHistory onClose={() => setOpen(false)} />
      </Popover>
      <TraceDialogHost />
    </>
  )
}

/**
 * 任务历史：项目级持久化记录（SQLite），刷新与后端重启后仍在。
 * 一行 = 提示词 + 一行元信息（Agent 标 · 状态点 · 范围 · 时间）；脚本 / 快照 / 失败原因 / 执行记录在元信息行的悬浮（或点击）详情卡里。
 *
 * 2026-10-07 设计审计 §6.6：从「盖住整个面板的一层」改成面板顶部那颗钮下拉出来的 320px 弹层
 * （最近 20 条 + 搜索 + 状态筛选 + 翻页），对话流留在原处不被遮住。弹层每次打开都重新挂载、重新查。
 */
export function TaskHistory({ onClose }: { onClose: () => void }) {
  useTranslation('ai')
  // 历史按项目存：这个视图说的是**打开它那一刻**的项目，查询与每一条的动作都钉在它上面（#589）
  // （ref 而不是 state：它一经定下就不变，也不该成为下面那个查询 effect 的依赖）
  const pjRef = useRef(currentProjectId())
  const pj = pjRef.current
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('')
  const [entries, setEntries] = useState<AiHistoryEntry[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [error, setError] = useState<string | null>(null)
  // 第一次查回来之前什么都不判断：还不知道有没有记录时既不该摆筛选，
  // 也不该先闪一句「还没有改图任务」
  const [loaded, setLoaded] = useState(false)
  const filtering = !!query || !!status
  // 一条记录都没有时不渲染搜索与筛选（审计 T37）——搜一个空库、按状态筛
  // 一个空库，两个动作都不会有任何结果。**筛出零条时它们必须留着**，
  // 否则用户没有办法把筛选条件取消掉。
  const showFilters = loaded && (filtering || total > 0)

  const load = async (q: string, st: string, off: number) => {
    try {
      const res = await fetchAiHistory({ q, status: st, limit: PAGE, offset: off }, pjRef.current)
      if (currentProjectId() !== pjRef.current) return
      setEntries(res.sessions)
      setTotal(res.total)
      setError(null)
    } catch (e) {
      setError(backendErrorText(e))
    } finally {
      setLoaded(true)
    }
  }

  useEffect(() => {
    const t = window.setTimeout(() => void load(query, status, offset), query ? 250 : 0)
    return () => window.clearTimeout(t)
  }, [query, status, offset])

  return (
    <div data-ai-history className="flex max-h-[min(30rem,70vh)] min-h-0 flex-col">
      <div className="flex h-8 shrink-0 items-center gap-2 pl-1.5">
        <h3 className="type-section">{ai('history.title')}</h3>
        <IconButton
          data-ai-history-close
          label={ai('history.close')}
          tip={false}
          iconSize="sm"
          className="ml-auto"
          onClick={onClose}
        >
          <X size={ICON_SIZE.sm} />
        </IconButton>
      </div>
      {showFilters && (
        <div className="flex shrink-0 items-center gap-1.5 px-0.5 pb-1.5">
          <SearchInput
            data-ai-history-search
            value={query}
            onValueChange={(v) => {
              setQuery(v)
              setOffset(0)
            }}
            placeholder={ai('history.searchPlaceholder')}
            aria-label={ai('history.searchAria')}
          />
          <span data-ai-history-filter className="contents">
          <Select
            value={status || ALL_STATUSES}
            onChange={(v) => {
              // Radix 的 Item 不许用空串当值（那是「未选中」的保留态），所以
              // 「全部状态」走一个显式哨兵，只在这一层翻译成后端认的空筛选
              setStatus(v === ALL_STATUSES ? '' : v)
              setOffset(0)
            }}
            options={[
              { value: ALL_STATUSES, label: ai('history.allStatuses') },
              ...HISTORY_STATUSES.map((v) => ({ value: v, label: statusLabel(v) })),
            ]}
            ariaLabel={ai('history.filterAria')}
            className="w-auto shrink-0"
          />
          </span>
        </div>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {error ? (
          <p className="px-1.5 py-2 text-sm text-danger-content">{error}</p>
        ) : !loaded ? null : entries.length === 0 ? (
          /* 空状态只给一句（审计 T37）：怎么开始，输入框自己说 */
          <EmptyState art={filtering ? 'search' : 'history'} title={ai(filtering ? 'history.noMatch' : 'history.empty')} />
        ) : (
          <div className="flex flex-col gap-0.5">
            {entries.map((s) => (
              <HistoryRow key={s.id} entry={s} pj={pj} onChanged={() => void load(query, status, offset)} onDismiss={onClose} />
            ))}
          </div>
        )}
      </div>
      {total > PAGE && (
        <div className="flex shrink-0 items-center justify-between border-t border-border pt-1.5">
          <Button size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
            {ai('history.prev')}
          </Button>
          <span className="type-meta tabular-nums">
            {Math.floor(offset / PAGE) + 1} / {Math.ceil(total / PAGE)}
          </span>
          <Button size="sm" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>
            {ai('history.next')}
          </Button>
        </div>
      )}
    </div>
  )
}

/** 时间按**当前界面语言**格式化（以前钉死 zh-CN，英文界面里会露馅） */
const timeOf = (ts: number) => formatTime(ts)

function HistoryRow({
  entry,
  pj,
  onChanged,
  onDismiss,
}: {
  entry: AiHistoryEntry
  /** 这条记录所属的项目（历史视图打开时认领的那个） */
  pj: string | null
  onChanged: () => void
  /** 收起整个任务历史弹层（打开宽卡之前先让路） */
  onDismiss: () => void
}) {
  useTranslation('ai')
  // 动作回来时已经换了项目：后果（提示、重查）属于那个项目，不写进现在这个
  const stillMine = () => currentProjectId() === pj
  const caps = useAiStore((s) => s.caps)
  const failed = entry.status === 'failed' || entry.status === 'timeout' || entry.status === 'interrupted'
  const running = entry.status === 'running'
  // 状态点 + 一个词：完成的任务说「有改动 / 没有改动」（不再单独说「完成」），其余照状态说
  const tone = failed ? 'danger' : running ? 'accent' : entry.status === 'done' && entry.changed ? 'ok' : 'neutral'
  const word =
    entry.status === 'done' ? ai(entry.changed ? 'history.state.changed' : 'history.state.unchanged') : statusLabel(entry.status)
  // 「整张图」是默认范围，不说；改的是子图 / 元素才写出来
  const scope = entry.scope && entry.scope !== 'figure' ? entry.target || ai(`scope.${entry.scope}`) : null
  // 历史里的 provider 是**当时**用的那个 Agent id：显示名从当前 capabilities 查，查不到就原样显示 id
  const agent = agentById(caps, entry.provider) ?? undefined
  const agentName = agentDisplayName(caps, entry.provider)

  return (
    // 弹层里的一行：每一行同一个版式（提示词 2 行 + 一行元信息），失败也只是红点 + 「失败」，原因在详情卡里；
    // 置顶 / 回滚 / 删除在元信息行的右端，指针靠近或键盘聚焦这一行才出现
    <div
      data-ai-history-row={entry.status}
      className="group/hrow relative rounded-md px-2 py-1.5 transition-colors duration-fast focus-within:bg-surface-hover hover:bg-surface-hover"
    >
      <p className="line-clamp-2 text-sm leading-relaxed text-ink">{entry.prompt}</p>
      <div data-ai-history-meta className="mt-0.5 flex h-5 min-w-0 items-center gap-1.5 pe-20 text-xs text-ink-3">
        <AgentBadge entry={entry} agent={agent} name={agentName} pj={pj} />
        <HoverPopover
          ariaLabel={ai('history.moreInfo')}
          trigger={
            <button
              type="button"
              data-ai-history-details
              className="flex min-w-0 items-center gap-1.5 rounded-sm text-start text-xs text-ink-3 outline-none focus-visible:focus-ring"
            >
              <span
                aria-hidden
                data-ai-history-dot={tone}
                className={cn(
                  'size-1.5 shrink-0 rounded-full',
                  tone === 'ok' && 'bg-ok',
                  tone === 'danger' && 'bg-danger',
                  tone === 'accent' && 'bg-accent',
                  tone === 'neutral' && 'bg-ink-faint',
                )}
              />
              <span data-ai-history-state className={cn('shrink-0', failed ? 'text-danger-content' : 'text-ink-2')}>
                {word}
              </span>
              {scope && (
                <>
                  <span aria-hidden className="text-ink-faint">·</span>
                  <span data-ai-history-scope className="min-w-0 truncate">{scope}</span>
                </>
              )}
              <span aria-hidden className="text-ink-faint">·</span>
              <span className="shrink-0 tabular-nums">{timeOf(entry.started_ms)}</span>
              {entry.pinned && <Pin data-ai-history-pinned aria-hidden size={ICON_SIZE.xs} filled className="shrink-0 text-accent" />}
              <Info
                aria-hidden
                size={ICON_SIZE.xs}
                className="shrink-0 text-ink-faint opacity-0 transition-opacity duration-fast group-focus-within/hrow:opacity-100 group-hover/hrow:opacity-100"
              />
            </button>
          }
        >
          <HistoryDetailsCard
            entry={entry}
            pj={pj}
            onOpenTrace={() => {
              // 宽卡交给常驻宿主画，再收起弹层（连带详情卡 / 模型卡）：浮层的层级比对话框高，留着就压在 Waterfall 上
              useTraceDialogStore.getState().open({ taskRef: entry.id, pj, running, settledKey: entry.status })
              onDismiss()
            }}
          />
        </HoverPopover>
      </div>
      <div
        data-ai-history-actions
        className={cn(
          // 放在元信息那一行的右端（那里本来就空着），不压提示词；不加底板、阴影
          'absolute right-1 bottom-1 flex items-center gap-px',
          'pointer-events-none opacity-0 transition-opacity duration-fast',
          'group-focus-within/hrow:pointer-events-auto group-focus-within/hrow:opacity-100 group-hover/hrow:pointer-events-auto group-hover/hrow:opacity-100',
        )}
      >
        <IconButton
          data-ai-history-pin
          label={ai(entry.pinned ? 'history.unpin' : 'history.pin')}
          tip={ai(entry.pinned ? 'history.unpinTip' : 'history.pinTip')}
          iconSize="xs"
          active={entry.pinned}
          aria-pressed={entry.pinned}
          onClick={() => void pinAiHistory(entry.id, !entry.pinned, pj).then(() => stillMine() && onChanged())}
        >
          <Pin size={ICON_SIZE.xs} filled={entry.pinned} className={entry.pinned ? undefined : 'text-ink-3'} />
        </IconButton>
        {entry.changed && entry.revert_available && (
          <IconButton
            data-ai-history-revert
            label={ai('history.revert')}
            tip={ai('history.revertTip')}
            iconSize="xs"
            className="text-ink-3 hover:text-danger"
            onClick={() =>
              void aiRevert(entry.id, pj).then(
                (res) => {
                  if (!stillMine()) return
                  // 这次修改改过名的话，画布编辑的身份跟着改回旧名字（ADR 0083 §五）
                  rebindRenamedIdentities(parseRenames(res?.renamed), 'back')
                  useUiStore.getState().setStatus(msg('history.reverted', undefined, 'ai'), 'done')
                  onChanged()
                },
                // 脚本在这次修改之后又变过（`ai_revert_conflict`）等：说出口，不装作已回滚——
                // 但只在还是那个项目的时候说（#589）
                (e) => {
                  if (stillMine()) useUiStore.getState().setStatus(backendErrorMsg(e), 'error')
                },
              )
            }
          >
            <RotateCcw size={ICON_SIZE.xs} />
          </IconButton>
        )}
        <IconButton
          data-ai-history-delete
          label={ai('history.delete')}
          iconSize="xs"
          className="text-ink-3 hover:text-ink"
          onClick={() => void deleteAiHistory(entry.id, pj).then(() => stillMine() && onChanged())}
        >
          <Trash2 size={ICON_SIZE.xs} />
        </IconButton>
      </div>
    </div>
  )
}
