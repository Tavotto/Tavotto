import { useEffect, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { msg } from '@/i18n/message'
import { CopyButton } from '../settings/CopyButton'
import { CanvasThumb } from '../CanvasThumb'
import { Button } from '../ui/Button'
import { Dialog } from '../ui/Dialog'
import { TextArea } from '../ui/Input'
import { Notice } from '../ui/Notice'
import { ProgressBar } from '../ui/ProgressBar'
import { shareLive } from '@/lib/reviewPlacement'
import { cn } from '@/lib/utils'
import { listRowClass } from '../ui/listRow'
import { useDocumentStore } from '@/store/documentStore'
import { useReviewStore } from '@/store/reviewStore'
import { useUiStore } from '@/store/uiStore'
import { useWorkspaceStore } from '@/store/workspaceStore'
import type { ReviewShareSummary } from '@/lib/api'
import type { PanelObject } from '@/types/document'
import { expiryDate, failureText, rs, versionWhen } from './reviewText'

/** 链接已复制：通知轨轻提示一句（复制按钮自己的「已复制」另算） */
export const toastLinkCopied = () => useUiStore.getState().setStatus(msg('review.linkCopied', undefined, 'dialogs'), 'done')

/** 缩略图：单图编辑 = 那张图，排版 = 整页。喂的是内存里的对象，与画布列表同一份画法 */
function ShareThumb() {
  const doc = useDocumentStore((s) => s.doc)
  const mode = useWorkspaceStore((s) => s.mode)
  const activePanelId = useWorkspaceStore((s) => s.activePanelId)
  const panel =
    mode === 'fast_edit' ? (doc.objects.find((o) => o.id === activePanelId && o.type === 'panel') as PanelObject | undefined) : undefined
  const page = panel ? { w: panel.w, h: panel.h } : { w: doc.page.w, h: doc.page.h }
  const objects = panel ? [{ ...panel, x: 0, y: 0 }] : doc.objects
  return (
    <div className="h-20 w-28 shrink-0 overflow-hidden rounded-md bg-paper inset-ring inset-ring-border" data-review-thumb>
      <CanvasThumb page={page} objects={objects} className="size-full" />
    </div>
  )
}

function Caption({ children, ...rest }: { children: React.ReactNode } & React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span className="type-caption text-ink-3" {...rest}>
      {children}
    </span>
  )
}

/** 额度用满的选择卡：三张卡，没有错误色、不是失败 */
function Picker({ onClose }: { onClose: () => void }) {
  const s = useReviewStore()
  const [choosing, setChoosing] = useState(false)
  const isNew = s.picker === 'new'
  const canUpdateOther = isNew && s.others.length > 0
  const cardClass = cn(listRowClass({ size: 'md' }), 'w-full flex-col items-start justify-center gap-0.5 px-3 text-left')
  if (choosing) {
    return (
      <div className="flex flex-col gap-1" data-review-picker-list>
        <p className="type-caption text-ink-3">{rs('picker.pickOne')}</p>
        {s.others.map((o: ReviewShareSummary) => (
          <button
            key={o.share_id}
            type="button"
            className={cardClass}
            data-review-pick-share={o.share_id}
            onClick={() => void s.updateOther(o.share_id)}
          >
            <b className="text-sm font-medium text-ink">{o.title}</b>
            <span className="type-caption text-ink-3">{rs('sharedMeta', { v: o.latest_version, when: versionWhen(o.updated_at) })}</span>
          </button>
        ))}
      </div>
    )
  }
  return (
    <div className="flex flex-col gap-1" data-review-picker={s.picker}>
      {canUpdateOther && (
        <button
          type="button"
          className={cardClass}
          data-review-pick="update-existing"
          onClick={() => (s.others.length === 1 ? void s.updateOther(s.others[0].share_id) : setChoosing(true))}
        >
          <b className="text-sm font-medium text-ink">{rs('picker.updateExisting')}</b>
          <span className="type-caption text-ink-3">
            {s.others.length === 1 ? rs('picker.updateOne', { title: s.others[0].title }) : rs('picker.updateExistingDesc')}
          </span>
        </button>
      )}
      <button
        type="button"
        className={cardClass}
        data-review-pick="export-png"
        onClick={() => {
          onClose()
          useUiStore.getState().setExportOpen(true)
        }}
      >
        <b className="text-sm font-medium text-ink">{rs('picker.exportPng')}</b>
        <span className="type-caption text-ink-3">{rs('picker.exportPngDesc')}</span>
      </button>
      <button type="button" className={cardClass} data-review-pick="later" onClick={onClose}>
        <b className="text-sm font-medium text-ink">{rs(isNew ? 'picker.laterNew' : 'picker.laterUpdate')}</b>
        <span className="type-caption text-ink-3">{rs(isNew ? 'picker.laterNewDesc' : 'picker.laterUpdateDesc')}</span>
      </button>
    </div>
  )
}

/**
 * 「分享审阅」弹窗（ADR 0187 §八）：三态——首次 / 已分享 / 额度用满（选择卡）。进行中用点阵进度，失败按失败码一句话 + 重试。
 * 唯一挂载点 `ReviewHost`。剩余次数只在页脚一行小字写；联网不通（数字是 null）就不写。
 */
export function ReviewDialog() {
  useTranslation('dialogs')
  const s = useReviewStore()
  const noteId = useId()
  const target = s.target
  const mode = useWorkspaceStore((st) => st.mode)
  const [note, setNote] = useState('')
  const [confirmStop, setConfirmStop] = useState(false)
  const docName = useDocumentStore((st) => st.canvases.find((c) => c.id === st.activeCanvasId)?.name)
  const activePanelId = useWorkspaceStore((st) => st.activePanelId)
  const panelName = useDocumentStore((st) => {
    const o = st.doc.objects.find((x) => x.id === activePanelId)
    return o?.type === 'panel' ? (o.name ?? o.fileId.split('/').pop()?.replace(/\.[^.]+$/, '')) : undefined
  })
  const title = (mode === 'fast_edit' ? panelName : docName) ?? target?.id ?? ''

  useEffect(() => {
    if (!s.dialogOpen) {
      setConfirmStop(false)
      setNote('')
    }
  }, [s.dialogOpen])

  const share = s.share && shareLive(s.share) ? s.share : null
  const busy = s.stage === 'rendering' || s.stage === 'uploading'
  const close = () => s.closeDialog()

  // 自动复制：一次生成 / 更新刚做完（done）→ 复制链接并在通知轨说一句
  useEffect(() => {
    if (s.stage !== 'done' || !s.share) return
    void navigator.clipboard?.writeText(s.share.review_url).then(toastLinkCopied, () => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [s.stage])

  let status: React.ReactNode = null
  if (busy) {
    status = (
      <div className="flex flex-col gap-1.5" data-review-status="working">
        <p className="text-sm text-ink-2">{rs(`stage.${s.stage}`)}</p>
        <ProgressBar pct={null} label={rs(`stage.${s.stage}`)} />
      </div>
    )
  } else if (s.stage === 'failed' && s.failure) {
    status = (
      <Notice tone="danger" data-review-status="failed">
        <span data-review-failure={s.failure.code}>{failureText(s.failure.code)}</span>
      </Notice>
    )
  }

  const retry = s.stage === 'failed' && s.failure?.retryable ? (
    <Button variant="primary" size="lg" onClick={() => void s.retry()} data-review-retry>
      {rs('retry')}
    </Button>
  ) : null

  let body: React.ReactNode
  let footer
  if (s.picker) {
    body = <Picker onClose={close} />
    footer = { secondary: <Button variant="ghost" size="lg" onClick={close}>{rs('close')}</Button> }
  } else if (share) {
    body = (
      <div className="flex flex-col gap-3">
        <div className="flex items-center justify-between gap-2 rounded-lg bg-surface-hover px-3 py-2" data-review-link>
          <span className="min-w-0 truncate font-mono text-xs text-ink-2" title={share.review_url} aria-label={rs('linkLabel')}>
            {share.review_url.replace(/^https?:\/\//, '')}
          </span>
          <CopyButton text={share.review_url} label={rs('copy')} variant="secondary" feedback="toast" onCopied={toastLinkCopied} />
        </div>
        <div className="flex items-center gap-3">
          <ShareThumb />
          <div className="flex min-w-0 flex-col gap-0.5">
            <b className="text-sm font-medium text-ink">
              {rs('sharedMeta', { v: share.latest_version, when: versionWhen(share.updated_at) })}
            </b>
            <Caption data-review-stats>
              {rs('sharedStats', { count: share.comment_count, open: share.open_count, date: expiryDate(share.expires_at) })}
            </Caption>
          </div>
        </div>
      </div>
    )
    footer = {
      start: (
        <Button
          variant={confirmStop ? 'danger-tinted' : 'danger'}
          size="lg"
          disabled={busy}
          onClick={() => (confirmStop ? void s.revoke() : setConfirmStop(true))}
          data-review-stop
        >
          {confirmStop ? rs('stopConfirm') : rs('stop')}
        </Button>
      ),
      secondary: s.quota.update_left !== null ? <Caption data-review-quota>{rs('quotaUpdate', { n: s.quota.update_left })}</Caption> : undefined,
      primary:
        retry ?? (
          <Button variant="primary" size="lg" loading={busy} disabled={busy} onClick={() => void s.update(note || undefined)} data-review-update>
            {rs('update')}
          </Button>
        ),
    }
  } else {
    body = (
      <div className="flex flex-col gap-3">
        <div className="flex items-center gap-3">
          <ShareThumb />
          <div className="flex min-w-0 flex-col gap-0.5">
            <b className="truncate text-sm font-medium text-ink">{title}</b>
            <Caption>{rs(target?.kind === 'layout' ? 'privacyLayout' : 'privacyFigure')}</Caption>
          </div>
        </div>
        <label id={`${noteId}-l`} htmlFor={noteId} className="type-caption text-ink-3">
          {rs('noteLabel')}
        </label>
        <TextArea id={noteId} aria-labelledby={`${noteId}-l`} rows={2} maxLength={200} value={note} disabled={busy} onChange={(e) => setNote(e.target.value)} data-review-note />
      </div>
    )
    footer = {
      start: s.quota.new_left !== null ? <Caption data-review-quota>{rs('quotaNew', { n: s.quota.new_left })}</Caption> : undefined,
      secondary: (
        <Button variant="ghost" size="lg" disabled={busy} onClick={close}>
          {rs('cancel')}
        </Button>
      ),
      primary:
        retry ?? (
          <Button variant="primary" size="lg" loading={busy} disabled={busy || !target} onClick={() => void s.create(title, note.trim())} data-review-create>
            {rs('create')}
          </Button>
        ),
    }
  }

  return (
    <Dialog
      open={s.dialogOpen}
      onOpenChange={(o) => (o ? undefined : close())}
      title={s.picker ? rs(s.picker === 'new' ? 'picker.newTitle' : 'picker.updateTitle') : rs('title')}
      size="md"
      anchor="review"
      blockDismiss={busy}
      status={s.picker ? null : status}
      footer={footer}
    >
      <div className={cn('flex flex-col gap-3')}>{body}</div>
    </Dialog>
  )
}
