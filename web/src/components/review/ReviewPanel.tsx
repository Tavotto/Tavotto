import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { ReviewComment, ReviewShareDetail } from '@/lib/api'
import { reviewSnapshotUrl } from '@/lib/api'
import { shareLive } from '@/lib/reviewPlacement'
import { cn } from '@/lib/utils'
import { TAB_REFRESH_MS, useReviewStore } from '@/store/reviewStore'
import { CopyButton } from '../settings/CopyButton'
import { Badge } from '../ui/Badge'
import { Button } from '../ui/Button'
import { EmptyLine } from '../ui/EmptyLine'
import { TextInput } from '../ui/Input'
import { Segmented } from '../ui/Segmented'
import { toastLinkCopied } from './ReviewDialog'
import { commentWhen, expiryDate, rs } from './reviewText'
import { useReviewPlacements, type PlacedComment } from './useReviewPlacements'

type Filter = 'all' | 'open'

/** 称呼头像字：取称呼的第一个字（按码点，不劈 emoji） */
export const avatarChar = (name: string): string => [...name.trim()][0] ?? '?'

/** 该条意见锚定的对象名：元素 = 图里那个元素的文字（版本元素表里的 label），区域 / 整体各一句 */
function targetName(c: ReviewComment, detail: ReviewShareDetail): string {
  if (c.anchor.kind === 'overall') return rs('comment.overall')
  if (c.anchor.kind === 'region') return rs('comment.region')
  const gid = c.anchor.gid
  const v = detail.versions.find((x) => x.v === c.v)
  return v?.elements.find((e) => e.gid === gid)?.label || gid
}

/** 「原位置已变化」那一行：该版本的快照缩略图 + 原位置的圆点 + 一行灰字 */
function MovedNote({ shareId, c }: { shareId: string; c: ReviewComment }) {
  const [failed, setFailed] = useState(false)
  const box = c.anchor.kind === 'overall' ? null : c.anchor.box
  return (
    <div className="flex items-center gap-2" data-review-moved>
      {box && !failed && (
        <span className="relative h-9 w-12 shrink-0 overflow-hidden rounded bg-surface-hover">
          <img
            src={reviewSnapshotUrl(shareId, c.v)}
            alt=""
            draggable={false}
            className="size-full object-contain"
            onError={() => setFailed(true)}
          />
          <span
            aria-hidden
            className="absolute size-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-sel ring-1 ring-surface"
            style={{ left: `${(box[0] + box[2] / 2) * 100}%`, top: `${(box[1] + box[3] / 2) * 100}%` }}
          />
        </span>
      )}
      <span className="text-xs text-ink-3">{rs('comment.moved')}</span>
    </div>
  )
}

function Avatar({ name, owner }: { name: string; owner?: boolean }) {
  return (
    <span
      aria-hidden
      className={cn(
        'inline-grid size-5 shrink-0 place-items-center rounded-full text-xs font-medium',
        owner ? 'bg-ink text-surface' : 'bg-surface-active text-ink-2',
      )}
    >
      {avatarChar(name)}
    </span>
  )
}

function CommentCard({ p, detail, live }: { p: PlacedComment; detail: ReviewShareDetail; live: boolean }) {
  const c = p.comment
  const selected = useReviewStore((s) => s.selectedId === c.id)
  const store = useReviewStore.getState
  const [replying, setReplying] = useState(false)
  const [text, setText] = useState('')
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (selected) ref.current?.scrollIntoView?.({ block: 'nearest' })
  }, [selected])
  const send = async () => {
    if (await store().reply(c.id, text)) {
      setText('')
      setReplying(false)
    }
  }
  return (
    <div
      ref={ref}
      data-review-comment={c.id}
      data-review-selected={selected || undefined}
      data-review-resolved={c.resolved || undefined}
      className={cn(
        'flex flex-col gap-1.5 border-b border-border px-3 py-2.5 text-sm',
        selected && 'bg-selected',
        c.resolved && 'opacity-70',
      )}
      onClick={() => store().select(c.id)}
    >
      <div className="flex items-center gap-1.5">
        <Avatar name={c.author} />
        <b className="min-w-0 truncate text-xs font-medium text-ink">{c.author}</b>
        <Badge mono>{rs('comment.version', { v: c.v })}</Badge>
        <span className="ml-auto shrink-0 text-xs text-ink-3">{commentWhen(c.created_at)}</span>
      </div>
      <div className="flex items-center gap-1 text-xs text-ink-3">
        <span className="min-w-0 truncate">{targetName(c, detail)}</span>
        {c.resolved && <span>· {rs('comment.done')}</span>}
      </div>
      {p.placement === 'moved' && <MovedNote shareId={detail.share_id} c={c} />}
      <p className="whitespace-pre-wrap break-words text-ink">{c.text}</p>
      {c.replies.map((r) => (
        <div key={r.id} className="ml-3 flex flex-col gap-0.5 border-l border-border pl-2" data-review-reply={r.from}>
          <div className="flex items-center gap-1.5">
            <Avatar name={r.from === 'owner' ? rs('comment.owner') : r.author} owner={r.from === 'owner'} />
            <b className="text-xs font-medium text-ink">{r.from === 'owner' ? rs('comment.owner') : r.author}</b>
            <span className="ml-auto text-xs text-ink-3">{commentWhen(r.created_at)}</span>
          </div>
          <p className="whitespace-pre-wrap break-words text-ink-2">{r.text}</p>
        </div>
      ))}
      {live && replying && (
        <form
          className="flex items-center gap-1.5"
          onClick={(e) => e.stopPropagation()}
          onSubmit={(e) => {
            e.preventDefault()
            void send()
          }}
        >
          <TextInput
            autoFocus
            value={text}
            maxLength={500}
            placeholder={rs('comment.replyPlaceholder')}
            aria-label={rs('comment.reply')}
            onChange={(e) => setText(e.target.value)}
            data-review-reply-input
          />
          <Button type="submit" variant="secondary" size="md" disabled={!text.trim()}>
            {rs('comment.send')}
          </Button>
        </form>
      )}
      {live && (
        <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
          {!replying && (
            <Button variant="ghost" size="sm" onClick={() => setReplying(true)} data-review-reply-open>
              {rs('comment.reply')}
            </Button>
          )}
          <Button variant="ghost" size="sm" onClick={() => void store().setResolved(c.id, !c.resolved)} data-review-resolve>
            {c.resolved ? rs('comment.reopen') : rs('comment.resolve')}
          </Button>
        </div>
      )}
    </div>
  )
}

/**
 * 右栏「审阅」页签（ADR 0187 §八）：只有这张图 / 这份排版创建过分享才出现。顶部一行灰字（版本 · 失效日）+ 复制链接，
 * 分段「全部 / 未处理」，意见列表。**不显示结论条**（维护者裁决）。页签开着时每 60 秒让引擎拉一次；打开那次读一遍并清未读。
 * 已停止 / 已过期：页签仍在，一行灰字说明，意见副本照看，可「删除这份记录」。
 */
export function ReviewPanel() {
  useTranslation('dialogs')
  const share = useReviewStore((s) => s.share)
  const detail = useReviewStore((s) => s.detail)
  const selectedId = useReviewStore((s) => s.selectedId)
  const [filter, setFilter] = useState<Filter>('all')
  const placed = useReviewPlacements()
  const shareId = share?.share_id

  useEffect(() => {
    if (!shareId) return
    void useReviewStore.getState().refreshTab({ markRead: true })
    const id = window.setInterval(() => void useReviewStore.getState().refreshTab(), TAB_REFRESH_MS)
    return () => window.clearInterval(id)
  }, [shareId])

  if (!share) return null
  const live = shareLive(share)
  const open = placed.filter((p) => !p.comment.resolved)
  const shown = filter === 'open' ? open : placed
  const note = live
    ? rs('tab.live', { v: share.latest_version, date: expiryDate(share.expires_at) })
    : rs(share.revoked ? 'tab.revoked' : 'tab.expired')

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-review-panel data-review-selected-id={selectedId ?? undefined}>
      <div className="flex shrink-0 items-center gap-2 px-3 py-2">
        <span className="min-w-0 flex-1 truncate text-xs text-ink-3" title={note} data-review-note-line>
          {note}
        </span>
        {live ? (
          <CopyButton text={share.review_url} label={rs('tab.copyLink')} variant="secondary" feedback="toast" onCopied={toastLinkCopied} />
        ) : (
          <Button variant="ghost" size="sm" onClick={() => void useReviewStore.getState().removeRecord()} data-review-delete>
            {rs('tab.deleteRecord')}
          </Button>
        )}
      </div>
      <div className="shrink-0 px-3 pb-2">
        <Segmented<Filter>
          value={filter}
          onChange={setFilter}
          ariaLabel={rs('tab.filterLabel')}
          items={[
            { value: 'all', label: rs('tab.all') },
            { value: 'open', label: rs('tab.open', { n: open.length }) },
          ]}
        />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto border-t border-border">
        {detail && shown.length > 0 ? (
          shown.map((p) => <CommentCard key={p.comment.id} p={p} detail={detail} live={live} />)
        ) : (
          <EmptyLine data-review-empty>{rs(filter === 'open' && placed.length > 0 ? 'tab.emptyOpen' : 'tab.empty')}</EmptyLine>
        )}
      </div>
    </div>
  )
}
