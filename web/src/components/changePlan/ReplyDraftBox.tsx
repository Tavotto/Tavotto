import { useState } from 'react'
import { useReviewStore } from '@/store/reviewStore'
import { useChangePlanStore } from '@/store/changePlanStore'
import { cn } from '@/lib/utils'
import { Button } from '../ui/Button'
import { TextArea } from '../ui/Input'
import { rs } from '../review/reviewText'
import { useReplyDraft } from './reviewSync'

/**
 * 被「交给 Agent 修改」处理掉的意见下的回复草稿框（ADR 0206 §六）：收拢时 2 行、细描边；获焦展开，「发送」在框内右下。
 * 草稿由前端从这条意见的改动现算（`lib/changePlanReplyDraft`），用户改，点「发送」才走现有回复端点；没发出去不会自己发。
 */
export function ReplyDraftBox({ commentId }: { commentId: string }) {
  const draft = useReplyDraft(commentId)
  const [open, setOpen] = useState(false)
  const [sending, setSending] = useState(false)
  if (!draft) return null
  const set = (text: string | null) => useChangePlanStore.getState().patchEntry(draft.key, { drafts: { ...(useChangePlanStore.getState().entries[draft.key]?.drafts ?? {}), [commentId]: text } })
  const send = async () => {
    if (sending || !draft.text.trim()) return
    setSending(true)
    const ok = await useReviewStore.getState().reply(commentId, draft.text)
    setSending(false)
    if (ok) set(null)
  }
  return (
    <div
      data-review-reply-draft={commentId}
      className="flex items-start gap-1.5"
      onClick={(e) => e.stopPropagation()}
      onFocus={() => setOpen(true)}
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOpen(false)
      }}
    >
      <span className="shrink-0 pt-1.5 text-xs leading-relaxed text-ink-3" data-review-reply-draft-tag>
        {rs('changePlan.draftLabel')}
      </span>
      <div className="relative min-w-0 flex-1">
      <TextArea
        value={draft.text}
        rows={open ? 3 : 2}
        maxRows={6}
        maxLength={500}
        aria-label={rs('changePlan.draftLabel')}
        className={cn('cursor-text text-xs', open && 'pb-9')}
        title={rs('changePlan.draftHint')}
        placeholder={rs('changePlan.draftHint')}
        onChange={(e) => set(e.target.value)}
        data-review-reply-draft-input
      />
      {open && (
        <Button
          size="sm"
          variant="secondary"
          className="absolute bottom-1.5 right-1.5"
          disabled={!draft.text.trim() || sending}
          // 不抢走输入框的焦点（mac Safari 点按钮不聚焦，blur 会先把框收起来、点击落空）
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => void send()}
          data-review-reply-draft-send
        >
          {rs('comment.send')}
        </Button>
      )}
      </div>
    </div>
  )
}
