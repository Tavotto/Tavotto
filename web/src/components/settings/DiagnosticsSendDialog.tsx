import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { buildDiagnosticPayload } from '@/diagnostics'
import {
  cancelDiagSend,
  discardDiagSend,
  fetchDiagSendBundle,
  fetchDiagSendStatus,
  prepareDiagSend,
  startDiagSend,
  type DiagSendCapability,
  type DiagSendPrepared,
  type DiagSendStatus,
  ApiError,
} from '@/lib/api'
import { Button } from '../ui/Button'
import { Dialog } from '../ui/Dialog'
import { TextArea } from '../ui/Input'
import { Notice } from '../ui/Notice'
import { Select } from '../ui/Select'
import { FormRow } from '../FormRow'
import { CopyButton } from './CopyButton'
import { saveDiagnosticsZip } from './diagnosticsSave'

const ds = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.diagnostics.send.${key}`, { ns: 'dialogs', ...(values ?? {}) })

const POLL_MS = 350
const DEFAULT_NOTE_MAX = 1000
const DEFAULT_CATEGORY = 'other'

/** 失败码里没登记过的（引擎比界面新）一律按「意外结果」说。 */
const failureText = (code: string | undefined): string =>
  translate(`settings.diagnostics.send.failure.${code ?? 'unexpected_response'}`, {
    ns: 'dialogs',
    defaultValue: translate('settings.diagnostics.send.failure.unexpected_response', { ns: 'dialogs' }),
  })

function formatBytes(n: number): string {
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MiB`
  if (n >= 1024) return `${Math.max(1, Math.round(n / 1024))} KiB`
  return `${n} B`
}

/** 「请在 N 分钟 / 小时后再试」；没有 Retry-After 就不说。 */
function retryText(seconds: number | null | undefined): string | null {
  if (!seconds || seconds <= 0) return null
  if (seconds >= 3600) return ds('retryInHours', { count: Math.ceil(seconds / 3600) })
  return ds('retryInMinutes', { count: Math.max(1, Math.ceil(seconds / 60)) })
}

type Phase =
  | 'preparing'
  | 'prepareFailed'
  | 'tooLarge'
  | 'ready'
  | 'sending'
  | 'cancelling'
  | 'done'
  | 'failed'

/**
 * 「发送问题反馈」（ADR 0118）：把已脱敏的诊断包交给 Tavotto 维护者。**逐次确认**。
 *
 * 流程只有一条：打开 → 本机备好一份包（零网络）→ 用户看「将发送什么」、可以先把**同一份字节**存成文件、
 * 选问题类型、填说明 → 点「发送」才让引擎去连诊断服务（`startDiagSend`，唯一的远程触发点）→ 状态区
 * 回报阶段 / 进度 → 成功给可复制的报告编号，失败给人话与「保存到本地」的退路。
 *
 * **没有记住的选择**：关掉对话框 = 丢掉备好的包（`discardDiagSend`，发送中则等于取消），再打开从头来。
 * 取消 / 关窗之后不会有任何东西自己继续发。
 *
 * **布局不跳**（#797 纪律）：页脚两颗按钮始终在原位，只换字与 disabled；状态区常驻、有最小高度，
 * 结果在里面换内容而不是往下长。
 */
export function DiagnosticsSendDialog({
  open,
  onOpenChange,
  capability,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  capability: DiagSendCapability
}) {
  useTranslation('dialogs')
  const noteMax = capability.note_max_chars ?? DEFAULT_NOTE_MAX
  const [phase, setPhase] = useState<Phase>('preparing')
  const [prepared, setPrepared] = useState<DiagSendPrepared | null>(null)
  const [status, setStatus] = useState<DiagSendStatus | null>(null)
  const [category, setCategory] = useState(DEFAULT_CATEGORY)
  const [note, setNote] = useState('')
  const [saveState, setSaveState] = useState<'idle' | 'saved' | 'failed'>('idle')
  const [cancelledNotice, setCancelledNotice] = useState(false)
  /** 备好的包在引擎里放太久被清掉了（404）：这一份不能再发，关掉重开。不是引擎失败码，所以单列。 */
  const [packageGone, setPackageGone] = useState(false)
  /** 引擎里这份包的 id（卸载 / 关窗时丢弃用；用 ref 因为清理函数要读到最新值）。 */
  const idRef = useRef<string | null>(null)
  /** 这一次打开的代号：晚到的 prepare 响应认它，过期的直接丢（并通知引擎释放）。 */
  const gen = useRef(0)

  const discard = useCallback(() => {
    const id = idRef.current
    idRef.current = null
    if (id) void discardDiagSend(id).catch(() => {})
  }, [])

  // 打开 → 备包（只在打开那一刻采一次载荷）；关闭 / 卸载 → 丢弃
  useEffect(() => {
    if (!open) return
    const mine = ++gen.current
    setPhase('preparing')
    setPrepared(null)
    setStatus(null)
    setCategory(DEFAULT_CATEGORY)
    setNote('')
    setSaveState('idle')
    setCancelledNotice(false)
    setPackageGone(false)
    void prepareDiagSend(buildDiagnosticPayload())
      .then((p) => {
        if (gen.current !== mine) {
          if (p.id) void discardDiagSend(p.id).catch(() => {})
          return
        }
        setPrepared(p)
        idRef.current = p.id
        setPhase(p.too_large ? 'tooLarge' : 'ready')
      })
      .catch(() => {
        if (gen.current === mine) setPhase('prepareFailed')
      })
    return () => {
      gen.current++
      discard()
    }
  }, [open, discard])

  // 发送中：轮询引擎状态（引擎在后台线程里跑，这里只读）
  useEffect(() => {
    if (phase !== 'sending' && phase !== 'cancelling') return
    const id = idRef.current
    if (!id) return
    let stop = false
    let timer: number | undefined
    const tick = async () => {
      try {
        const s = await fetchDiagSendStatus(id)
        if (stop) return
        setStatus(s)
        if (s.state === 'done') return setPhase('done')
        if (s.state === 'failed') return setPhase('failed')
        if (s.state === 'cancelled') {
          setCancelledNotice(true)
          return setPhase('ready')
        }
      } catch {
        /* 单次读失败不改变结论，下一拍再读 */
      }
      if (!stop) timer = window.setTimeout(() => void tick(), POLL_MS)
    }
    timer = window.setTimeout(() => void tick(), POLL_MS)
    return () => {
      stop = true
      window.clearTimeout(timer)
    }
  }, [phase])

  const send = () => {
    const id = idRef.current
    if (!id || (phase !== 'ready' && phase !== 'failed')) return
    setCancelledNotice(false)
    setPhase('sending')
    setStatus(null)
    startDiagSend(id, { category, note: note.trim() })
      .then(setStatus)
      .catch((e) => {
        if (e instanceof ApiError && e.status === 404) setPackageGone(true)
        setStatus({ code: 'internal', retryable: false } as DiagSendStatus)
        setPhase('failed')
      })
  }

  const cancelSending = () => {
    const id = idRef.current
    if (!id) return
    setPhase('cancelling')
    void cancelDiagSend(id).catch(() => {})
  }

  const save = () => {
    const id = idRef.current
    if (!id) return
    fetchDiagSendBundle(id)
      .then((blob) => {
        saveDiagnosticsZip(blob)
        setSaveState('saved')
      })
      .catch(() => setSaveState('failed'))
  }

  const sending = phase === 'sending' || phase === 'cancelling'
  const editable = phase === 'ready' || phase === 'failed'
  const retryable = phase === 'failed' && status?.retryable !== false
  const kinds = Array.from(new Set((prepared?.entries ?? []).map((e) => e.kind)))
  const categories = capability.categories ?? [DEFAULT_CATEGORY]

  const progress =
    status?.stage === 'upload' && status.total > 0
      ? ds('progress.upload', { percent: Math.min(100, Math.floor((status.sent / status.total) * 100)) })
      : ds(`progress.${status?.stage === 'complete' ? 'complete' : 'init'}`)

  const statusNode = (
    <div className="min-h-11" data-diag-send-status={phase}>
      {phase === 'preparing' && <p className="type-caption">{ds('preparing')}</p>}
      {phase === 'prepareFailed' && (
        <Notice tone="danger">{ds('prepareFailed')}</Notice>
      )}
      {phase === 'tooLarge' && prepared && (
        <Notice tone="warn">
          {ds('tooLarge', { size: formatBytes(prepared.size), max: formatBytes(prepared.max_bytes) })}
        </Notice>
      )}
      {phase === 'ready' && cancelledNotice && <p className="type-caption">{ds('cancelled')}</p>}
      {phase === 'sending' && (
        <p className="type-caption" role="status">
          {progress}
        </p>
      )}
      {phase === 'cancelling' && (
        <p className="type-caption" role="status">
          {ds('progress.cancelling')}
        </p>
      )}
      {phase === 'failed' && (
        <Notice tone="danger" data-diag-send-failure={status?.code ?? ''}>
          <span className="flex flex-col gap-1">
            <span>{packageGone ? ds('packageGone') : failureText(status?.code)}</span>
            {retryText(status?.retry_after) && <span>{retryText(status?.retry_after)}</span>}
            {!packageGone && <span>{ds('failureHint')}</span>}
          </span>
        </Notice>
      )}
      {phase === 'done' && status?.report_id && (
        <Notice tone="ok" title={ds('doneTitle')}>
          <span className="flex flex-col gap-1">
            <span className="flex flex-wrap items-center gap-2">
              <span>{ds('reportId')}</span>
              <code data-diag-report-id className="font-mono text-ink">
                {status.report_id}
              </code>
              <CopyButton text={status.report_id} label={ds('copyId')} variant="ghost" appearance="icon" />
            </span>
            <span>{ds('doneBody')}</span>
          </span>
        </Notice>
      )}
    </div>
  )

  const closeOnly = phase === 'done' || phase === 'prepareFailed' || phase === 'tooLarge'
  const nonRetryableFailure = phase === 'failed' && !retryable

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={ds('title')}
      size="md"
      anchor="diagnostics-send"
      blockDismiss={sending}
      onEscape={sending ? (phase === 'sending' ? cancelSending : undefined) : undefined}
      status={statusNode}
      footer={{
        secondary: sending ? (
          <Button variant="ghost" size="lg" onClick={cancelSending} disabled={phase === 'cancelling'} data-diag-send-cancel>
            {ds('cancelSending')}
          </Button>
        ) : phase === 'done' ? undefined : (
          <Button variant="ghost" size="lg" onClick={() => onOpenChange(false)} data-diag-send-dismiss>
            {ds('cancel')}
          </Button>
        ),
        primary:
          closeOnly || nonRetryableFailure ? (
            <Button variant="primary" size="lg" onClick={() => onOpenChange(false)} data-diag-send-close>
              {phase === 'done' ? ds('done') : ds('close')}
            </Button>
          ) : (
            <Button
              variant="primary"
              size="lg"
              loading={phase === 'sending'}
              disabled={phase === 'preparing' || phase === 'cancelling' || phase === 'sending'}
              onClick={send}
              data-diag-send-confirm
            >
              {phase === 'failed' ? ds('retry') : ds('send')}
            </Button>
          ),
      }}
    >
      <div className="flex flex-col gap-3" data-diag-send-body>
        <p className="text-sm text-ink-2">{ds('lead')}</p>

        <section aria-label={ds('includedTitle')} className="flex flex-col gap-1">
          <h3 className="type-section">{ds('includedTitle')}</h3>
          {prepared ? (
            <ul className="flex list-disc flex-col gap-0.5 pl-4 text-sm text-ink-2" data-diag-send-kinds>
              {kinds.map((k) => (
                <li key={k} data-diag-kind={k}>
                  {translate(`settings.diagnostics.send.kind.${k}`, {
                    ns: 'dialogs',
                    defaultValue: translate('settings.diagnostics.send.kind.other', { ns: 'dialogs' }),
                  })}
                </li>
              ))}
            </ul>
          ) : (
            <p className="type-caption">{ds('preparing')}</p>
          )}
          <p className="type-caption">{ds('notIncluded')}</p>
          <div className="flex items-center gap-2">
            {prepared && <span className="type-caption">{ds('size', { size: formatBytes(prepared.size) })}</span>}
            <Button
              variant="ghost"
              size="sm"
              onClick={save}
              disabled={!prepared?.id || phase === 'done'}
              data-diag-send-save
            >
              {ds('save')}
            </Button>
            {saveState !== 'idle' && (
              <span className="type-caption" role="status">
                {saveState === 'saved' ? ds('saved') : ds('saveFailed')}
              </span>
            )}
          </div>
        </section>

        <FormRow label={ds('categoryLabel')}>
          <Select
            value={category}
            onChange={setCategory}
            options={categories.map((c) => ({
              value: c,
              label: translate(`settings.diagnostics.send.category.${c}`, { ns: 'dialogs', defaultValue: c }),
            }))}
            ariaLabel={ds('categoryLabel')}
            disabled={!editable}
            className="w-full"
          />
        </FormRow>
        <FormRow label={ds('noteLabel')} align="start">
          <span className="flex min-w-0 flex-1 flex-col gap-1">
            <TextArea
              aria-label={ds('noteLabel')}
              value={note}
              rows={3}
              maxRows={5}
              maxLength={noteMax}
              disabled={!editable}
              onChange={(e) => setNote(e.target.value)}
              data-diag-send-note
            />
            <span className="flex justify-between gap-2 type-caption">
              <span>{ds('noteHint')}</span>
              <span>{ds('noteCount', { count: note.length, max: noteMax })}</span>
            </span>
          </span>
        </FormRow>

        <p className="type-caption">{ds('retention', { days: capability.retention_days ?? 30 })}</p>
      </div>
    </Dialog>
  )
}
