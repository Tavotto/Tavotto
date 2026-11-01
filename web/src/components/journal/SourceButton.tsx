import { Fragment, useState } from 'react'
import { snapshotUrl, useJournalCatalogStore } from '@/store/journalCatalogStore'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { ArrowUpRight, BookOpen } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { Button, IconButton } from '../ui/Button'
import { Popover } from '../ui/Popover'
import type { JournalFieldSource } from '@/lib/journals'
import {
  adviceSentence,
  cardConclusion,
  highlightQuote,
  sourceHost,
} from '@/lib/journalPick'

const jt = (key: string, values?: Record<string, unknown>) =>
  translate(`journal.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 摘录（没有快照时顶部的那一块）：排在白「纸」上（深色主题里纸也是白的），命中的数用 `<mark>` 标出，
 * 荧光笔浅黄、字仍是正文墨色（`highlightQuote`：数据给了 `mark` 就用它，不猜）。
 */
function Quote({ source }: { source: JournalFieldSource }) {
  return (
    <blockquote data-journal-quote className="bg-paper px-3.5 py-3 text-sm leading-5 text-paper-ink">
      {highlightQuote(source.quote, source).map((seg, i) =>
        seg.hit ? (
          <mark key={i} className="rounded-xs bg-paper-highlight px-0.5 text-paper-ink">
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
 * 原网页高亮截图（采集时渲染、已裁成那一段）：满卡宽、贴顶贴边、无内框；最高约 210px，更高就裁；
 * 底边 28px 渐隐到纸白。纸在深色主题里也是白的。加载失败由调用方退回摘录——不显示任何错误态。
 */
function Snapshot({ source, onFail }: { source: JournalFieldSource; onFail: () => void }) {
  const base = useJournalCatalogStore((s) => s.assetBase)
  const snap = source.snapshot!
  return (
    <div data-journal-snapshot className="relative max-h-[210px] overflow-hidden bg-paper">
      <img
        src={snapshotUrl(base, snap.file)}
        width={snap.width}
        height={snap.height}
        alt={source.quote}
        onError={onFail}
        className="block h-auto w-full"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 bottom-0 h-7"
        style={{ backgroundImage: 'linear-gradient(to bottom, transparent, var(--color-paper))' }}
      />
    </div>
  )
}

/** 一条出处的「证据」：快照图优先，没有或加载失败退回摘录 */
function Evidence({ source }: { source: JournalFieldSource }) {
  const [failed, setFailed] = useState(false)
  if (source.snapshot && !failed) return <Snapshot source={source} onFail={() => setFailed(true)} />
  return <Quote source={source} />
}

/**
 * 出处卡（变体 C）：上面是原文证据（快照图或摘录，白纸出血到边），下面一条底栏——
 * 左两行：结论（15 semibold）与「期刊 · 域名 · 采集日期[ · PDF]」（12 灰）；右：「打开原网页 ↗」强调色文字链接，有存档再加灰色「存档」。
 * 链接交给浏览器（桌面壳里是系统浏览器）；`link` 已带 text fragment / `#page=N`。
 * 建议性字号（Elsevier 一类）的结论用 `adviceSentence`；多条出处（单栏 + 双栏、正文 + 上下标）上面各出一块证据，底栏取第一条的来源。
 */
export function SourceCardBody({
  journal,
  entries,
}: {
  journal: string
  entries: [string, JournalFieldSource][]
}) {
  const advice = adviceSentence(journal, Object.fromEntries(entries))
  const conclusion = advice ?? entries.map(([path, s]) => cardConclusion(path, s)).join('；')
  const lead = entries[0][1]
  const metaParts = [journal, sourceHost(lead.url), lead.retrieved, lead.kind === 'pdf' ? 'PDF' : ''].filter(Boolean)
  return (
    <div data-journal-card className="flex flex-col">
      {entries.map(([path, s]) => (
        <div key={path} data-journal-entry={path}>
          <Evidence source={s} />
        </div>
      ))}
      <div className="flex items-end justify-between gap-3 border-t border-border px-3.5 py-3">
        <div className="flex min-w-0 flex-col gap-0.5">
          <p data-journal-sentence className="type-title">
            {conclusion}
          </p>
          {/* 窄了只在「 · 」处换行（圆点留在行尾），日期 / 域名不被拦腰截断；读屏与测试看到的仍是一整句 */}
          <p data-journal-meta className="type-caption flex flex-wrap gap-x-1">
            {metaParts.map((part, i) => (
              <Fragment key={i}>
                {i > 0 && ' '}
                <span className="whitespace-nowrap">
                  {part}
                  {i < metaParts.length - 1 && ' ·'}
                </span>
              </Fragment>
            ))}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-3 pb-0.5 text-sm">
          <a
            href={lead.link}
            target="_blank"
            rel="noreferrer"
            data-journal-open
            className="inline-flex items-center gap-0.5 font-medium text-info-content hover:underline"
          >
            {jt('card.open')}
            <ArrowUpRight size={ICON_SIZE.sm} aria-hidden />
          </a>
          {lead.archive_url && (
            <a
              href={lead.archive_url}
              target="_blank"
              rel="noreferrer"
              data-journal-archive
              className="text-ink-3 hover:text-ink-2 hover:underline"
            >
              {jt('card.archive')}
            </a>
          )}
        </div>
      </div>
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
      width={360}
      flush
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
