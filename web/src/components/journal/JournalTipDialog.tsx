import { useId, useRef, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { JOURNAL_ISSUES } from '@/lib/journalTip'
import { useContactStore, validEmail } from '@/store/contactStore'
import { CREDIT_MAX } from '@/lib/journalCredit'
import { Button } from '../ui/Button'
import { Checkbox } from '../ui/Checkbox'
import { Dialog } from '../ui/Dialog'
import { ICON_SIZE } from '../ui/Icon'
import { ArrowUpRight } from '../ui/icons'
import { TextArea, TextInput } from '../ui/Input'
import { Segmented } from '../ui/Segmented'
import {
  AttachmentField,
  SentCard,
  cs,
  sentMinHeight,
  useContactStatusNode,
  useFileDrop,
} from '../contact/contactParts'

const jt = (key: string, values?: Record<string, unknown>) =>
  translate(`journalTip.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/** 「反馈条目」里的一行：label 左 / 值右，样式同设置页「期刊要求」。 */
function Row({
  label,
  value,
  name,
  children,
}: {
  label: string
  value?: string
  name: string
  children?: ReactNode
}) {
  return (
    <div className="flex min-h-8 items-center gap-2 text-sm" data-journal-tip-row={name}>
      <dt className="w-18 shrink-0 text-ink-2">{label}</dt>
      <dd className="min-w-0 flex-1 truncate tabular-nums text-ink" title={value}>
        {children ?? value}
      </dd>
    </div>
  )
}

/**
 * 期刊规范反馈（ADR 0213）：出处卡「这条不对」/ 选择器「告诉作者加上」进来的专用窗。
 * 发送、草稿、附件、取消、失败重试、编号结果全走 `contactStore`（同一条管线，与「联系作者」互斥显示）；
 * 这里只是另一副版式：条目只读、问题分段、期刊原文、出处链接，没有主题、诊断信息、「我的来信」。
 */
export function JournalTipDialog() {
  useTranslation('dialogs')
  const s = useContactStore()
  const { capability, draft, phase } = s
  const tip = s.journalTip
  const bodyId = useId()
  const urlId = useId()
  const nameId = useId()
  const emailId = useId()
  const creditId = useId()
  const busy = phase === 'sending' || phase === 'cancelling' || phase === 'diag'
  const editable = !busy && phase !== 'done'
  const { dragging, handlers: dropHandlers } = useFileDrop(editable)
  const statusNode = useContactStatusNode()
  const formHeight = useRef(0)
  if (!capability || !tip) return null

  const missing = tip.type === 'missing'
  const editor = tip.type === 'editor'
  const bodyMax = capability.body_max ?? 5000
  const source = [tip.sourceHost, tip.sourceDate].filter(Boolean).join(' · ')
  // 字段随「问题」联动：只换标签 / placeholder，已填内容不清
  const issue = draft.issue
  const bodyLabel = missing ? 'noteLabel' : issue === 'link_broken' ? 'noteLabel' : issue === 'other' ? 'descLabel' : 'quoteLabel'
  const urlLabel = issue === 'link_broken' ? 'urlLabelNew' : 'urlLabel'
  const bodyPlaceholder = !missing && (issue === '' || issue === 'wrong_value' || issue === 'updated')

  const writeView = (
    <div
      className="flex flex-col gap-4"
      data-journal-tip-body
      ref={(el) => {
        if (el) formHeight.current = el.offsetHeight
      }}
      {...dropHandlers}
    >
      {editor ? (
        <div className="flex flex-col gap-2">
          <div className="flex flex-col gap-1.5">
            <label htmlFor={bodyId} className="type-section">
              {jt('editorQuoteLabel')}
            </label>
            <TextArea
              id={bodyId}
              aria-label={jt('editorQuoteLabel')}
              value={draft.body}
              rows={4}
              maxRows={10}
              maxLength={bodyMax}
              placeholder={jt('editorQuotePlaceholder')}
              disabled={!editable}
              onChange={(e) => s.setDraft({ body: e.target.value })}
              data-contact-text
              className="px-3 py-2"
            />
          </div>
          <AttachmentField editable={editable} dragging={dragging} pickLabel={jt('attach')} />
        </div>
      ) : null}
      {editor ? null : missing ? (
        <div className="flex flex-col gap-1.5">
          <label htmlFor={nameId} className="type-section">
            {jt('nameLabel')}
          </label>
          <TextInput
            id={nameId}
            value={draft.query}
            maxLength={120}
            disabled={!editable}
            onChange={(e) => s.setDraft({ query: e.target.value })}
            data-journal-tip-name
          />
        </div>
      ) : (
        <>
          <section aria-label={jt('entryTitle')}>
            <dl className="flex flex-col divide-y divide-border border-y border-border" data-journal-tip-entry>
              {tip.ruleLabel && <Row name="rule" label={jt('rowRule')} value={tip.ruleLabel} />}
              {tip.current && <Row name="current" label={jt('rowCurrent')} value={tip.current} />}
              {source && (
                <Row name="source" label={jt('rowSource')} value={source}>
                  {tip.sourceHost && tip.sourceUrl ? (
                    <>
                      <a
                        href={tip.sourceUrl}
                        target="_blank"
                        rel="noreferrer"
                        data-journal-tip-source-link
                        className="inline-flex items-center gap-0.5 text-info-content hover:underline"
                      >
                        {tip.sourceHost}
                        <ArrowUpRight size={ICON_SIZE.sm} aria-hidden />
                      </a>
                      {tip.sourceDate && <span className="text-ink-3"> · {tip.sourceDate}</span>}
                    </>
                  ) : undefined}
                </Row>
              )}
            </dl>
          </section>
          <div className="flex flex-col gap-1.5">
            <span className="type-section">
              {jt('issueLabel')}
            </span>
            <Segmented
              ariaLabel={jt('issueLabel')}
              value={draft.issue || null}
              onChange={(v) => s.setDraft({ issue: v })}
              items={JOURNAL_ISSUES.map((v) => ({ value: v, label: jt(`issue.${v}`), disabled: !editable }))}
              data-testid="journal-tip-issue"
            />
          </div>
        </>
      )}

      {missing ? (
        <div className="flex flex-col gap-1.5">
          <label htmlFor={urlId} className="type-section">
            {jt('guideLabel')}
          </label>
          <TextInput
            id={urlId}
            type="url"
            inputMode="url"
            value={draft.url}
            maxLength={2000}
            placeholder={jt('urlPlaceholder')}
            disabled={!editable}
            onChange={(e) => s.setDraft({ url: e.target.value })}
            data-contact-journal-url
          />
        </div>
      ) : null}

      {editor ? null : missing ? (
        <div className="flex flex-col gap-2">
        <div className="flex flex-col gap-1.5">
          <label htmlFor={bodyId} className="type-section">
            {jt(bodyLabel)}
          </label>
          <TextArea
            id={bodyId}
            aria-label={jt(bodyLabel)}
            value={draft.body}
            rows={missing ? 3 : 4}
            maxRows={10}
            maxLength={bodyMax}
            placeholder={bodyPlaceholder ? jt('quotePlaceholder') : undefined}
            disabled={!editable}
            onChange={(e) => s.setDraft({ body: e.target.value })}
            data-contact-text
            className="px-3 py-2"
          />
        </div>
          <AttachmentField editable={editable} dragging={dragging} pickLabel={jt('attach')} />
        </div>
      ) : (
      <div className="flex flex-col gap-1.5">
        <label htmlFor={bodyId} className="type-section">
          {jt(bodyLabel)}
        </label>
        <TextArea
          id={bodyId}
          aria-label={jt(bodyLabel)}
          value={draft.body}
          rows={missing ? 3 : 4}
          maxRows={10}
          maxLength={bodyMax}
          placeholder={bodyPlaceholder ? jt('quotePlaceholder') : undefined}
          disabled={!editable}
          onChange={(e) => s.setDraft({ body: e.target.value })}
          data-contact-text
          className="px-3 py-2"
        />
      </div>
      )}

      {!missing && !editor && (
        <div className="flex flex-col gap-2">
          <div className="flex flex-col gap-1.5">
            <label htmlFor={urlId} className="type-section">
              {jt(urlLabel)}
            </label>
            <TextInput
              id={urlId}
              type="url"
              inputMode="url"
              value={draft.url}
              maxLength={2000}
              placeholder={jt('urlPlaceholder')}
              disabled={!editable}
              onChange={(e) => s.setDraft({ url: e.target.value })}
              data-contact-journal-url
            />
          </div>
          <AttachmentField editable={editable} dragging={dragging} pickLabel={jt('attach')} />
        </div>
      )}

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
          placeholder={jt(editor ? 'editorEmail' : missing ? 'emailMissing' : 'emailWrong')}
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

      {/* 署名（ADR 0213 三期）：默认不勾，勾了才出称呼框；编辑部原话（editor）不进目录，没有这一行。解释放 title，不堆字 */}
      {!editor && (
        <div className="flex flex-col gap-1.5" data-journal-tip-credit>
          <label className="flex items-center gap-2 text-sm text-ink" title={jt('creditTitle')}>
            <Checkbox
              checked={draft.creditOn}
              disabled={!editable}
              onChange={(e) => s.setDraft({ creditOn: e.target.checked })}
              data-journal-tip-credit-on
            />
            <span>{jt('credit')}</span>
          </label>
          {draft.creditOn && (
            <TextInput
              id={creditId}
              aria-label={jt('creditInput')}
              value={draft.credit}
              maxLength={CREDIT_MAX}
              placeholder={jt('creditPlaceholder')}
              disabled={!editable}
              autoFocus
              onChange={(e) => s.setDraft({ credit: e.target.value.replace(/[\r\n]+/g, ' ') })}
              data-journal-tip-credit-name
            />
          )}
        </div>
      )}
    </div>
  )

  return (
    <Dialog
      open={s.open}
      onOpenChange={(o) => s.setOpen(o)}
      title={jt(`title.${missing ? 'missing' : 'wrong'}`)}
      size="md"
      description={
        !missing && tip.journalLabel ? (
          <p className="type-caption" data-journal-tip-about>
            {editor ? jt('editorAbout', { journal: tip.journalLabel }) : tip.journalLabel}
          </p>
        ) : undefined
      }
      anchor="journal-tip"
      blockDismiss={busy}
      onEscape={busy ? () => void s.cancel() : undefined}
      status={statusNode}
      footer={{
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
        ) : phase === 'done' ? null : (
          <Button variant="ghost" size="lg" onClick={() => s.setOpen(false)} data-contact-dismiss>
            {jt('cancel')}
          </Button>
        ),
        primary:
          phase === 'done' ? (
            <Button variant="primary" size="lg" onClick={() => s.setOpen(false)} data-contact-close>
              {cs('done')}
            </Button>
          ) : (
            <Button variant="primary" size="lg" loading={busy} disabled={busy || (editor && !draft.body.trim() && draft.files.length === 0)} onClick={() => void s.send()} data-contact-send>
              {phase === 'failed' ? cs('retry') : jt(missing ? 'submitMissing' : 'submit')}
            </Button>
          ),
      }}
    >
      {phase === 'done' ? <SentCard minHeight={sentMinHeight(formHeight.current)} /> : writeView}
    </Dialog>
  )
}
