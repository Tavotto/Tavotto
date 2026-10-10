import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronRight, Pin, RotateCcw, RotateCcwClock, Trash2, X } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { msg, t as translate } from '@/i18n'
import { formatTime } from '@/i18n/format'
import {
  agentDisplayName,
  aiRevert,
  backendErrorMsg,
  backendErrorText,
  deleteAiHistory,
  fetchAiHistory,
  pinAiHistory,
  type AiHistoryEntry,
} from '@/lib/api'
import { currentProjectId } from '@/lib/session'
import { cn } from '@/lib/utils'
import { scriptName, useAiStore } from '@/store/aiStore'
import { useUiStore } from '@/store/uiStore'
import { Button, IconButton } from '../ui/Button'
import { EmptyState } from '../ui/EmptyState'
import { SearchInput } from '../ui/SearchInput'
import { Select } from '../ui/Select'
import { StatusPill } from '../ui/StatusPill'
import { statusLabel } from './transcriptModel'

const ai = (key: string, values?: Record<string, unknown>) => translate(key, { ns: 'ai', ...(values ?? {}) })

/** 「全部状态」的哨兵：Radix Select 不接受空串作为 Item 的值 */
const ALL_STATUSES = '__all__'

/** 历史筛选下拉里的状态集合（会话状态 + 只在历史里出现的 interrupted） */
const HISTORY_STATUSES = ['running', 'done', 'failed', 'timeout', 'cancelled', 'reverted', 'interrupted']

const PAGE = 20

/**
 * 任务历史：项目级持久化记录（SQLite），刷新与后端重启后仍在。
 * 默认只显示人类可读目标；脚本名等技术信息在条目的「技术详情」里。
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
          <EmptyState icon={RotateCcwClock} title={ai(filtering ? 'history.noMatch' : 'history.empty')} />
        ) : (
          <div className="flex flex-col gap-0.5">
            {entries.map((s) => (
              <HistoryRow key={s.id} entry={s} pj={pj} onChanged={() => void load(query, status, offset)} />
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
}: {
  entry: AiHistoryEntry
  /** 这条记录所属的项目（历史视图打开时认领的那个） */
  pj: string | null
  onChanged: () => void
}) {
  useTranslation('ai')
  // 动作回来时已经换了项目：后果（提示、重查）属于那个项目，不写进现在这个
  const stillMine = () => currentProjectId() === pj
  const caps = useAiStore((s) => s.caps)
  const [detailsOpen, setDetailsOpen] = useState(false)
  const failed = entry.status === 'failed' || entry.status === 'timeout' || entry.status === 'interrupted'

  return (
    // 弹层里的一行（不再是一张卡：弹层本身就是那张浮起来的面）：8px 圆角，hover 浮 surface-hover
    <div
      data-ai-history-row={entry.status}
      className="rounded-md px-1.5 py-1.5 transition-colors duration-fast hover:bg-surface-hover"
    >
      <p className="line-clamp-2 text-sm leading-relaxed text-ink">{entry.prompt}</p>
      <p className="type-meta mt-0.5 truncate">
        {/* 历史里的 provider 是**当时**用的那个 Agent id：显示名从当前
            capabilities 查，查不到就原样显示 id（不写死两个名字，也不留空） */}
        {entry.target || ai('scope.figure')} · {agentDisplayName(caps, entry.provider)}
        {entry.model ? ` · ${entry.model}` : ''} · {timeOf(entry.started_ms)}
      </p>
      <div className="mt-1 flex items-center gap-0.5">
        <StatusPill tone={failed ? 'danger' : entry.changed ? 'ok' : 'neutral'}>
          {statusLabel(entry.status)}
          {entry.changed ? ai('history.changedSuffix') : ''}
        </StatusPill>
        <span className="flex-1" />
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
            variant="danger"
            onClick={() =>
              void aiRevert(entry.id, pj).then(
                () => {
                  if (!stillMine()) return
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
      {entry.error && <p className="mt-0.5 text-sm text-danger-content">{entry.error}</p>}
      <button
        type="button"
        data-ai-history-details
        onClick={() => setDetailsOpen((v) => !v)}
        aria-expanded={detailsOpen}
        className="mt-0.5 flex items-center gap-1 rounded-sm text-left text-xs text-ink-3 outline-none hover:text-ink-2 focus-visible:focus-ring"
      >
        <ChevronRight size={ICON_SIZE.xs} className={cn('shrink-0 transition-transform', detailsOpen && 'rotate-90')} />
        {ai('panel.techDetails')}
      </button>
      {detailsOpen && (
        <div className="mt-0.5 flex flex-col gap-0.5 border-l border-border pl-2">
          {/* 脚本名是路径 → 等宽片；句子本身不是代码 */}
          <p className="type-meta flex min-w-0 items-center gap-1">
            <span className="shrink-0">{ai('panel.scriptLabel')}</span>
            <code className="min-w-0 truncate rounded-xs bg-surface-2 px-1 font-mono text-ink-2">
              {entry.script ? scriptName(entry.script) : ai('panel.none')}
            </code>
          </p>
          {entry.effort && <p className="type-meta">{ai('history.effort', { effort: entry.effort })}</p>}
          <p className="type-meta">
            {ai(entry.revert_available ? 'history.snapshotAvailable' : 'history.snapshotCleared', { id: entry.id })}
          </p>
        </div>
      )}
    </div>
  )
}
