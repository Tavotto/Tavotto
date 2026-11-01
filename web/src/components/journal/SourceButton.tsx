import { useState } from 'react'
import { snapshotUrl, useJournalCatalogStore } from '@/store/journalCatalogStore'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { BookOpen } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { Button, IconButton } from '../ui/Button'
import { buttonClass } from '../ui/buttonClass'
import { Popover } from '../ui/Popover'
import type { JournalFieldSource } from '@/lib/journals'
import {
  adviceSentence,
  cardSentence,
  highlightQuote,
  sourceHost,
} from '@/lib/journalPick'

const jt = (key: string, values?: Record<string, unknown>) =>
  translate(`journal.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/** 引文：命中的数用 `<mark>` 标出（`highlightQuote`：数据给了 `mark` 就用它，不猜） */
function Quote({ source }: { source: JournalFieldSource }) {
  return (
    <blockquote data-journal-quote className="rounded-md bg-surface-2 px-2 py-1.5 text-sm leading-5 text-ink-2">
      {highlightQuote(source.quote, source).map((seg, i) =>
        seg.hit ? (
          <mark key={i} className="rounded-xs bg-selected px-0.5 text-ink">
            {seg.text}
          </mark>
        ) : (
          <span key={i}>{seg.text}</span>
        ),
      )}
    </blockquote>
  )
}

/**
 * 原网页高亮截图（采集时渲染、已裁成那一段；全宽、高度跟图、最高约 240px，更高在卡内滚）。
 * 没有快照或图加载失败时由调用方退回摘录框——不显示任何错误态。
 */
function Snapshot({ source, onFail }: { source: JournalFieldSource; onFail: () => void }) {
  const base = useJournalCatalogStore((s) => s.assetBase)
  const snap = source.snapshot!
  return (
    <div
      data-journal-snapshot
      className="max-h-60 overflow-y-auto rounded-md border border-border"
    >
      <img
        src={snapshotUrl(base, snap.file)}
        width={snap.width}
        height={snap.height}
        alt={source.quote}
        onError={onFail}
        className="block h-auto w-full"
      />
    </div>
  )
}

/** 一条出处的「证据」：快照图优先，没有或加载失败退回摘录框 */
function Evidence({ source }: { source: JournalFieldSource }) {
  const [failed, setFailed] = useState(false)
  if (source.snapshot && !failed) return <Snapshot source={source} onFail={() => setFailed(true)} />
  return <Quote source={source} />
}

/** 链接交给浏览器（桌面壳里是系统浏览器，与其余外链同一写法）；`link` 已带 text fragment / `#page=N` */
function Links({ source }: { source: JournalFieldSource }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <a
        href={source.link}
        target="_blank"
        rel="noreferrer"
        data-journal-open
        className={buttonClass({ variant: 'primary', size: 'sm' })}
      >
        {jt('card.open')}
      </a>
      {source.archive_url && (
        <a
          href={source.archive_url}
          target="_blank"
          rel="noreferrer"
          data-journal-archive
          className={buttonClass({ variant: 'secondary', size: 'sm' })}
        >
          {jt('card.archive')}
        </a>
      )}
    </div>
  )
}

/**
 * 出处卡的内容：一句话 + 原文摘录（命中的数加 `<mark>`）+ 来源域名与采集日期 + 打开原网页（主按钮）/ 查看存档。
 * 建议性字号（Elsevier 一类）那句话用 `adviceSentence`，其余用「期刊：字段 值」；多条出处各出一块引文。
 */
export function SourceCardBody({
  journal,
  entries,
}: {
  journal: string
  entries: [string, JournalFieldSource][]
}) {
  const advice = adviceSentence(journal, Object.fromEntries(entries))
  const sentence =
    advice ?? entries.map(([path, s]) => cardSentence(journal, path, s)).join('；')
  return (
    <div data-journal-card className="flex flex-col gap-2 p-1">
      <p data-journal-sentence className="type-title">
        {sentence}
      </p>
      {entries.map(([path, s]) => (
        <div key={path} data-journal-entry={path} className="flex flex-col gap-1.5">
          <Evidence source={s} />
          <p data-journal-meta className="type-caption">
            {jt('card.meta', { host: sourceHost(s.url), date: s.retrieved })}
          </p>
          <Links source={s} />
        </div>
      ))}
    </div>
  )
}

/**
 * 期刊值旁边的小「出处」按钮 → 悬浮卡。选择器的要求清单与预检结果行共用这一个。
 * 没有出处（`entries` 为空）不渲染——期刊没规定的项没有卡可开。
 */
export function SourceButton({
  journal,
  entries,
  name,
  compact = false,
}: {
  journal: string
  entries: [string, JournalFieldSource][]
  /** 这个值叫什么（可达名「<名>的出处」） */
  name: string
  /** 窄处（问题树的尾随格）：只画一枚小图标，文字「出处」留给可达名与悬停提示 */
  compact?: boolean
}) {
  useTranslation('dialogs')
  const [open, setOpen] = useState(false)
  if (!entries.length) return null
  return (
    <Popover
      open={open}
      onOpenChange={setOpen}
      width={320}
      ariaLabel={jt('card.aria')}
      trigger={
        compact ? (
          <IconButton
            iconSize="xs"
            label={jt('sourceOf', { name })}
            data-journal-source={entries[0][0]}
            className="text-ink-3 hover:text-ink-2"
          >
            <BookOpen size={ICON_SIZE.sm} />
          </IconButton>
        ) : (
          <Button
            size="sm"
            variant="ghost"
            data-journal-source={entries[0][0]}
            aria-label={jt('sourceOf', { name })}
            className="text-ink-2"
          >
            {jt('source')}
          </Button>
        )
      }
    >
      <SourceCardBody journal={journal} entries={entries} />
    </Popover>
  )
}
