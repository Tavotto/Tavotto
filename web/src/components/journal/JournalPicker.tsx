import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { msg, t as translate } from '@/i18n'
import { ChevronDown } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { Button } from '../ui/Button'
import { EmptyLine } from '../ui/EmptyLine'
import { Popover } from '../ui/Popover'
import { SearchInput } from '../ui/SearchInput'
import { listRowClass } from '../ui/listRow'
import { cn } from '@/lib/utils'
import { listJournals } from '@/lib/journals'
import { groupByPublisher, searchJournals } from '@/lib/journalPick'
import { ensureJournalReady } from '@/lib/journalCatalogSync'
import { clearJournal, selectJournal } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useJournalCatalogStore } from '@/store/journalCatalogStore'
import { useUiStore } from '@/store/uiStore'

const jt = (key: string, values?: Record<string, unknown>) =>
  translate(`journal.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 选择序号（模块级，选择器卸载后也还在）：每次点选 / 点「不按期刊」加一。下载要等网络，等的时候选择器仍可交互、
 * 设置也能关掉、项目也能换——回来时序号变了（被新选择取代）或文档变了，这次结果丢弃，不去改别人的绑定与页宽
 * （web/AGENTS.md「会在项目之间存活的 store 都有项目代际」：请求序号挡旧响应、发请求那一刻的归属挡串项目）。
 */
let pickSeq = 0

/**
 * 期刊选择器：可搜索（名称 / 缩写 / ISSN），按出版社分组。选中 = `selectJournal`（一次提交：绑定 + 页宽跟栏，
 * 撤销一步全回去）。目录空（种子为空且没有缓存）时只有一行中性的话，不写任何解释。
 */
export function JournalPicker() {
  useTranslation('dialogs')
  const catalog = useJournalCatalogStore((s) => s.catalog)
  const ref = useDocumentStore((s) => s.doc.profile?.journalRef)
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const all = useMemo(() => listJournals(catalog), [catalog])
  const groups = useMemo(() => groupByPublisher(searchJournals(all, query)), [all, query])
  const first = groups[0]?.journals[0]

  const choose = async (id: string) => {
    // 远程目录里还没下载的刊：先向服务端取这一本的标准（种子 / 已下载的不发请求）。
    // 额度用完 → 这本刊暂时按「不按期刊」；没网又没缓存 → 保持现状。两种都只用一条胶囊提示。
    const seq = ++pickSeq
    const docId = useDocumentStore.getState().documentId
    const ready = await ensureJournalReady(id)
    // 等下载时被新选择取代，或文档已经换了：丢弃（下载下来的刊包已并入目录，那是目录的事，与绑定无关）
    if (seq !== pickSeq || useDocumentStore.getState().documentId !== docId) return
    if (ready.state !== 'ready') {
      if (ready.state === 'quota') clearJournal()
      useUiStore
        .getState()
        .setStatus(
          ready.state === 'quota'
            ? msg('journal.quotaLater', undefined, 'dialogs')
            : msg('journal.needsNetwork', undefined, 'dialogs'),
          'info',
        )
      setOpen(false)
      return
    }
    selectJournal(id)
    setOpen(false)
    setQuery('')
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
          id="journal-picker"
          data-journal-picker
          className="w-65 min-w-0 shrink justify-between"
        >
          <span className="min-w-0 truncate">{ref?.label ?? jt('pick')}</span>
          <ChevronDown size={ICON_SIZE.xs} aria-hidden className="shrink-0 text-ink-3" />
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
                  clearJournal()
                  setOpen(false)
                }}
                className={cn(listRowClass({ size: 'sm', selected: !ref }), 'w-[calc(100%-0.5rem)] px-2 text-left')}
              >
                <span className="min-w-0 flex-1 truncate">{jt('noJournal')}</span>
              </button>
            )}
            {/* 「不按期刊」钉在最上面，列表在它下面自己滚：选中项再靠后也不会把它顶出视野 */}
            <div data-journal-scroller className="flex max-h-64 flex-col overflow-y-auto">
              {all.length > 0 && groups.length === 0 && <EmptyLine data-journal-empty>{jt('noMatch')}</EmptyLine>}
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
