import { useEffect, useId, useRef, useState, type DragEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { formatDateTime } from '@/i18n/format'
import { FEEDBACK_EMAIL } from '@/lib/brand'
import { cn } from '@/lib/utils'
import { draftText, useContactStore, validEmail, type ContactFailure } from '@/store/contactStore'
import { Button, IconButton } from '../ui/Button'
import { Checkbox } from '../ui/Checkbox'
import { Details, Summary } from '../ui/Details'
import { Dialog } from '../ui/Dialog'
import { dropZoneClass } from '../ui/dropZone'
import { FieldGroup } from '../ui/FormSection'
import { TextArea, TextInput } from '../ui/Input'
import { Notice } from '../ui/Notice'
import { ProgressBar } from '../ui/ProgressBar'
import { X } from '../ui/icons'
import { ICON_SIZE } from '../ui/Icon'
import { CopyButton } from '../settings/CopyButton'
import { ReplyPaper } from './ReplyPaper'

const cs = (key: string, values?: Record<string, unknown>) =>
  translate(`contact.${key}`, { ns: 'dialogs', ...(values ?? {}) })

function formatBytes(n: number): string {
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MiB`
  if (n >= 1024) return `${Math.max(1, Math.round(n / 1024))} KiB`
  return `${n} B`
}

/** 「请在 N 分钟 / 小时后再试」；没有 Retry-After 就不说。 */
function retryText(seconds: number | null | undefined): string | null {
  if (!seconds || seconds <= 0) return null
  if (seconds >= 3600) return cs('retryInHours', { n: Math.ceil(seconds / 3600) })
  return cs('retryInMinutes', { n: Math.max(1, Math.ceil(seconds / 60)) })
}

/** 失败的人话：引擎发信码 / 诊断包码 / 本机输入校验码三个出处各查各的表，查不到按「意外结果」说。 */
function failureText(f: ContactFailure): string {
  if (f.source === 'local') return cs(`error.${f.code}`, { defaultValue: cs('failure.unexpected_response') })
  if (f.source === 'diag') {
    const reason = translate(`contact.diagFailureCode.${f.code}`, {
      ns: 'dialogs',
      defaultValue: translate(`settings.diagnostics.send.failure.${f.code}`, {
        ns: 'dialogs',
        defaultValue: translate('settings.diagnostics.send.failure.unexpected_response', { ns: 'dialogs' }),
      }),
    })
    return cs('diagFailure', { reason })
  }
  return cs(`failure.${f.code}`, { defaultValue: cs('failure.unexpected_response') })
}

/** 「我的来信」里用户自己写的那一份：灰色引用块；正文长了折叠成前 4 行 +「展开」。 */
function YourLetter({ subject, body, names }: { subject: string; body: string; names: string[] }) {
  const [open, setOpen] = useState(false)
  if (!subject && !body && names.length === 0) return null // 旧记录没存原文
  const long = body.split('\n').length > 4 || body.length > 240
  return (
    <blockquote
      className="flex flex-col gap-1 border-l-2 border-border pl-3 text-sm text-ink-2"
      data-contact-letter-yours
    >
      <span className="type-caption">{cs('lettersYouWrote')}</span>
      {subject && <span className="break-words font-medium" data-contact-letter-subject>{subject}</span>}
      {body && (
        <p
          className={cn('whitespace-pre-wrap break-words', long && !open && 'line-clamp-4')}
          data-contact-letter-body
        >
          {body}
        </p>
      )}
      {long && (
        <Button
          variant="ghost"
          size="sm"
          className="self-start"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          data-contact-letter-toggle
        >
          {open ? cs('lettersCollapse') : cs('lettersExpand')}
        </Button>
      )}
      {names.length > 0 && (
        <span className="type-caption break-words" data-contact-letter-files>
          {cs('lettersAttachments', { names: names.join('、') })}
        </span>
      )}
    </blockquote>
  )
}

/**
 * 失败卡里的复制动作：按钮上写明复制什么（「复制内容」「复制地址」），复制后自己说「已复制」两秒。
 * 剪贴板不可用时保持原样——用户仍可从旁边的文字手工选中。这里只写剪贴板，不调起任何别的程序。
 */
function CopyAction({ text, label }: { text: string | (() => string); label: string }) {
  const [copied, setCopied] = useState(false)
  useEffect(() => {
    if (!copied) return
    const id = window.setTimeout(() => setCopied(false), 2000)
    return () => window.clearTimeout(id)
  }, [copied])
  return (
    <Button
      variant="secondary"
      size="md"
      onClick={() => {
        const value = typeof text === 'function' ? text() : text
        void navigator.clipboard?.writeText(value).then(
          () => setCopied(true),
          () => {},
        )
      }}
      data-contact-copy
    >
      {copied ? cs('copied') : label}
    </Button>
  )
}

/**
 * 「联系作者」（ADR 0145）：在应用里给作者写信。**不需要登录、不需要配置自己的邮箱**——由 Tavotto 服务器代发；
 * 全产品没有邮件协议链接，失败时只有「复制内容 / 复制地址」。
 *
 * 四个状态：编辑 → 发送中（可取消、显示上传进度）→ 已发送（编号 + 回信去向）/ 失败（草稿保留：再试一次 / 复制内容 / 复制地址）。
 * 草稿在 `contactStore` 里，关窗不丢；「我的来信」是同一个窗里的另一页（看过即已读）。
 * 状态区住在对话框的 `status` 槽里，不推挤表单；页脚两颗按钮始终在原位只换字 / disabled（#797 纪律）。
 */
export function ContactDialog() {
  useTranslation('dialogs')
  const s = useContactStore()
  const { capability, draft, phase, view } = s
  const subjectId = useId()
  const bodyId = useId()
  const emailId = useId()
  const fileInput = useRef<HTMLInputElement | null>(null)
  const [dragging, setDragging] = useState(false)
  if (!capability) return null

  const busy = phase === 'sending' || phase === 'cancelling' || phase === 'diag'
  const editable = !busy && phase !== 'done'
  const recipient = capability.recipient ?? FEEDBACK_EMAIL
  const bodyMax = capability.body_max ?? 5000
  const subjectMax = capability.subject_max ?? 120
  const maxFiles = capability.max_attachments ?? 5
  const maxTotal = capability.max_total_bytes ?? 20 * 1024 * 1024
  const progress = s.status && s.status.total ? Math.round(((s.status.sent ?? 0) / s.status.total) * 100) : null
  const stageKey =
    phase === 'diag'
      ? 'diag'
      : phase === 'cancelling'
        ? 'cancelling'
        : (s.status?.stage ?? 'init')

  const dropHandlers = {
    onDragEnter: (e: DragEvent) => {
      if (editable && e.dataTransfer.types.includes('Files')) setDragging(true)
    },
    onDragOver: (e: DragEvent) => {
      if (!editable || !e.dataTransfer.types.includes('Files')) return
      e.preventDefault()
      e.dataTransfer.dropEffect = 'copy'
    },
    onDragLeave: (e: DragEvent) => {
      if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragging(false)
    },
    onDrop: (e: DragEvent) => {
      if (!e.dataTransfer.types.includes('Files')) return
      e.preventDefault()
      setDragging(false)
      if (editable) s.addFiles([...e.dataTransfer.files])
    },
  }

  const kinds = [...new Set((s.diag.prepared?.entries ?? []).map((e) => e.kind))]

  // 状态区：发送中 / 已发送 / 失败各一块，常驻在页脚上方（不随正文滚）
  let statusNode = null
  if (busy) {
    statusNode = (
      <div className="flex flex-col gap-1.5" data-contact-status="sending">
        <p className="text-sm text-ink-2">
          {cs(`stage.${stageKey}`, { pct: Math.min(100, Math.max(0, progress ?? 0)) })}
        </p>
        <ProgressBar
          pct={stageKey === 'upload' && progress !== null ? progress : null}
          label={cs(`stage.${stageKey}`, { pct: progress ?? 0 })}
        />
      </div>
    )
  } else if (phase === 'done') {
    statusNode = (
      <Notice tone="ok" title={cs('sentTitle')} data-contact-status="done">
        <span className="flex flex-col gap-1">
          <span className="flex flex-wrap items-center gap-x-2">
            <span data-contact-message-id>{cs('sentNumber', { id: s.messageId ?? '' })}</span>
            {s.messageId && <CopyButton text={s.messageId} label={cs('sentCopyNumber')} appearance="icon" />}
          </span>
          <span data-contact-inbox-missing={s.inboxMissing ? '' : undefined}>
            {cs(s.inboxMissing ? (draft.replyEmail.trim() ? 'sentNoInboxEmail' : 'sentNoInboxNoEmail') : 'sentReplyHere')}
          </span>
        </span>
      </Notice>
    )
  } else if (phase === 'failed' && s.failure) {
    const wait = s.failure.retryable ? retryText(s.failure.retryAfter) : null
    statusNode = (
      <Notice tone="danger" title={cs('failedTitle')} data-contact-status="failed">
        <span className="flex flex-col gap-1.5">
          <span data-contact-failure={s.failure.code}>
            {failureText(s.failure)} {wait}
          </span>
          {s.failure.source !== 'local' && <span>{cs('failedKept')}</span>}
          {/* 没有邮件协议链接、也不唤起邮件程序：只给「复制内容 / 复制地址」，由用户自己决定怎么发 */}
          <span className="flex flex-wrap items-center gap-1.5" data-contact-fallback>
            <CopyAction text={() => draftText(draft)} label={cs('copyContent')} />
            <CopyAction text={recipient} label={cs('copyAddress')} />
            <span className="type-caption">{cs('failedFallback', { address: recipient })}</span>
          </span>
        </span>
      </Notice>
    )
  } else if (s.formError) {
    statusNode = (
      <p className="text-sm text-danger-content" role="alert" data-contact-form-error={s.formError}>
        {cs(`error.${s.formError}`)}
      </p>
    )
  } else if (s.status?.state === 'cancelled' || (phase === 'edit' && s.status?.state === 'idle')) {
    statusNode = <p className="type-caption">{cs('cancelledNote')}</p>
  }

  const writeView = (
    <div className="flex flex-col gap-4" data-contact-body>
      <div className="flex items-center gap-2 text-sm">
        <span className="type-section">{cs('toLabel')}</span>
        <span className="text-ink-2" data-contact-recipient>
          {recipient}
        </span>
      </div>

      <div className="flex flex-col gap-1.5">
        <label htmlFor={subjectId} className="type-section">
          {cs('subjectLabel')}
        </label>
        <TextInput
          id={subjectId}
          value={draft.subject}
          maxLength={subjectMax}
          placeholder={cs('subjectPlaceholder')}
          disabled={!editable}
          onChange={(e) => s.setDraft({ subject: e.target.value })}
          data-contact-subject
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <label htmlFor={bodyId} className="type-section">
          {cs('bodyLabel')}
        </label>
        <TextArea
          id={bodyId}
          aria-label={cs('bodyLabel')}
          value={draft.body}
          rows={6}
          maxRows={12}
          maxLength={bodyMax}
          placeholder={cs('bodyPlaceholder')}
          disabled={!editable}
          onChange={(e) => s.setDraft({ body: e.target.value })}
          data-contact-text
          className="px-3 py-2"
        />
        <p className="flex justify-end type-caption tabular-nums">
          {cs('bodyCount', { count: draft.body.length, max: bodyMax })}
        </p>
      </div>

      <div className="flex flex-col gap-1.5" data-contact-attachments>
        <span className="type-section">{cs('attachLabel')}</span>
        <div
          {...dropHandlers}
          data-contact-drop
          className={cn(
            'flex flex-col gap-2 rounded-md bg-surface-hover px-3 py-2.5',
            dropZoneClass(dragging),
          )}
        >
          <p className="type-caption">{cs('attachHint', { max: maxFiles, size: formatBytes(maxTotal) })}</p>
          {draft.files.length > 0 && (
            <ul className="flex flex-col gap-1" data-contact-file-list>
              {draft.files.map((f, i) => (
                <li key={`${f.name}-${f.size}`} className="flex items-center gap-2 text-sm" data-contact-file={f.name}>
                  <span className="min-w-0 flex-1 truncate text-ink">{f.name}</span>
                  <span className="shrink-0 text-xs text-ink-3">{formatBytes(f.size)}</span>
                  <IconButton
                    label={cs('attachRemove', { name: f.name })}
                    disabled={!editable}
                    onClick={() => s.removeFile(i)}
                    data-contact-file-remove
                  >
                    <X size={ICON_SIZE.sm} />
                  </IconButton>
                </li>
              ))}
            </ul>
          )}
          <div className="flex items-center gap-2">
            <Button
              variant="secondary"
              size="md"
              disabled={!editable || draft.files.length >= maxFiles}
              onClick={() => fileInput.current?.click()}
              data-contact-pick
            >
              {cs('attachPick')}
            </Button>
            {s.fileError && (
              <span className="type-caption text-danger-content" role="alert" data-contact-file-error={s.fileError}>
                {cs(`fileError.${s.fileError}`)}
              </span>
            )}
          </div>
          <input
            ref={fileInput}
            type="file"
            multiple
            hidden
            accept={(capability.extensions ?? []).join(',')}
            onChange={(e) => {
              s.addFiles([...(e.target.files ?? [])])
              e.target.value = ''
            }}
            data-contact-file-input
          />
        </div>
      </div>

      <FieldGroup>
        <div className="flex flex-col gap-1 px-3 py-2">
          <label className="flex items-start gap-2 text-sm text-ink">
            <Checkbox
              checked={draft.attachDiag}
              disabled={!editable}
              onChange={(e) => s.setAttachDiag(e.target.checked)}
              data-contact-diag
              className="mt-0.5"
            />
            <span className="flex min-w-0 flex-col">
              <span>{cs('diagLabel')}</span>
              <span className="type-caption">{cs('diagHint')}</span>
            </span>
          </label>
          {draft.attachDiag && (
            <Details data-contact-diag-details className="pl-6 text-sm">
              <Summary className="h-8 text-ink-2 hover:text-ink">
                <span className="flex min-w-0 flex-1 items-center gap-1.5">
                  <span data-contact-diag-state={s.diag.state}>
                    {s.diag.state === 'preparing' && cs('diagPreparing')}
                    {s.diag.state === 'ready' && cs('diagReady', { size: formatBytes(s.diag.prepared?.size ?? 0) })}
                    {s.diag.state === 'failed' && cs('diagFailedPrepare')}
                    {s.diag.state === 'tooLarge' && cs('diagTooLarge')}
                  </span>
                  <span className="ml-auto text-xs text-ink-3">{cs('diagContents')}</span>
                </span>
              </Summary>
              <div className="flex flex-col gap-1 pb-2">
                <h3 className="type-section">{cs('diagKindsTitle')}</h3>
                <ul className="flex list-disc flex-col gap-0.5 pl-4 text-xs leading-5 text-ink-2" data-contact-diag-kinds>
                  {kinds.map((k) => (
                    <li key={k} data-diag-kind={k}>
                      {translate(`settings.diagnostics.send.kind.${k}`, {
                        ns: 'dialogs',
                        defaultValue: translate('settings.diagnostics.send.kind.other', { ns: 'dialogs' }),
                      })}
                    </li>
                  ))}
                </ul>
              </div>
            </Details>
          )}
        </div>
      </FieldGroup>

      <div className="flex flex-col gap-1.5">
        <label htmlFor={emailId} className="type-section">
          {cs('emailLabel')}
        </label>
        <TextInput
          id={emailId}
          type="email"
          inputMode="email"
          autoComplete="email"
          value={draft.replyEmail}
          maxLength={capability.email_max ?? 254}
          placeholder={cs('emailPlaceholder')}
          disabled={!editable}
          onChange={(e) => s.setDraft({ replyEmail: e.target.value })}
          data-contact-email
        />
        {draft.replyEmail.trim() !== '' && !validEmail(draft.replyEmail.trim()) && (
          <p className="type-caption text-danger-content" data-contact-email-hint>
            {cs('error.emailInvalid')}
          </p>
        )}
        <p className="type-caption" data-contact-email-note>
          {cs('emailNote')}
        </p>
        {s.rememberedEmail && (
          <p className="flex items-center gap-1 type-caption" data-contact-email-remembered>
            {cs('emailRemembered')}
            <Button variant="ghost" size="sm" onClick={s.forgetEmail} disabled={!editable} data-contact-email-forget>
              {cs('emailForget')}
            </Button>
          </p>
        )}
      </div>
    </div>
  )

  const lettersView = (
    <div className="flex flex-col gap-3" data-contact-letters>
      <div className="flex items-center justify-between gap-2">
        <p className="type-caption">{cs('lettersRetention', { days: capability.retention_days ?? 365 })}</p>
        <Button
          variant="secondary"
          size="md"
          loading={s.checking}
          loadingLabel={cs('lettersChecking')}
          onClick={() => void s.checkReplies()}
          data-contact-check
        >
          {cs('lettersCheck')}
        </Button>
      </div>
      {s.messagesLoaded && s.messages.length === 0 && (
        <p className="text-sm text-ink-3" data-contact-letters-empty>
          {cs('lettersEmpty')}
        </p>
      )}
      <ul className="flex flex-col gap-3">
        {s.messages.map((m) => (
          <li key={m.message_id} className="flex flex-col gap-2 rounded-md bg-surface-hover px-3 py-2.5" data-contact-letter={m.message_id}>
            <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-sm">
              <span className="font-medium text-ink">{cs('lettersNumber', { id: m.message_id })}</span>
              <span className="type-caption">{cs('lettersSentAt', { time: formatDateTime(m.sent_at * 1000) })}</span>
              <span className="type-caption">· {cs(`lettersState.${m.state}`)}</span>
              <Button
                variant="ghost"
                size="sm"
                className="ml-auto"
                onClick={() => void s.forget(m.message_id)}
                data-contact-letter-forget
              >
                {cs('lettersForget')}
              </Button>
            </div>
            <YourLetter subject={m.subject ?? ''} body={m.body ?? ''} names={m.attachment_names ?? []} />
            {m.poll_error && (
              <p className="type-caption" data-contact-poll-error={m.poll_error}>
                {cs(m.poll_error === 'too_large' ? 'pollTooLarge' : 'pollBad')}
              </p>
            )}
            {m.replies.map((r) => (
              <div key={r.id} className="flex flex-col gap-1 rounded-md bg-surface px-3 py-2" data-contact-reply={r.id}>
                <div className="flex items-center gap-2 type-caption">
                  <span>{cs('replyFrom')}</span>
                  <span>{formatDateTime(Date.parse(r.created_at) || 0)}</span>
                  {s.freshIds.includes(r.id) && (
                    <span className="rounded-full bg-accent-subtle px-1.5 text-accent" data-contact-reply-new>
                      {cs('replyNew')}
                    </span>
                  )}
                </div>
                {r.has_html ? (
                  <ReplyPaper messageId={m.message_id} replyId={r.id} fallback={r.body} />
                ) : (
                  <p className="whitespace-pre-wrap break-words text-sm text-ink">{r.body}</p>
                )}
              </div>
            ))}
          </li>
        ))}
      </ul>
    </div>
  )

  const unreadBadge = s.unread > 0 ? ` (${s.unread})` : ''

  return (
    <Dialog
      open={s.open}
      onOpenChange={(o) => s.setOpen(o)}
      title={view === 'letters' ? cs('letters') : cs('title')}
      description={<span className="block text-sm leading-5">{cs('description')}</span>}
      size="md"
      anchor="contact"
      blockDismiss={busy}
      onEscape={busy ? () => void s.cancel() : undefined}
      status={view === 'write' ? statusNode : null}
      footer={{
        start: (
          <Button
            variant="ghost"
            size="lg"
            disabled={busy}
            onClick={() => s.setView(view === 'write' ? 'letters' : 'write')}
            data-contact-switch-view
          >
            {view === 'write' ? `${cs('letters')}${unreadBadge}` : cs('write')}
          </Button>
        ),
        secondary: busy ? (
          <Button
            variant="ghost"
            size="lg"
            onClick={() => void s.cancel()}
            disabled={phase === 'cancelling'}
            data-contact-cancel
          >
            {cs('cancelSending')}
          </Button>
        ) : phase === 'done' ? (
          <Button variant="ghost" size="lg" onClick={s.reset} data-contact-another>
            {cs('sentAnother')}
          </Button>
        ) : view === 'letters' ? null : ( // 「我的来信」只有主按钮「关闭」，不再并排两个「关闭」
          <Button variant="ghost" size="lg" onClick={() => s.setOpen(false)} data-contact-dismiss>
            {cs('close')}
          </Button>
        ),
        primary:
          view === 'letters' ? (
            <Button variant="primary" size="lg" onClick={() => s.setOpen(false)} data-contact-close>
              {cs('close')}
            </Button>
          ) : phase === 'done' ? (
            <Button variant="primary" size="lg" onClick={() => s.setOpen(false)} data-contact-close>
              {cs('done')}
            </Button>
          ) : (
            <Button
              variant="primary"
              size="lg"
              loading={busy}
              disabled={busy || (draft.attachDiag && (s.diag.state === 'preparing' || s.diag.state === 'failed' || s.diag.state === 'tooLarge'))}
              onClick={() => void s.send()}
              data-contact-send
            >
              {phase === 'failed' ? cs('retry') : cs('send')}
            </Button>
          ),
      }}
    >
      {view === 'write' ? writeView : lettersView}
    </Dialog>
  )
}
