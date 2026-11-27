import { useEffect, useRef, useState, type DragEvent } from 'react'
import { t as translate } from '@/i18n'
import { cn } from '@/lib/utils'
import { draftText, useContactStore, type ContactFailure } from '@/store/contactStore'
import { Button, IconButton } from '../ui/Button'
import { dropZoneClass } from '../ui/dropZone'
import { Notice } from '../ui/Notice'
import { ProgressBar } from '../ui/ProgressBar'
import { Check, File as FileIcon, Plus, X } from '../ui/icons'
import { useUiStore } from '@/store/uiStore'
import { msg } from '@/i18n/message'
import { ICON_SIZE, IconSlot } from '../ui/Icon'
import { CopyButton } from '../settings/CopyButton'
import { FEEDBACK_EMAIL } from '@/lib/brand'

export const cs = (key: string, values?: Record<string, unknown>) =>
  translate(`contact.${key}`, { ns: 'dialogs', ...(values ?? {}) })

export function formatBytes(n: number): string {
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MiB`
  if (n >= 1024) return `${Math.max(1, Math.round(n / 1024))} KiB`
  return `${n} B`
}

/** 「请在 N 分钟 / 小时后再试」；没有 Retry-After 就不说。 */
export function retryText(seconds: number | null | undefined): string | null {
  if (!seconds || seconds <= 0) return null
  if (seconds >= 3600) return cs('retryInHours', { n: Math.ceil(seconds / 3600) })
  return cs('retryInMinutes', { n: Math.max(1, Math.ceil(seconds / 60)) })
}

/** 失败的人话：引擎发信码 / 诊断包码 / 本机输入校验码三个出处各查各的表，查不到按「意外结果」说。 */
export function failureText(f: ContactFailure): string {
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
export const TILE = 88

/** 图片缩略图：object URL 随文件挂载、卸载即 revoke；环境没有 createObjectURL（测试 / 极老内核）就退回类型方块。 */
export function Thumb({ file }: { file: File }) {
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

export function extOf(name: string): string {
  const i = name.lastIndexOf('.')
  return i >= 0 ? name.slice(i + 1, i + 6).toUpperCase() : ''
}

/** 一个已添加的附件：88 见方；图片是缩略图，其它是文件图标 + 下方的扩展名徽标；文件名在下面截断；右上角 × 删除。 */
export function AttachTile({ file, onRemove, disabled }: { file: File; onRemove: () => void; disabled: boolean }) {
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

/**
 * 失败卡里的复制动作：按钮上写明复制什么（「复制内容」「复制地址」）；复制成功后**只在底部通知轨说一句**
 * （「已复制 …」），按钮文字不变——与同窗里的「复制编号」同一做法，不挤页面。
 * 剪贴板不可用时什么都不说——用户仍可从旁边的文字手工选中。这里只写剪贴板，不调起任何别的程序。
 */
export function CopyAction({
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

/** 拖放文件进窗：只在可编辑时接；返回根上要展开的四个处理器与「正拖着」状态。 */
export function useFileDrop(editable: boolean) {
  const [dragging, setDragging] = useState(false)
  const addFiles = useContactStore((s) => s.addFiles)
  const handlers = {
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
      if (editable) addFiles([...e.dataTransfer.files])
    },
  }
  return { dragging, handlers }
}

/** 附件区：缩略方块 + 「添加」钮 + 限额错误 + 隐藏的文件框（「联系作者」与期刊反馈窗共用，同一套限额）。 */
export function AttachmentField({
  editable,
  dragging,
  pickLabel,
}: {
  editable: boolean
  dragging: boolean
  pickLabel: string
}) {
  const capability = useContactStore((s) => s.capability)
  const files = useContactStore((s) => s.draft.files)
  const fileError = useContactStore((s) => s.fileError)
  const addFiles = useContactStore((s) => s.addFiles)
  const removeFile = useContactStore((s) => s.removeFile)
  const fileInput = useRef<HTMLInputElement | null>(null)
  const maxFiles = capability?.max_attachments ?? 5
  const maxTotal = capability?.max_total_bytes ?? 20 * 1024 * 1024
  return (
    <div className="flex flex-col gap-2" data-contact-attachments>
      {files.length > 0 && (
        <ul data-contact-file-list className="flex flex-wrap gap-2">
          {files.map((f, i) => (
            <AttachTile key={`${f.name}-${f.size}-${f.lastModified}`} file={f} disabled={!editable} onRemove={() => removeFile(i)} />
          ))}
        </ul>
      )}
      {files.length < maxFiles && (
        <div
          data-contact-drop
          data-dragging={dragging ? '' : undefined}
          className={cn('flex rounded-md', dropZoneClass(dragging))}
        >
          <Button
            variant="secondary"
            size="md"
            disabled={!editable}
            onClick={() => fileInput.current?.click()}
            data-contact-pick
          >
            <Plus size={ICON_SIZE.sm} />
            {pickLabel}
          </Button>
        </div>
      )}
      {fileError && (
        <p className="type-caption text-danger-content" role="alert" data-contact-file-error={fileError}>
          {cs(`fileError.${fileError}`, { max: maxFiles, size: formatBytes(maxTotal) })}
        </p>
      )}
      <input
        ref={fileInput}
        type="file"
        multiple
        hidden
        onChange={(e) => {
          addFiles([...(e.target.files ?? [])])
          e.target.value = ''
        }}
        data-contact-file-input
      />
    </div>
  )
}

/** 对话框 `status` 槽里的那一块：发送中 / 失败 / 表单校验一句话 / 已取消。没有就是 null。 */
export function useContactStatusNode() {
  const s = useContactStore()
  const { capability, draft, phase } = s
  const busy = phase === 'sending' || phase === 'cancelling' || phase === 'diag'
  const recipient = capability?.recipient ?? FEEDBACK_EMAIL
  const progress = s.status && s.status.total ? Math.round(((s.status.sent ?? 0) / s.status.total) * 100) : null
  const stageKey = phase === 'diag' ? 'diag' : phase === 'cancelling' ? 'cancelling' : (s.status?.stage ?? 'init')
  if (busy) {
    return (
      <div
        className="flex items-center justify-between gap-4 rounded-md bg-surface-hover px-3 py-1.5"
        data-contact-status="sending"
      >
        <p className="min-w-0 truncate text-sm text-ink-2">
          {cs(`stage.${stageKey}`, { pct: Math.min(100, Math.max(0, progress ?? 0)) })}
        </p>
        <ProgressBar
          pct={stageKey === 'upload' && progress !== null ? progress : null}
          label={cs(`stage.${stageKey}`, { pct: progress ?? 0 })}
          className="w-36 shrink-0"
        />
      </div>
    )
  }
  if (phase === 'failed' && s.failure) {
    const wait = s.failure.retryable ? retryText(s.failure.retryAfter) : null
    return (
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
  }
  if (s.formError && s.formError !== 'emailInvalid') {
    return (
      <p className="text-sm text-danger-content" role="alert" data-contact-form-error={s.formError}>
        {cs(`error.${s.formError}`)}
      </p>
    )
  }
  if (s.status?.state === 'cancelled' || (phase === 'edit' && s.status?.state === 'idle')) {
    return <p className="type-caption">{cs('cancelledNote')}</p>
  }
  return null
}

/** 发送成功后的结果卡：已发送 + 回信去向 + 编号。`minHeight` = 发送前表单的高度，窗口不因表单退场而变矮。 */
export function SentCard({ minHeight }: { minHeight?: string }) {
  const messageId = useContactStore((s) => s.messageId)
  const inboxMissing = useContactStore((s) => s.inboxMissing)
  const hasEmail = useContactStore((s) => s.draft.replyEmail.trim() !== '')
  return (
    <div
      className="flex flex-col items-center justify-center gap-3 px-2 py-8 text-center"
      style={{ minHeight }}
      role="status"
      data-contact-status="done"
    >
      {/* 无底盘的 ok 色对勾，与标题同一行同一中线（手帐线稿 2026-10-28；原来是 48px 绿底圆盘） */}
      <p className="type-section flex items-center justify-center gap-1.5" data-contact-sent-title>
        <IconSlot aria-hidden className="text-ok-content">
          <Check size={ICON_SIZE.md} />
        </IconSlot>
        {cs('sentTitle')}
      </p>
      <p className="text-sm text-ink-2" data-contact-inbox-missing={inboxMissing ? '' : undefined}>
        {cs(inboxMissing ? (hasEmail ? 'sentNoInboxEmail' : 'sentNoInboxNoEmail') : 'sentReplyHere')}
      </p>
      <span className="flex items-center gap-1 text-sm text-ink-3">
        <span className="font-mono" data-contact-message-id>
          {cs('sentNumber', { id: messageId ?? '' })}
        </span>
        {messageId && (
          <CopyButton
            text={messageId}
            label={cs('sentCopyNumber')}
            appearance="icon"
            feedback="toast"
            onCopied={() => useUiStore.getState().setStatus(msg('contact.numberCopied', undefined, 'dialogs'), 'done')}
          />
        )}
      </span>
    </div>
  )
}

/** 写信表单的高度：进入结果态时拿它当结果卡的最小高度 */
export const sentMinHeight = (formHeight: number): string | undefined =>
  formHeight ? `min(${formHeight}px, calc(100dvh - 14rem))` : undefined
