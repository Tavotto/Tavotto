import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { msg, t as translate } from '@/i18n'
import { ChevronDown } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { Spinner } from '@/components/ui/Spinner'
import { Button } from '../ui/Button'
import { EmptyLine } from '../ui/EmptyLine'
import { SUBTLE_LINK_CLASS } from '../ui/subtleLink'
import { Popover } from '../ui/Popover'
import { SearchInput } from '../ui/SearchInput'
import { listRowClass } from '../ui/listRow'
import { cn } from '@/lib/utils'
import { listJournals } from '@/lib/journals'
import { groupByPublisher, searchJournals } from '@/lib/journalPick'
import { ensureJournalReady } from '@/lib/journalCatalogSync'
import { journalCatalogUi } from '@/lib/journalUi'
import { clearJournal, selectJournal, selectV2OnlyJournal } from '@/store/actions'
import { fetchJournalV2, fetchJournalV2Index, fetchJournalV2Outcome, v2OnlySummaries, type V2IndexEntry, type V2Patch } from '@/lib/journalV2'
import { useJournalV2Store } from '@/store/journalV2Store'
import { useDocumentStore } from '@/store/documentStore'
import { useJournalCatalogStore } from '@/store/journalCatalogStore'
import { useUiStore } from '@/store/uiStore'
import { useJournalPickerRequest } from '@/store/journalPickerRequest'
import { journalTipsAvailable, useContactStore } from '@/store/contactStore'
import { tipForMissing } from '@/lib/journalTip'

const jt = (key: string, values?: Record<string, unknown>) =>
  translate(`journal.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 选择序号（模块级，选择器卸载后也还在）：每次点选 / 点「不按期刊」加一。下载要等网络，等的时候选择器仍可交互、
 * 设置也能关掉、项目也能换——回来时序号变了（被新选择取代）或文档变了，这次结果丢弃，不去改别人的绑定与页宽
 * （web/AGENTS.md「会在项目之间存活的 store 都有项目代际」：请求序号挡旧响应、发请求那一刻的归属挡串项目）。
 */
let pickSeq = 0

/** 点选那一刻的归属：项目 + 画布（`switchCanvas` 不换 documentId，绑定却是按画布存的，只比项目会写到另一张画布上） */
const docOrigin = (): string => {
  const s = useDocumentStore.getState()
  return `${s.documentId}\u0000${s.activeCanvasId}`
}

/**
 * 选择器写到哪里去。**缺省 = 写当前激活画布的文档**（设置里的用法，行为与此前逐字相同）；
 * 「从数据到图」右栏的「期刊」一行传自己的：只改草稿的绑定，不碰任何画布（`components/datastudio/DataPanel`）。
 * 下载 / 额度 / 序号 / 目录这一整套网络逻辑只有这一份，目标只决定「选定之后写哪」。
 */
export interface JournalPickTarget {
  /** 此刻已选的刊（决定触发器上的名字与列表里的选中项）；没选 = undefined */
  ref: { id: string; label: string } | undefined
  select: (journalId: string, v2: V2Patch | null) => void
  selectV2Only: (entry: Pick<V2IndexEntry, 'id' | 'label'>, patch: V2Patch) => void
  /** 「不按期刊」，也是额度用完时的退路 */
  clear: () => void
  /** 点选那一刻的归属标识：等下载回来时它变了（换了项目 / 画布）就丢弃这次结果 */
  origin: () => string
}

const DOC_TARGET: JournalPickTarget = {
  ref: undefined,
  select: selectJournal,
  selectV2Only: selectV2OnlyJournal,
  clear: clearJournal,
  origin: docOrigin,
}

/**
 * 期刊选择器：可搜索（名称 / 缩写 / ISSN），按出版社分组。选中 = `selectJournal`（一次提交：绑定 + 页宽跟栏，
 * 撤销一步全回去）。目录空（种子为空且没有缓存）时只有一行中性的话，不写任何解释。
 */
export function JournalPicker({
  target,
  triggerClassName,
  disabled,
  anchor,
}: {
  target?: JournalPickTarget
  triggerClassName?: string
  disabled?: boolean
  /** 触发器自己的锚点属性名（如 `data-issues-journal-picker`）：给了就用它、不再占 `id="journal-picker"`（设置里的那一个独占） */
  anchor?: `data-${string}`
} = {}) {
  useTranslation('dialogs')
  const catalog = useJournalCatalogStore((s) => s.catalog)
  const docRef = useDocumentStore((s) => s.doc.profile?.journalRef)
  const ref = target ? target.ref : docRef
  const write = target ?? DOC_TARGET
  const originOf = write.origin
  const sameOrigin = (o: string) => originOf() === o
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  // 外部请求展开（助手回答里的「选期刊」）：只有问题页签那一行（带锚点、写当前文档）接
  const openRequested = useJournalPickerRequest((s) => s.pending)
  useEffect(() => {
    if (!openRequested || target || anchor !== 'data-issues-journal-picker' || disabled) return
    if (useJournalPickerRequest.getState().consume()) setOpen(true)
  }, [openRequested, target, anchor, disabled])
  const canTip = useContactStore((s) => journalTipsAvailable(s.capability))
  const [pending, setPending] = useState<{ seq: number; label: string } | null>(null)
  const v2Index = useJournalV2Store((s) => s.index)
  const v1 = useMemo(() => listJournals(catalog), [catalog])
  // 只在 v2 里有的刊（v1 目录没有）并进同一个列表；index 取不到就是没有这些行，别的照旧
  const v2Only = useMemo(() => v2OnlySummaries(v2Index, catalog), [v2Index, catalog])
  const all = useMemo(() => [...v1, ...v2Only], [v1, v2Only])
  const groups = useMemo(() => groupByPublisher(searchJournals(all, query)), [all, query])
  const first = groups[0]?.journals[0]

  // 打开选择器时取一次已签名的 v2 index（只读公开 index：不逐刊拉包、不占额度）；上次没取到的下次打开再试
  useEffect(() => {
    if (!open || !journalCatalogUi() || useJournalV2Store.getState().index) return
    const controller = new AbortController()
    void fetchJournalV2Index(controller.signal).then((entries) => {
      if (!controller.signal.aborted && entries) useJournalV2Store.getState().setIndex(entries)
    })
    return () => controller.abort()
  }, [open])

  /** 这本刊只有 v2：没有 v2 内容就没有东西可绑，所以不设 2 秒上限，等到有结果 */
  const runV2Only = async (id: string, seq: number, origin: string) => {
    const entry = v2Only.find((j) => j.journal_id === id)
    if (!entry) return
    const out = await fetchJournalV2Outcome(id)
    if (seq !== pickSeq || !sameOrigin(origin)) return
    if (out.state !== 'ready' || !out.patch) {
      // 终态与 v1 远程刊同一套：额度用完 → 暂按「不按期刊」；没网 / 被拒 / 开关关 → 保持现状。都只一条胶囊
      if (out.state === 'quota') write.clear()
      useUiStore
        .getState()
        .setStatus(
          out.state === 'quota' ? msg('journal.quotaLater', undefined, 'dialogs') : msg('journal.needsNetwork', undefined, 'dialogs'),
          'info',
        )
      return
    }
    write.selectV2Only({ id, label: entry.label }, out.patch)
  }

  const runV1 = async (id: string, seq: number, origin: string) => {
    // 远程目录里还没下载的刊：先向服务端取这一本的标准（种子 / 已下载的不发请求）。
    // 额度用完 → 这本刊暂时按「不按期刊」；没网又没缓存 → 保持现状。两种都只用一条胶囊提示。
    const ready = await ensureJournalReady(id)
    // 等下载时被新选择取代，或文档已经换了：丢弃（下载下来的刊包已并入目录，那是目录的事，与绑定无关）
    if (seq !== pickSeq || !sameOrigin(origin)) return
    if (ready.state !== 'ready') {
      if (ready.state === 'quota') write.clear()
      useUiStore
        .getState()
        .setStatus(
          ready.state === 'quota'
            ? msg('journal.quotaLater', undefined, 'dialogs')
            : msg('journal.needsNetwork', undefined, 'dialogs'),
          'info',
        )
      return
    }
    // v2 事实（取不到就是 null，照 v1 选）：同样要防「等的时候被取代 / 文档换了」
    // 最多等 2 秒：慢就先按 v1 选，v2 到了由「打开项目自动更新」那条路叠上去（useJournalV2Sync）
    const v2 = await Promise.race([fetchJournalV2(id), new Promise<null>((resolve) => setTimeout(() => resolve(null), 2000))])
    if (seq !== pickSeq || !sameOrigin(origin)) return
    write.select(id, v2)
  }

  /**
   * 点一项：**立刻**关浮层、触发器立刻显示所选刊名（等下载 / v2 的这段时间文字转灰、箭头换成小转圈，`pending`）；
   * 取到后照旧提交。失败 / 额度用完 / 被新选择取代 / 归属变了：`pending` 撤掉，触发器回到 `ref` 的刊名。
   * `pending` 带自己的序号，只撤自己那一份（被取代的那次结束时不会把后来者的撤掉）。
   */
  const choose = async (id: string) => {
    const label = all.find((j) => j.journal_id === id)?.label ?? id
    const seq = ++pickSeq
    const origin = originOf()
    setPending({ seq, label })
    setOpen(false)
    setQuery('')
    try {
      if (v2Only.some((j) => j.journal_id === id)) await runV2Only(id, seq, origin)
      else await runV1(id, seq, origin)
    } finally {
      setPending((p) => (p?.seq === seq ? null : p))
    }
  }

  return (
    <Popover
      open={open}
      onOpenChange={(v) => {
        setOpen(v)
        if (!v) setQuery('')
      }}
      width={320}
      align="end"
      ariaLabel={jt('title')}
      trigger={
        <Button
          variant="secondary"
          size="md"
          disabled={disabled}
          aria-busy={pending ? true : undefined}
          // 设置里的那一个保留 id / 锚点；草稿的那一个换自己的锚点（设置对话框可能开在数据页上面，同一个 id / 锚点出现两次选择器会认不出谁）
          {...(anchor ? { [anchor]: '' } : target ? { 'data-draft-journal-picker': '' } : { id: 'journal-picker', 'data-journal-picker': '' })}
          className={cn('w-65 min-w-0 shrink justify-between', triggerClassName)}
        >
          <span className={cn('min-w-0 truncate', pending && 'text-ink-3')}>{pending?.label ?? ref?.label ?? jt('pick')}</span>
          {pending ? (
            <Spinner size={ICON_SIZE.xs} aria-hidden className="shrink-0 text-ink-3" />
          ) : (
            <ChevronDown size={ICON_SIZE.xs} aria-hidden className="shrink-0 text-ink-3" />
          )}
        </Button>
      }
    >
      {all.length === 0 && !ref ? (
        <EmptyLine data-journal-empty>{jt('none')}</EmptyLine>
      ) : (
        <div className="flex flex-col gap-1.5">
          {/* 目录不可用（空 / 还没取到）时搜索与列表都没东西可搜，但项目已按某本期刊检查的话「不按期刊」照样给：退路不依赖目录 */}
          {all.length > 0 && (
            <SearchInput
              value={query}
              onValueChange={setQuery}
              placeholder={jt('search')}
              aria-label={jt('searchAria')}
              data-journal-search
              autoFocus
              onKeyDown={(e) => {
                if (e.key === 'Enter' && first) choose(first.journal_id)
              }}
            />
          )}
          <div
            role="listbox"
            aria-label={jt('listAria')}
            data-journal-list
            className="flex flex-col"
          >
            {!query && (
              <button
                type="button"
                role="option"
                aria-selected={!ref}
                data-journal-none
                onClick={() => {
                  pickSeq++
                  setPending(null)
                  write.clear()
                  setOpen(false)
                }}
                className={cn(listRowClass({ size: 'sm', selected: !ref }), 'w-[calc(100%-0.5rem)] px-2 text-left')}
              >
                <span className="min-w-0 flex-1 truncate">{jt('noJournal')}</span>
              </button>
            )}
            {/* 「不按期刊」钉在最上面，列表在它下面自己滚：选中项再靠后也不会把它顶出视野 */}
            <div data-journal-scroller className="flex max-h-64 flex-col overflow-y-auto">
              {all.length > 0 && groups.length === 0 && (
                <EmptyLine
                  data-journal-empty
                  action={
                    canTip
                      ? {
                          label: jt('noJournalListed'),
                          title: jt('noJournalListedTitle'),
                          className: SUBTLE_LINK_CLASS,
                          onClick: () => useContactStore.getState().openJournalTip(tipForMissing(query)),
                        }
                      : undefined
                  }
                >
                  {jt('noMatch')}
                </EmptyLine>
              )}
              {groups.map((g) => (
                <div key={g.publisher} role="group" aria-label={g.label} data-journal-group={g.publisher}>
                  <p className="type-caption px-3 pb-0.5 pt-1.5">{g.label}</p>
                  {g.journals.map((j) => {
                    const selected = ref?.id === j.journal_id
                    return (
                      <button
                        key={j.journal_id}
                        type="button"
                        role="option"
                        aria-selected={selected}
                        data-journal-option={j.journal_id}
                        onClick={() => choose(j.journal_id)}
                        className={cn(listRowClass({ size: 'sm', selected }), 'w-[calc(100%-0.5rem)] px-2 text-left')}
                      >
                        <span className="min-w-0 flex-1 truncate">{j.label}</span>
                      </button>
                    )
                  })}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </Popover>
  )
}
