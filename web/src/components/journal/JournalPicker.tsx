import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
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
import { selectJournal } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useJournalCatalogStore } from '@/store/journalCatalogStore'

const jt = (key: string, values?: Record<string, unknown>) =>
  translate(`journal.${key}`, { ns: 'dialogs', ...(values ?? {}) })

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

  const choose = (id: string) => {
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
      {all.length === 0 ? (
        <EmptyLine data-journal-empty>{jt('none')}</EmptyLine>
      ) : (
        <div className="flex flex-col gap-1.5">
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
          <div
            role="listbox"
            aria-label={jt('listAria')}
            data-journal-list
            className="flex max-h-72 flex-col overflow-y-auto"
          >
            {groups.length === 0 && <EmptyLine data-journal-empty>{jt('noMatch')}</EmptyLine>}
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
      )}
    </Popover>
  )
}
