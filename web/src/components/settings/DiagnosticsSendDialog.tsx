import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { buildSendPayload, NO_DETAIL_OPTIONS, type DetailOptions } from '@/diagnostics/detail'
import {
  cancelDiagSend,
  discardDiagSend,
  fetchDiagSendBundle,
  fetchDiagSendStatus,
  isStalePrepare,
  prepareDiagSend,
  startDiagSend,
  type DiagSendCapability,
  type DiagSendPrepared,
  type DiagSendStatus,
  ApiError,
} from '@/lib/api'
import { PRIVACY_DOC_URL } from '@/lib/brand'
import { cn } from '@/lib/utils'
import { currentProjectId, onCurrentProjectChange } from '@/lib/session'
import { useDiagSendStore } from '@/store/diagSendStore'
import { useProjectStore } from '@/store/projectStore'
import { Button } from '../ui/Button'
import { Checkbox } from '../ui/Checkbox'
import { Details, Summary } from '../ui/Details'
import { Dialog } from '../ui/Dialog'
import { FieldGroup } from '../ui/FormSection'
import { TextArea } from '../ui/Input'
import { Select } from '../ui/Select'
import { CopyButton } from './CopyButton'
import { saveDiagnosticsZip } from './diagnosticsSave'
import { WeComContactNote } from './WeComContact'

const ds = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.diagnostics.send.${key}`, { ns: 'dialogs', ...(values ?? {}) })

const EXTRA_KEYS = ['source', 'crash_reports', 'corrupt_sessions'] as const

const POLL_MS = 350
/** 勾选变化后等多久才重新备包：连点几个框只备一次，点击本身从不等后端 */
const REFRESH_DEBOUNCE_MS = 300
const DEFAULT_NOTE_MAX = 1000
const DEFAULT_CATEGORY = 'other'
/** 引擎拒绝「迟到的 start」的两个码（`diagsend.start`）：取消已先被处理，不是发送失败 */
const LATE_START_CODES = new Set(['diag_stale_action', 'diag_session_cancelled'])

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
  | 'unknown'
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
  const noteId = useId()
  const categoryId = useId()
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
  /** 引擎动作代次（取自备包 / 最近一次状态）；send 带上它，cancel 之后迟到的 send 会被引擎拒 */
  const actionGen = useRef<number | undefined>(undefined)
  /** 取消之后会话作废（引擎不让它复活）→ 自动重新备包；这个标记让「已取消」那句话在重备之后还在 */
  const showCancelled = useRef(false)
  /** 备包那一刻属于哪个项目；诊断包带着那个项目的状态，项目一换这份包就不是「现在这个项目」的了。 */
  const packagePj = useRef<string | null>(null)
  /** 换代计数：项目切换 / 用户点「重新准备」都让备包流程重来一遍（旧包随清理被丢弃）。 */
  const [epoch, setEpoch] = useState(0)
  /**
   * 三个勾选项（脚本源码 / 系统崩溃报告 / 会话损坏副本内容）：**每次打开对话框都从全不勾开始、不记忆**。
   * 勾选变化 = 内容变了 = 要换一份包，但**点击即时生效、不等后端**：框的状态本地立刻更新，换包走防抖的
   * 静默刷新（`scheduleRefresh`：不回到「正在准备」、不禁用表单、不清已填内容；请求序号认最新一次，旧结果丢弃）。
   * 刷新期间发送 / 保存禁用（旧包不含新勾选），新包到了才放开。
   */
  const [options, setOptions] = useState<DetailOptions>(NO_DETAIL_OPTIONS)
  const optionsRef = useRef<DetailOptions>(NO_DETAIL_OPTIONS)
  const refreshTimer = useRef<number | undefined>(undefined)
  const refreshSeq = useRef(0)
  /** 勾选已变、新包还没到：发送 / 保存暂不可用（框本身不受影响） */
  const [refreshing, setRefreshing] = useState(false)
  /** 勾了但引擎没附上（引擎侧丢了载荷 / 老引擎）：折叠区里一句话说明 */
  const [optionsLost, setOptionsLost] = useState(false)

  const discard = useCallback(() => {
    const id = idRef.current
    idRef.current = null
    // 显式传备包时的项目：清理发生在 `setCurrentProjectId(B)` 之后，全局当前项目已不是这份包的项目
    if (id) void discardDiagSend(id, packagePj.current).catch(() => {})
  }, [])

  /** 采用一份新备好的包；以引擎实际采用的选项为准：勾着的框不能在引擎没附上内容时仍显示勾着 */
  const adoptPrepared = useCallback((p: DiagSendPrepared, asked: DetailOptions) => {
    setPrepared(p)
    const applied = { ...NO_DETAIL_OPTIONS, ...(p.options ?? {}) }
    if (p.options) {
      optionsRef.current = applied
      setOptions(applied)
      setOptionsLost(EXTRA_KEYS.some((k) => asked[k] && !applied[k]))
    } else setOptionsLost(false)
    idRef.current = p.id
    actionGen.current = p.gen
    // 旧包的「已不在引擎里」与失败说明只属于旧包：换上新包就一并复位，否则新包的普通失败仍显示过期文案
    setPackageGone(false)
    setStatus(null)
    setPhase(p.too_large ? 'tooLarge' : 'ready')
  }, [])

  // 打开 → 备包（只在打开那一刻采一次载荷）；关闭 / 卸载 → 丢弃
  useEffect(() => {
    if (!open) return
    const mine = ++gen.current
    setPhase('preparing')
    setPrepared(null)
    setStatus(null)
    window.clearTimeout(refreshTimer.current)
    refreshSeq.current++
    setRefreshing(false)
    const opts = NO_DETAIL_OPTIONS
    setCategory(DEFAULT_CATEGORY)
    // AI 修复替用户起草的说明（若有）：只预填，用户可改可删；源码勾选框照旧从不勾开始
    setNote(useDiagSendStore.getState().takePresetNote().slice(0, DEFAULT_NOTE_MAX))
    optionsRef.current = opts
    setOptions(opts)
    setSaveState('idle')
    setCancelledNotice(showCancelled.current)
    showCancelled.current = false
    setPackageGone(false)
    const pj = currentProjectId()
    packagePj.current = pj
    // 切项目进行中（`projectStore.switching`：从认领新 pj 到文档 / 轨迹 / 各 store 换代完成）：内存里
    // 还是旧项目的文档与轨迹，此刻备的包会是 A/B 混合包——不备，等 `switching` 落下去再来（下面的订阅）
    if (useProjectStore.getState().switching) return () => void (gen.current++)
    void buildSendPayload(opts)
      // 延迟的备包绑定调度时抓到的项目：等待期间项目换了就丢弃，绝不对新项目备包；请求也显式写这个项目
      .then((payload) => (gen.current === mine && currentProjectId() === pj ? prepareDiagSend(payload, opts, pj) : null))
      .then((p) => {
        if (!p) return
        if (gen.current !== mine) {
          if (p.id) void discardDiagSend(p.id, pj).catch(() => {})
          return
        }
        adoptPrepared(p, opts)
      })
      .catch((e) => {
        if (isStalePrepare(e)) return // 被更新的备包取代：不是失败，界面由那份新请求更新
        if (gen.current === mine) setPhase('prepareFailed')
      })
    return () => {
      gen.current++
      window.clearTimeout(refreshTimer.current)
      refreshSeq.current++
      discard()
    }
  }, [open, epoch, discard, adoptPrepared])

  // 对话框开着时项目被换掉（外部 `tavotto open`、切项目）：旧项目的包（含在途的备包响应、发送中的会话）
  // 一律丢弃，并重新备包——绝不拿 A 项目的诊断包在 B 项目的界面上发出去（Codex #923 P1）
  useEffect(() => {
    if (!open) return
    const bump = () => setEpoch((e) => e + 1)
    const offId = onCurrentProjectChange(bump)
    // 切换开始：旧包立刻作废（重跑备包流程，它见 `switching` 就只置「正在准备」）；
    // 切换完成：在换代全部落地之后才备新包——只认这一个「项目已完全加载」的信号
    // （`projectStore.switching`，`runSwitch` 排队到执行完一直亮着；不另造一套代次）
    const offSwitch = useProjectStore.subscribe((st, prev) => {
      if (st.switching !== prev.switching) bump()
    })
    return () => {
      offId()
      offSwitch()
    }
  }, [open])

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
        if (s.gen !== undefined) actionGen.current = s.gen
        if (s.state === 'done') {
          useDiagSendStore.getState().markSent() // AI 修复的「发送」卡据此知道用户真的点了发送
          return setPhase('done')
        }
        if (s.state === 'unknown') return setPhase('unknown')
        if (s.state === 'failed') return setPhase('failed')
        if (s.state === 'cancelled') {
          // 取消过的会话不可复活：重新备包得到新会话，再发就是新的授权
          showCancelled.current = true
          return setEpoch((e) => e + 1)
        }
      } catch (e) {
        // 404 = 引擎里已经没有这个会话（引擎重启过 / 会话过期）：终态，不是暂时故障——再轮询只会把用户困在模态框里
        if (!stop && e instanceof ApiError && e.status === 404) {
          setPackageGone(true)
          setStatus({ code: 'internal', retryable: false } as DiagSendStatus)
          return setPhase('failed')
        }
        /* 其余单次读失败不改变结论，下一拍再读 */
      }
      if (!stop) timer = window.setTimeout(() => void tick(), POLL_MS)
    }
    timer = window.setTimeout(() => void tick(), POLL_MS)
    return () => {
      stop = true
      window.clearTimeout(timer)
    }
  }, [phase])

  /** 防抖的静默换包：只在「没有更新的勾选 / 没换代 / 没换项目」时才采用结果，旧响应丢弃并通知引擎释放 */
  const scheduleRefresh = () => {
    const mineGen = gen.current
    const seq = ++refreshSeq.current
    window.clearTimeout(refreshTimer.current)
    setRefreshing(true)
    // 旧包即将被换掉：「已保存」说的是旧包，换包后不再成立
    setSaveState('idle')
    refreshTimer.current = window.setTimeout(() => {
      const opts = optionsRef.current
      const pj = packagePj.current
      const current = () => gen.current === mineGen && refreshSeq.current === seq
      const run = (retried: boolean): void => {
        void buildSendPayload(opts)
          .then((payload) => (current() && currentProjectId() === pj ? prepareDiagSend(payload, opts, pj) : null))
          .then((p) => {
            // null = 备包前项目已换：epoch 会推进 gen，打开 effect 重跑并复位 refreshing，这里无需处理
            if (!p) return
            if (!current()) {
              if (p.id && p.id !== idRef.current) void discardDiagSend(p.id, pj).catch(() => {})
              return
            }
            const old = idRef.current
            if (old && old !== p.id) void discardDiagSend(old, pj).catch(() => {})
            adoptPrepared(p, opts)
            setRefreshing(false)
          })
          .catch((e) => {
            if (!current()) return // 已被更新的勾选 / 换代取代：由那一份更新界面
            // 最新一次刷新被引擎判 stale（别的窗口为同一项目备了包）：没有更新的本地回调会来清 refreshing，
            // 不能卡死。重试一次（optionsRef 此刻仍等于 opts，否则 current() 已为假）；再 stale 就按失败作废
            if (isStalePrepare(e) && !retried) return run(true)
            // 旧包不含用户刚改的勾选（例：刚取消勾选源码，旧包仍带源码）：失败时必须作废，
            // 否则「保存」会把用户已取消的内容下载下来。丢弃引擎里那份并清空 prepared / idRef
            discard()
            setPrepared(null)
            setRefreshing(false)
            setPhase('prepareFailed')
          })
      }
      run(false)
    }, REFRESH_DEBOUNCE_MS)
  }

  const allOptionsOn = EXTRA_KEYS.every((k) => options[k])
  /** 一键全选 / 全不选：全勾着就清空，否则全勾上；只记一笔、只换一次包 */
  const toggleAllOptions = () => {
    if (phase !== 'ready' && phase !== 'failed') return
    const on = !EXTRA_KEYS.every((k) => optionsRef.current[k])
    optionsRef.current = { ...optionsRef.current, source: on, crash_reports: on, corrupt_sessions: on }
    setOptions(optionsRef.current)
    scheduleRefresh()
  }

  const toggleOption = (key: keyof DetailOptions) => {
    if (phase !== 'ready' && phase !== 'failed') return
    optionsRef.current = { ...optionsRef.current, [key]: !optionsRef.current[key] }
    setOptions(optionsRef.current)
    scheduleRefresh()
  }

  const send = () => {
    const id = idRef.current
    if (!id || refreshing || (phase !== 'ready' && phase !== 'failed')) return
    if (useProjectStore.getState().switching || packagePj.current !== currentProjectId()) {
      // 项目已换而监听还没来得及重渲：不发旧包，重新备包
      setEpoch((e) => e + 1)
      return
    }
    setCancelledNotice(false)
    setPhase('sending')
    setStatus(null)
    // 回调认发起这一刻的代号：A 的 start 挂着时切到 B，B 备包完成后 A 的 start 才回来，不许改 B 的对话框
    const mine = gen.current
    startDiagSend(id, { category, note: note.trim(), gen: actionGen.current })
      .then((st) => {
        if (gen.current === mine) setStatus(st)
      })
      .catch((e) => {
        if (gen.current !== mine) return
        // 取消先于 start 被引擎处理：引擎正确拒了迟到的 start（409 stale_action / session_cancelled）。这是取消流程的
        // 一部分，不是失败——进入 cancelling，让轮询看到 cancelled 后重新备包（覆盖成失败会停掉轮询、把用户困在错误里）
        if (e instanceof ApiError && e.status === 409 && LATE_START_CODES.has(String(e.body?.code ?? ''))) {
          setPhase('cancelling')
          return
        }
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
    if (!id || refreshing) return
    const mine = gen.current
    // 「勾选版本」：取包在途时用户改了勾选（只推进 refreshSeq、不推进 gen），这份包按旧勾选生成，
    // 可能仍含刚取消的内容（脚本源码 / 崩溃数据）——不写盘。发送不需要：发送中复选框已禁用（toggleOption 要求 ready/failed）
    const seq = refreshSeq.current
    fetchDiagSendBundle(id)
      .then((blob) => {
        if (gen.current !== mine || refreshSeq.current !== seq) return
        saveDiagnosticsZip(blob)
        setSaveState('saved')
      })
      .catch(() => {
        if (gen.current === mine) setSaveState('failed')
      })
  }

  // 常显的那一句按**引擎确认的** options 选句（`prepared.options`，不是用户点的）：勾了源码 / 崩溃报告就如实说附带了什么。
  // 仍然只有一句话（卡片一句话纪律）；多项组合用同一个模板列出。
  const applied = EXTRA_KEYS.filter((k) => prepared?.options?.[k])
  const sentence = applied.length
    ? ds('sentenceExtras', { items: applied.map((k) => ds(`sentenceItem.${k}`)).join(ds('listSeparator')) })
    : ds('sentence')
  const sending = phase === 'sending' || phase === 'cancelling'
  const editable = phase === 'ready' || phase === 'failed'
  const retryable = phase === 'failed' && status?.retryable !== false
  const kinds = Array.from(new Set((prepared?.entries ?? []).map((e) => e.kind)))
  const categories = capability.categories ?? [DEFAULT_CATEGORY]

  const progress =
    status?.stage === 'upload' && status.total > 0
      ? ds('progress.upload', { percent: Math.min(100, Math.floor((status.sent / status.total) * 100)) })
      : ds(`progress.${status?.stage === 'complete' ? 'complete' : 'init'}`)

  // 状态文字住在页脚左侧、与按钮同一行：固定行高（两行 12px），超出截断（完整文字在 title 与 textContent 里）。
  // 收起态对话框因此紧凑，异步结果只换这一格里的字，「发送」按钮不动。
  const line = (tone: 'plain' | 'danger' | 'ok' | 'warn', text: string, extra?: Record<string, string>) => (
    <p
      className={cn(
        'line-clamp-2 min-w-0 text-xs leading-4',
        tone === 'plain' && 'text-ink-3',
        tone === 'danger' && 'text-danger-content',
        tone === 'ok' && 'text-ok-content',
        tone === 'warn' && 'text-warn-content',
      )}
      title={text}
      role={tone === 'danger' ? 'alert' : 'status'}
      {...extra}
    >
      {text}
    </p>
  )
  const failureLine = [
    packageGone ? ds('packageGone') : failureText(status?.code),
    retryText(status?.retry_after),
    packageGone ? null : ds('failureHint'),
  ]
    .filter(Boolean)
    .join(' ')
  const statusNode = (
    <div className="flex h-8 min-w-0 grow-[100] items-center gap-1" data-diag-send-status={phase}>
      {/* 勾选触发的静默刷新（refreshing）不出字：「正在准备」只属于首次备包 */}
      {phase === 'preparing' && line('plain', ds('preparing'))}
      {phase === 'prepareFailed' && line('danger', ds('prepareFailed'))}
      {phase === 'tooLarge' &&
        prepared &&
        line('warn', ds('tooLarge', { size: formatBytes(prepared.size), max: formatBytes(prepared.max_bytes) }))}
      {phase === 'ready' && cancelledNotice && line('plain', ds('cancelled'))}
      {phase === 'sending' && line('plain', progress)}
      {phase === 'cancelling' && line('plain', ds('progress.cancelling'))}
      {phase === 'failed' && line('danger', failureLine, { 'data-diag-send-failure': status?.code ?? '' })}
      {phase === 'unknown' && status?.report_id && (
        <>
          <p className="min-w-0 text-xs leading-4 text-warn-content" role="alert" title={ds('unknownOutcome')} data-diag-send-unknown>
            <span>{ds('unknownOutcome')} </span>
            <code data-diag-report-id className="font-mono text-ink">
              {status.report_id}
            </code>
          </p>
          <CopyButton text={status.report_id} label={ds('copyId')} variant="ghost" appearance="icon" />
        </>
      )}
      {phase === 'done' && status?.report_id && (
        <>
          <p className="min-w-0 text-xs leading-4 text-ok-content" role="status" title={ds('doneBody')}>
            <span>{ds(status.cancel_raced ? 'doneAfterCancel' : 'doneTitle')} · {ds('reportId')} </span>
            <code data-diag-report-id className="font-mono text-ink">
              {status.report_id}
            </code>
          </p>
          <CopyButton text={status.report_id} label={ds('copyId')} variant="ghost" appearance="icon" />
        </>
      )}
    </div>
  )

  const closeOnly = phase === 'done' || phase === 'unknown' || phase === 'prepareFailed' || phase === 'tooLarge'
  const nonRetryableFailure = phase === 'failed' && !retryable

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={ds('title')}
      description={
        <span className="block text-sm leading-5" data-diag-send-sentence>
          {sentence}
        </span>
      }
      size="md"
      anchor="diagnostics-send"
      blockDismiss={sending}
      onEscape={sending ? (phase === 'sending' ? cancelSending : undefined) : undefined}
      footer={{
        start: statusNode,
        secondary: sending ? (
          <Button variant="ghost" size="lg" onClick={cancelSending} disabled={phase === 'cancelling'} data-diag-send-cancel>
            {ds('cancelSending')}
          </Button>
        ) : phase === 'done' || (!packageGone && (closeOnly || nonRetryableFailure)) ? undefined : (
          <Button variant="ghost" size="lg" onClick={() => onOpenChange(false)} data-diag-send-dismiss>
            {ds('close')}
          </Button>
        ),
        primary:
          packageGone && phase === 'failed' ? (
            <Button variant="primary" size="lg" onClick={() => setEpoch((e) => e + 1)} data-diag-send-reprepare>
              {ds('reprepare')}
            </Button>
          ) : closeOnly || nonRetryableFailure ? (
            <Button variant="primary" size="lg" onClick={() => onOpenChange(false)} data-diag-send-close>
              {phase === 'done' ? ds('done') : ds('close')}
            </Button>
          ) : (
            <Button
              variant="primary"
              size="lg"
              loading={phase === 'sending'}
              disabled={phase === 'preparing' || phase === 'cancelling' || phase === 'sending' || refreshing}
              onClick={send}
              data-diag-send-confirm
            >
              {phase === 'failed' ? ds('retry') : ds('send')}
            </Button>
          ),
      }}
    >
      <div className="flex flex-col gap-5" data-diag-send-body>
        {/* 表单语言（2026-10-19，参照 OpenBitFun）：标签在字段上方、间距 6；提示与字数同一行（提示左、字数右）；
            框只给可编辑的控件；诊断包是一组（`FieldGroup`：3% 底、无边）里的一行可展开项，展开的内容与标题文字对齐缩进。
            如实说明要发什么的那一句是对话框副标题（常显）。 */}
        {/* 额外内容（2026-10-21，beta 0.19.0b1 反馈）：三个框不折叠、紧跟副标题那一句，别埋在「查看内容」下面要滚才看到；
            默认全不勾、不记忆；各框的风险短句留在各自下面；右上「全选 / 全不选」一键切换（一次只换一次包）。
            勾选变化 = 换包，但点击本身即时生效（见 `scheduleRefresh`）。 */}
        <section aria-label={ds('extrasTitle')} className="flex flex-col gap-1.5" data-diag-send-extras>
          <div className="flex items-center justify-between gap-2">
            <h3 className="type-section">{ds('extrasTitle')}</h3>
            <Button variant="ghost" size="sm" disabled={!editable} onClick={toggleAllOptions} data-diag-send-extras-all>
              {allOptionsOn ? ds('extrasSelectNone') : ds('extrasSelectAll')}
            </Button>
          </div>
          {EXTRA_KEYS.map((key) => (
            <label key={key} className="flex items-start gap-2 text-sm text-ink-2">
              <Checkbox
                checked={options[key]}
                disabled={!editable}
                onChange={() => toggleOption(key)}
                data-diag-send-extra={key}
                className="mt-0.5"
              />
              <span className="flex min-w-0 flex-col">
                <span className="text-ink">{ds(`extra.${key}`)}</span>
                <span className="type-caption">{ds(`extraHint.${key}`)}</span>
              </span>
            </label>
          ))}
          {optionsLost && (
            <p className="type-caption text-warn-content" role="status" data-diag-send-options-lost>
              {ds('optionsLost')}
            </p>
          )}
        </section>

        <div className="flex flex-col gap-1.5">
          <label htmlFor={noteId} className="type-section">
            {ds('noteLabel')}
          </label>
          <TextArea
            id={noteId}
            aria-label={ds('noteLabel')}
            placeholder={ds('notePlaceholder')}
            value={note}
            rows={4}
            maxRows={8}
            maxLength={noteMax}
            disabled={!editable}
            onChange={(e) => setNote(e.target.value)}
            data-diag-send-note
            className="px-3 py-2"
          />
          <p className="flex items-start justify-between gap-4 type-caption">
            <span>
              {ds('noteAttached')}
              {ds('noteHint')}
            </span>
            <span className="shrink-0 tabular-nums">{ds('noteCount', { count: note.length, max: noteMax })}</span>
          </p>
        </div>

        <div className="flex flex-col gap-1.5" data-diag-send-category>
          <span id={categoryId} className="type-section">
            {ds('categoryLabel')}
          </span>
          <Select
            value={category}
            onChange={setCategory}
            options={categories.map((c) => ({
              value: c,
              label: translate(`settings.diagnostics.send.category.${c}`, { ns: 'dialogs', defaultValue: c }),
            }))}
            ariaLabel={ds('categoryLabel')}
            disabled={!editable}
            className="w-56"
          />
        </div>

        <FieldGroup>
          <Details data-diag-send-details className="px-3 py-1.5 text-sm">
            <Summary className="h-8 text-ink hover:text-ink">
              <span className="flex min-w-0 flex-1 items-center gap-1.5">
                <span>{ds('attachmentTitle')}</span>
                {prepared && (
                  <span className="text-ink-3" data-diag-send-size>
                    · {formatBytes(prepared.size)}
                  </span>
                )}
                <span className="ml-auto text-xs text-ink-3">{ds('details')}</span>
              </span>
            </Summary>
            <div className="flex flex-col gap-3 pb-2 pl-5 pt-1">
              <section aria-label={ds('includedTitle')} className="flex flex-col gap-1">
                <h3 className="type-section">{ds('includedTitle')}</h3>
                {prepared ? (
                  <ul className="flex list-disc flex-col gap-0.5 pl-4 text-xs leading-5 text-ink-2" data-diag-send-kinds>
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
                <p className="type-caption" data-diag-send-error-summary-note>
                  {ds('errorSummaryNote')}
                </p>
              </section>

              <section aria-label={ds('filesTitle', { count: prepared?.entries.length ?? 0 })} className="flex flex-col gap-1" data-diag-send-files-section>
                <Details data-diag-send-files className="text-sm">
                  <Summary className="h-6 text-ink-2 hover:text-ink">
                    {ds('filesTitle', { count: prepared?.entries.length ?? 0 })}
                  </Summary>
                  <ul className="mt-1 flex flex-col gap-0.5 font-mono text-xs text-ink-3" data-diag-send-file-list>
                    {(prepared?.entries ?? []).map((e) => (
                      <li key={e.name} className="flex justify-between gap-3" data-diag-file={e.name}>
                        <span className="min-w-0 truncate">{e.name}</span>
                        <span className="shrink-0">{formatBytes(e.bytes ?? 0)}</span>
                      </li>
                    ))}
                  </ul>
                </Details>
                {prepared && (prepared.truncated?.length ?? 0) > 0 && (
                  <p className="type-caption" data-diag-send-truncated>
                    {ds('truncated', {
                      kinds: (prepared.truncated ?? []).map((c) => ds(`short.${c.kind}`)).join(ds('listSeparator')),
                    })}
                  </p>
                )}
              </section>

              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                {prepared && <span className="type-caption">{ds('size', { size: formatBytes(prepared.size) })}</span>}
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={save}
                  disabled={!prepared?.id || phase === 'done' || refreshing}
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

              <p className="type-caption">
                {ds('bestEffort')} {ds('retention', { days: capability.retention_days ?? 30 })}{' '}
                <a
                  href={PRIVACY_DOC_URL}
                  target="_blank"
                  rel="noreferrer"
                  data-diag-send-policy
                  className="underline underline-offset-2 hover:text-ink"
                >
                  {ds('policy')}
                </a>
              </p>
            </div>
          </Details>
        </FieldGroup>

        {/* 结果出来后（成功 / 未知 / 失败）给一条直接找人的路；其余阶段占位不显示（按钮不跳位）；仅中文界面 */}
        <WeComContactNote
          active={phase === 'done' || phase === 'unknown' || phase === 'failed'}
          withReportId={phase !== 'failed' && !!status?.report_id}
        />
      </div>
    </Dialog>
  )
}
