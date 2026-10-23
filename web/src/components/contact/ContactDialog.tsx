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
import { FieldGroup } from '../ui/FormSection'
import { TextArea, TextInput } from '../ui/Input'
import { Notice } from '../ui/Notice'
import { Popover } from '../ui/Popover'
import { ProgressBar } from '../ui/ProgressBar'
import { Check, File as FileIcon, Plus, Trash2, X } from '../ui/icons'
import { useUiStore } from '@/store/uiStore'
import { msg } from '@/i18n/message'
import { ICON_SIZE } from '../ui/Icon'
import { CopyButton } from '../settings/CopyButton'
import { WeComQrImage, useWeComContactVisible } from '../settings/WeComContact'
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

/** 附件方块边长（px）。 */
const TILE = 88

/** 图片缩略图：object URL 随文件挂载、卸载即 revoke；环境没有 createObjectURL（测试 / 极老内核）就退回类型方块。 */
function Thumb({ file }: { file: File }) {
  const [url, setUrl] = useState<string | null>(null)
  useEffect(() => {
    if (typeof URL === 'undefined' || typeof URL.createObjectURL !== 'function') return
    const u = URL.createObjectURL(file)
    setUrl(u)
    return () => {
      URL.revokeObjectURL(u)
      setUrl(null)
    }
  }, [file])
  if (!url) return null
  return <img src={url} alt="" className="size-full object-cover" draggable={false} data-contact-file-thumb />
}

function extOf(name: string): string {
  const i = name.lastIndexOf('.')
  return i >= 0 ? name.slice(i + 1, i + 6).toUpperCase() : ''
}

/** 一个已添加的附件：88 见方；图片是缩略图，其它是文件图标 + 下方的扩展名徽标；文件名在下面截断；右上角 × 删除。 */
function AttachTile({ file, onRemove, disabled }: { file: File; onRemove: () => void; disabled: boolean }) {
  const isImage = file.type.startsWith('image/')
  return (
    <li className="group relative flex flex-col gap-1" style={{ width: TILE }} data-contact-file={file.name}>
      <div
        className="relative flex items-center justify-center overflow-hidden rounded-md bg-surface-hover"
        style={{ width: TILE, height: TILE }}
        title={`${file.name} · ${formatBytes(file.size)}`}
      >
        {isImage && <Thumb file={file} />}
        {(!isImage || typeof URL === 'undefined' || typeof URL.createObjectURL !== 'function') && (
          <span className="flex flex-col items-center gap-1.5" data-contact-file-kind>
            <FileIcon size={ICON_SIZE.lg} className="text-ink-3" />
            <span
              className="rounded-sm bg-accent-subtle px-1.5 py-0.5 text-xs font-medium tracking-tight text-accent"
              data-contact-file-ext
            >
              {extOf(file.name) || '···'}
            </span>
          </span>
        )}
      </div>
      <span className="truncate text-xs text-ink-3" data-contact-file-name>
        {file.name}
      </span>
      <IconButton
        label={cs('attachRemove', { name: file.name })}
        iconSize="xs"
        tip={false}
        disabled={disabled}
        onClick={onRemove}
        data-contact-file-remove
        className="absolute right-1 top-1 rounded-full border border-border bg-surface/90"
      >
        <X size={ICON_SIZE.sm} />
      </IconButton>
    </li>
  )
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
          className="-ml-2 self-start"
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

/** 中文界面的轻量入口：页脚里的 ghost 小钮，点开一个小浮层，里面是企业微信二维码。 */
function WeComEntry() {
  return (
    <Popover
      side="top"
      align="start"
      width={196}
      ariaLabel={cs('wecom')}
      trigger={
        <Button variant="ghost" size="lg" data-contact-wecom>
          {cs('wecom')}
        </Button>
      }
    >
      <div className="flex flex-col items-center gap-2 p-1" data-contact-wecom-pop>
        <WeComQrImage />
        <span className="type-caption">{cs('wecomScan')}</span>
      </div>
    </Popover>
  )
}

/**
 * 失败卡里的复制动作：按钮上写明复制什么（「复制内容」「复制地址」）；复制成功后**只在底部通知轨说一句**
 * （「已复制 …」），按钮文字不变——与同窗里的「复制编号」同一做法，不挤页面。
 * 剪贴板不可用时什么都不说——用户仍可从旁边的文字手工选中。这里只写剪贴板，不调起任何别的程序。
 */
function CopyAction({
  text,
  label,
  toastKey,
  toastValues,
}: {
  text: string | (() => string)
  label: string
  toastKey: string
  toastValues?: Record<string, unknown>
}) {
  return (
    <Button
      variant="secondary"
      size="md"
      onClick={() => {
        const value = typeof text === 'function' ? text() : text
        void navigator.clipboard?.writeText(value).then(
          () => useUiStore.getState().setStatus(msg(`contact.${toastKey}`, toastValues, 'dialogs'), 'done'),
          () => {},
        )
      }}
      data-contact-copy
    >
      {label}
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
  const wecom = useWeComContactVisible()
  // 写信表单此刻的高度：进入结果态时拿它当结果卡的最小高度，对话框不因表单退场而变矮跳动
  const formHeight = useRef(0)
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
  } else if (phase === 'failed' && s.failure) {
    const wait = s.failure.retryable ? retryText(s.failure.retryAfter) : null
    statusNode = (
      <Notice tone="danger" title={cs('failedTitle')} data-contact-status="failed">
        <span className="flex flex-col gap-1.5">
          <span data-contact-failure={s.failure.code}>
            {failureText(s.failure)} {wait}
          </span>
          {/* 没有邮件协议链接、也不唤起邮件程序：只给「复制内容 / 复制地址」，由用户自己决定怎么发 */}
          <span className="flex flex-wrap items-center gap-1.5" data-contact-fallback>
            <CopyAction text={() => draftText(draft)} label={cs('copyContent')} toastKey="contentCopied" />
            <CopyAction text={recipient} label={cs('copyAddress')} toastKey="addressCopied" toastValues={{ address: recipient }} />
          </span>
        </span>
      </Notice>
    )
  } else if (s.formError && s.formError !== 'emailInvalid') {
    statusNode = (
      <p className="text-sm text-danger-content" role="alert" data-contact-form-error={s.formError}>
        {cs(`error.${s.formError}`)}
      </p>
    )
  } else if (s.status?.state === 'cancelled' || (phase === 'edit' && s.status?.state === 'idle')) {
    statusNode = <p className="type-caption">{cs('cancelledNote')}</p>
  }

  // 发送成功：写信表单整个退场，窗口里只剩这张结果卡（含「存不下回信凭据」两种）
  const doneView = (
    <div
      className="flex flex-col items-center justify-center gap-3 px-2 py-8 text-center"
      // 最小高度 = 发送前表单的高度（窗口矮了就让位给窗口：不超过视口减去页眉页脚的一截），结果卡在其中垂直居中
      style={{ minHeight: formHeight.current ? `min(${formHeight.current}px, calc(100dvh - 14rem))` : undefined }}
      role="status"
      data-contact-status="done"
    >
      <span className="flex size-12 items-center justify-center rounded-full bg-ok-surface text-ok-content">
        <Check size={ICON_SIZE.lg} />
      </span>
      <p className="type-section" data-contact-sent-title>
        {cs('sentTitle')}
      </p>
      <p className="text-sm text-ink-2" data-contact-inbox-missing={s.inboxMissing ? '' : undefined}>
        {cs(s.inboxMissing ? (draft.replyEmail.trim() ? 'sentNoInboxEmail' : 'sentNoInboxNoEmail') : 'sentReplyHere')}
      </p>
      <span className="flex items-center gap-1 text-sm text-ink-3">
        <span className="font-mono" data-contact-message-id>
          {cs('sentNumber', { id: s.messageId ?? '' })}
        </span>
        {s.messageId && <CopyButton
            text={s.messageId}
            label={cs('sentCopyNumber')}
            appearance="icon"
            feedback="toast"
            onCopied={() => useUiStore.getState().setStatus(msg('contact.numberCopied', undefined, 'dialogs'), 'done')}
          />}
      </span>
    </div>
  )

  const writeView = (
    <div
      className="flex flex-col gap-4"
      data-contact-body
      ref={(el) => {
        if (el) formHeight.current = el.offsetHeight
      }}
      {...dropHandlers}
    >
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
        {draft.body.length > bodyMax * 0.9 && (
          <p className="flex justify-end type-caption tabular-nums" data-contact-body-count>
            {cs('bodyCount', { count: draft.body.length, max: bodyMax })}
          </p>
        )}
      </div>

      <div className="flex flex-col gap-1.5" data-contact-attachments>
        <span className="type-section">{cs('attachLabel')}</span>
        <ul
          data-contact-drop
          data-dragging={dragging ? '' : undefined}
          className="flex flex-wrap gap-2"
          data-contact-file-list
        >
          {draft.files.map((f, i) => (
            <AttachTile key={`${f.name}-${f.size}-${f.lastModified}`} file={f} disabled={!editable} onRemove={() => s.removeFile(i)} />
          ))}
          {draft.files.length < maxFiles && (
            <li className="flex flex-col" style={{ width: TILE }}>
              <button
                type="button"
                disabled={!editable}
                onClick={() => fileInput.current?.click()}
                aria-label={cs('attachPick')}
                title={cs('attachPick')}
                data-contact-pick
                style={{ width: TILE, height: TILE }}
                className={cn(
                  'flex items-center justify-center rounded-md border border-dashed border-ink-3/50 text-ink-3',
                  'transition-colors duration-fast hover:border-accent hover:bg-accent-subtle hover:text-accent',
                  'focus-visible:outline-2 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-40',
                  dragging && 'border-accent bg-accent-subtle text-accent',
                )}
              >
                <Plus size={ICON_SIZE.lg} />
              </button>
            </li>
          )}
        </ul>
        {s.fileError && (
          <p className="type-caption text-danger-content" role="alert" data-contact-file-error={s.fileError}>
            {cs(`fileError.${s.fileError}`, { max: maxFiles, size: formatBytes(maxTotal) })}
          </p>
        )}
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
            <span>{cs('diagLabel')}</span>
          </label>
          {draft.attachDiag && (
            <p data-contact-diag-hint className="type-caption pl-6">{cs('diagHint')}</p>
          )}
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
          onBlur={() => {
            // 清空了回信邮箱就等于不再让本机记着它
            if (s.rememberedEmail && draft.replyEmail.trim() === '') s.forgetEmail()
          }}
          data-contact-email
        />
        {draft.replyEmail.trim() !== '' && !validEmail(draft.replyEmail.trim()) && (
          <p className="type-caption text-danger-content" role="alert" data-contact-email-hint>
            {cs('error.emailInvalid')}
          </p>
        )}
      </div>
    </div>
  )

  const lettersView = (
    <div className="flex flex-col gap-3" data-contact-letters>
      <div className="flex justify-end">
        <Button
          variant="ghost"
          size="sm"
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
            <div className="flex items-center gap-x-2 text-sm">
              <span className="font-mono text-xs text-ink" data-contact-letter-number>
                {cs('lettersNumber', { id: m.message_id })}
              </span>
              <span className="ml-auto flex min-w-0 flex-wrap items-center justify-end gap-x-1.5">
                <span className="type-caption">{cs('lettersSentAt', { time: formatDateTime(m.sent_at * 1000) })}</span>
                <span className="type-caption">· {cs(`lettersState.${m.state}`)}</span>
              </span>
              <IconButton
                label={cs('lettersForget')}
                iconSize="sm"
                onClick={() => void s.forget(m.message_id)}
                data-contact-letter-forget
              >
                <Trash2 size={ICON_SIZE.sm} />
              </IconButton>
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
      size="md"
      anchor="contact"
      blockDismiss={busy}
      onEscape={busy ? () => void s.cancel() : undefined}
      status={view === 'write' ? statusNode : null}
      footer={{
        start: (
          <span className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="lg"
              disabled={busy}
              onClick={() => s.setView(view === 'write' ? 'letters' : 'write')}
              data-contact-switch-view
            >
              {view === 'write' ? `${cs('letters')}${unreadBadge}` : cs('write')}
            </Button>
            {wecom && <WeComEntry />}
          </span>
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
      {view === 'write' ? (phase === 'done' ? doneView : writeView) : lettersView}
    </Dialog>
  )
}
