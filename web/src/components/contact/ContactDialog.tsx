import { useId, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { formatDateTime } from '@/i18n/format'
import { cn } from '@/lib/utils'
import { useContactStore, validEmail } from '@/store/contactStore'
import { Button, IconButton } from '../ui/Button'
import { Checkbox } from '../ui/Checkbox'
import { Details, Summary } from '../ui/Details'
import { Dialog } from '../ui/Dialog'
import { FieldGroup } from '../ui/FormSection'
import { TextArea, TextInput } from '../ui/Input'
import { Popover } from '../ui/Popover'
import { Trash2 } from '../ui/icons'
import { ICON_SIZE } from '../ui/Icon'
import { WeComQrImage, useWeComContactVisible } from '../settings/WeComContact'
import { ReplyPaper } from './ReplyPaper'
import {
  AttachmentField,
  SentCard,
  cs,
  formatBytes,
  sentMinHeight,
  useContactStatusNode,
  useFileDrop,
} from './contactParts'

/** 诊断包的三项额外内容，顺序同「发送问题反馈」确认框（`DiagnosticsSendDialog.EXTRA_KEYS`）。 */
const DIAG_EXTRA_KEYS = ['source', 'crash_reports', 'corrupt_sessions'] as const

/** 「我的来信」里用户自己写的那一份：灰色引用块；正文长了折叠成前 4 行 +「展开」。 */
function YourLetter({
  subject,
  body,
  names,
  isReport = false,
}: {
  subject: string
  body: string
  names: string[]
  isReport?: boolean
}) {
  const [open, setOpen] = useState(false)
  const empty = !subject && !body && names.length === 0
  if (empty && !isReport) return null // 旧记录没存原文
  const long = body.split('\n').length > 4 || body.length > 240
  return (
    <blockquote
      className="flex flex-col gap-1 border-l-2 border-border pl-3 text-sm text-ink-2"
      data-contact-letter-yours
    >
      <span className="type-caption">{cs('lettersYouWrote')}</span>
      {subject && <span className="break-words font-medium" data-contact-letter-subject>{subject}</span>}
      {isReport && !body && (
        <span className="break-words" data-contact-letter-no-note>
          {cs('lettersNoNote')}
        </span>
      )}
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

/** 中文界面的轻量入口：标题下一行轻文字，「加企业微信」是链接式小钮，点开一个小浮层，里面是企业微信二维码。 */
function WeComEntry() {
  return (
    <p className="type-caption" data-contact-wecom-line>
      {cs('wecomLead')}
      <Popover
        side="bottom"
        align="start"
        width={196}
        ariaLabel={cs('wecom')}
        trigger={
          <button
            type="button"
            className="mx-0.5 rounded-xs text-ink-2 underline underline-offset-2 outline-none hover:text-ink focus-visible:focus-ring"
            data-contact-wecom
          >
            {cs('wecomLink')}
          </button>
        }
      >
        <div className="flex flex-col items-center gap-2 p-1" data-contact-wecom-pop>
          <WeComQrImage />
          <span className="type-caption">{cs('wecomScan')}</span>
        </div>
      </Popover>
      {cs('wecomTail')}
    </p>
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
  const wecom = useWeComContactVisible()
  const busy = phase === 'sending' || phase === 'cancelling' || phase === 'diag'
  const editable = !busy && phase !== 'done'
  const { dragging, handlers: dropHandlers } = useFileDrop(editable)
  const statusSlot = useContactStatusNode()
  // 写信表单此刻的高度：进入结果态时拿它当结果卡的最小高度，对话框不因表单退场而变矮跳动
  const formHeight = useRef(0)
  if (!capability) return null

  const bodyMax = capability.body_max ?? 5000
  const subjectMax = capability.subject_max ?? 120

  const kinds = [...new Set((s.diag.prepared?.entries ?? []).map((e) => e.kind))]
  // 发送成功：写信表单整个退场，窗口里只剩这张结果卡（含「存不下回信凭据」两种）
  const doneView = <SentCard minHeight={sentMinHeight(formHeight.current)} />

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

      <AttachmentField editable={editable} dragging={dragging} pickLabel={cs('attachPick')} />

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
            <p data-contact-diag-hint className="type-caption pl-6">
              {cs(DIAG_EXTRA_KEYS.some((k) => draft.diagOptions[k]) ? 'diagHintExtras' : 'diagHint')}
            </p>
          )}
          {/* 额外内容（同「发送问题反馈」确认框的三项）：勾了诊断信息才展开；默认全不勾；勾上崩溃报告 / 损坏会话才在下面出隐私提示；
              脚本源码不出提示（用户 2026-11-04 裁决）。勾选变化 = 换包（store 里防抖）。 */}
          {draft.attachDiag && (
            <section aria-label={cs('diagExtrasTitle')} className="flex flex-col gap-1.5 pl-6" data-contact-diag-extras>
              <div className="flex items-center justify-between gap-2">
                <h3 className="type-section">{cs('diagExtrasTitle')}</h3>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={!editable}
                  onClick={() => {
                    const on = !DIAG_EXTRA_KEYS.every((k) => draft.diagOptions[k])
                    for (const k of DIAG_EXTRA_KEYS) if (draft.diagOptions[k] !== on) s.setDiagOption(k, on)
                  }}
                  data-contact-diag-extras-all
                >
                  {DIAG_EXTRA_KEYS.every((k) => draft.diagOptions[k])
                    ? translate('settings.diagnostics.send.extrasSelectNone', { ns: 'dialogs' })
                    : translate('settings.diagnostics.send.extrasSelectAll', { ns: 'dialogs' })}
                </Button>
              </div>
              <p className="type-caption" data-contact-diag-extras-prompt>
                {cs('diagExtrasPrompt')}
              </p>
              {DIAG_EXTRA_KEYS.map((key) => (
                <label key={key} className="flex items-start gap-2 text-sm text-ink">
                  <Checkbox
                    checked={draft.diagOptions[key]}
                    disabled={!editable}
                    onChange={(e) => s.setDiagOption(key, e.target.checked)}
                    data-contact-diag-extra={key}
                    className="mt-0.5"
                  />
                  <span className="flex min-w-0 flex-col">
                    <span>{translate(`settings.diagnostics.send.extra.${key}`, { ns: 'dialogs' })}</span>
                    {key !== 'source' && draft.diagOptions[key] && (
                      <span className="type-caption" data-contact-diag-extra-hint={key}>
                        {translate(`settings.diagnostics.send.extraHint.${key}`, { ns: 'dialogs' })}
                      </span>
                    )}
                  </span>
                </label>
              ))}
            </section>
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

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2" data-contact-secondary>
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
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
              {m.kind === 'report' && m.report_id ? (
                <>
                  <span
                    className="shrink-0 whitespace-nowrap rounded-full bg-accent-subtle px-1.5 text-xs text-accent"
                    data-contact-letter-kind="report"
                  >
                    {cs('lettersReportTag')}
                  </span>
                  <span className="whitespace-nowrap font-mono text-xs text-ink" data-contact-letter-number data-contact-report-id={m.report_id}>
                    {cs('lettersReportNumber', { id: m.report_id })}
                  </span>
                </>
              ) : (
                <>
                  {m.kind === 'journal' && (
                    <span
                      className="shrink-0 whitespace-nowrap rounded-full bg-accent-subtle px-1.5 text-xs text-accent"
                      data-contact-letter-kind="journal"
                    >
                      {cs('lettersJournalTag')}
                    </span>
                  )}
                  <span className="font-mono text-xs text-ink" data-contact-letter-number>
                    {cs('lettersNumber', { id: m.message_id })}
                  </span>
                </>
              )}
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
            <YourLetter
              subject={m.subject ?? ''}
              body={m.body ?? ''}
              names={m.attachment_names ?? []}
              isReport={m.kind === 'report'}
            />
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
      open={s.open && !s.journalTip}
      onOpenChange={(o) => s.setOpen(o)}
      title={view === 'letters' ? cs('letters') : cs('title')}
      size="md"
      description={
        wecom ? (
          <WeComEntry />
        ) : undefined
      }
      anchor="contact"
      blockDismiss={busy}
      onEscape={busy ? () => void s.cancel() : undefined}
      status={view === 'write' ? statusSlot : null}
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
      {view === 'write' ? (phase === 'done' ? doneView : writeView) : lettersView}
    </Dialog>
  )
}
