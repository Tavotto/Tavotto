import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { msg } from '@/i18n/message'
import { CanvasThumb } from '../CanvasThumb'
import { Button } from '../ui/Button'
import { Dialog } from '../ui/Dialog'
import { TextInput } from '../ui/Input'
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
import { expiryShort, failureText, rs, versionWhen } from './reviewText'

/** 链接已复制：通知轨轻提示一句（复制按钮自己的「已复制」另算） */
export const toastLinkCopied = () => useUiStore.getState().setStatus(msg('review.linkCopied', undefined, 'dialogs'), 'done')

/** 大预览图：单图编辑 = 那张图，排版 = 整页（按它自己的宽高比，纸色底）。喂的是内存里的对象，与画布列表同一份画法 */
function ShareThumb() {
  const doc = useDocumentStore((s) => s.doc)
  const mode = useWorkspaceStore((s) => s.mode)
  const activePanelId = useWorkspaceStore((s) => s.activePanelId)
  const panel =
    mode === 'fast_edit' ? (doc.objects.find((o) => o.id === activePanelId && o.type === 'panel') as PanelObject | undefined) : undefined
  const page = panel ? { w: panel.w, h: panel.h } : { w: doc.page.w, h: doc.page.h }
  const objects = panel ? [{ ...panel, x: 0, y: 0 }] : doc.objects
  const ratio = page.w > 0 && page.h > 0 ? page.w / page.h : 1.5
  return (
    <div className="flex justify-center rounded-lg bg-surface-hover p-3" data-review-thumb>
      <div className="max-h-72 w-full overflow-hidden bg-paper" style={{ aspectRatio: String(ratio), maxWidth: `${ratio * 18}rem` }}>
        <CanvasThumb page={page} objects={objects} className="size-full" flush pxWidth={720} />
      </div>
    </div>
  )
}

/** 额度用满：不放图，一句话 + 「导出 PNG」次按钮 + 「更新已有的链接（X）」主按钮；多个可更新的链接时主按钮打开一个简单列表 */
function Picker({ onClose }: { onClose: () => void }) {
  const s = useReviewStore()
  const [choosing, setChoosing] = useState(false)
  const rowClass = cn(listRowClass({ size: 'md' }), 'w-full flex-col items-start justify-center gap-0.5 px-3 text-left')
  if (choosing) {
    return (
      <div className="flex flex-col gap-1" data-review-picker-list>
        {s.others.map((o: ReviewShareSummary) => (
          <button key={o.share_id} type="button" className={rowClass} data-review-pick-share={o.share_id} onClick={() => void s.updateOther(o.share_id)}>
            <b className="text-sm font-medium text-ink">{o.title}</b>
            <span className="type-caption text-ink-3">{versionWhen(o.updated_at)}</span>
          </button>
        ))}
      </div>
    )
  }
  return (
    <div className="flex flex-col gap-4" data-review-picker="new">
      <p className="text-sm text-ink-2">{rs('picker.desc')}</p>
      <div className="flex justify-end gap-2">
        <Button
          variant="secondary"
          size="lg"
          data-review-pick="export-png"
          onClick={() => {
            onClose()
            useUiStore.getState().setExportOpen(true)
          }}
        >
          {rs('picker.exportPng')}
        </Button>
        {s.others.length > 0 && (
          <Button
            variant="primary"
            size="lg"
            data-review-pick="update-existing"
            onClick={() => (s.others.length === 1 ? void s.updateOther(s.others[0].share_id) : setChoosing(true))}
          >
            {s.others.length === 1 ? rs('picker.updateOne', { title: s.others[0].title }) : rs('picker.updateMany')}
          </Button>
        )}
      </div>
    </div>
  )
}

/**
 * 「分享审阅」弹窗（ADR 0187 §八，第 4 轮设计）：四态。已分享有改动 / 已分享没改动 / 首次 / 新建用完（不放图）。
 * 没有版本号、没有意见数、没有剩余次数；进行中用点阵进度，失败按失败码一句话 + 重试。唯一挂载点 `ReviewHost`。
 */
export function ReviewDialog() {
  useTranslation('dialogs')
  const s = useReviewStore()
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
        <span className="flex items-center justify-between gap-2">
          <span data-review-failure={s.failure.code}>{failureText(s.failure.code)}</span>
          {s.failure.retryable && (
            <Button variant="secondary" size="sm" onClick={() => void s.retry()} data-review-retry>
              {rs('retry')}
            </Button>
          )}
        </span>
      </Notice>
    )
  }

  const quiet = 'type-caption text-ink-3'
  let body: React.ReactNode
  if (s.picker) {
    body = <Picker onClose={close} />
  } else if (share) {
    body = (
      <div className="flex flex-col gap-3">
        <ShareThumb />
        <div className="flex items-center gap-2" data-review-link>
          <div className="flex h-8 min-w-0 flex-1 items-center rounded-full bg-surface-hover px-3">
            <span className="min-w-0 truncate font-mono text-xs text-ink-2" title={share.review_url} aria-label={rs('linkLabel')}>
              {share.review_url.replace(/^https?:\/\//, '')}
            </span>
          </div>
          <Button
            variant="primary"
            size="lg"
            data-review-copy
            onClick={() => void navigator.clipboard?.writeText(share.review_url).then(toastLinkCopied, () => {})}
          >
            {rs('copyLink')}
          </Button>
        </div>
        {s.changed && (
          <div className="flex items-center justify-between gap-2 border-t border-border pt-3" data-review-changed>
            <span className="flex items-center gap-1.5 text-sm text-ink-2">
              <span aria-hidden className="size-1.5 rounded-full bg-accent" />
              {rs('changed')}
            </span>
            <Button variant="secondary" size="md" loading={busy} disabled={busy} onClick={() => void s.update()} data-review-update>
              {rs('updateHere')}
            </Button>
          </div>
        )}
        <div className="flex items-center justify-between gap-2">
          <span className={quiet} data-review-stats>
            {rs('shareNote', { date: expiryShort(share.expires_at) })}
          </span>
          <button
            type="button"
            className={cn(quiet, 'rounded-sm outline-none hover:text-ink focus-visible:focus-ring', confirmStop && 'text-danger-content')}
            disabled={busy}
            onClick={() => (confirmStop ? void s.revoke() : setConfirmStop(true))}
            data-review-stop
          >
            {confirmStop ? rs('stopConfirm') : rs('stop')}
          </button>
        </div>
      </div>
    )
  } else {
    body = (
      <div className="flex flex-col gap-3">
        <ShareThumb />
        <div className="flex items-center gap-2">
          <TextInput
            value={note}
            maxLength={200}
            disabled={busy}
            placeholder={rs('notePlaceholder')}
            aria-label={rs('notePlaceholder')}
            onChange={(e) => setNote(e.target.value)}
            className="min-w-0 flex-1"
            data-review-note
          />
          <Button variant="primary" size="lg" loading={busy} disabled={busy || !target} onClick={() => void s.create(title, note.trim())} data-review-create>
            {rs('create')}
          </Button>
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className={quiet}>{rs('privacyFoot')}</span>
          <span className={quiet} data-review-quota>
            {rs('dailyNew')}
          </span>
        </div>
      </div>
    )
  }

  return (
    <Dialog
      open={s.dialogOpen}
      onOpenChange={(o) => (o ? undefined : close())}
      title={
        s.picker ? (
          rs('picker.newTitle')
        ) : (
          <span className="flex items-baseline gap-2">
            {rs('title')}
            <span className="truncate text-sm font-normal text-ink-3">{title}</span>
          </span>
        )
      }
      size="md"
      anchor="review"
      blockDismiss={busy}
      status={status}
    >
      {body}
    </Dialog>
  )
}
