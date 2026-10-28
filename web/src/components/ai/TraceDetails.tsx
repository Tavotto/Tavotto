import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ChartColumn, ChevronRight } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { fetchExecTrace, fetchExecTraceDiagnostic, type ExecTraceDto, type ExecTraceOperation } from '@/lib/api'
import { supported, toWaterfall } from '@/lib/execTrace'
import { Button } from '../ui/Button'
import { Dialog } from '../ui/Dialog'
import { TraceWaterfall } from './traceWaterfall/TraceWaterfall'

/** 运行中且展开时多久问一次权威快照（SSE 会丢、不承担完整性；收起 / 页面不可见时不问） */
const LIVE_POLL_MS = 1000
/** 运行中的「此刻」多久往前走一格：时间轴与运行中的条跟着长，不必每帧重画整棵树 */
const LIVE_TICK_MS = 250

type Load =
  | { state: 'idle' }
  | { state: 'loading' }
  /** `stale`：会话已经结束、但最后那次取快照失败——手里这份不是终局，不能再当「运行中」往前走 */
  | { state: 'ready'; trace: ExecTraceDto; stale?: boolean }
  | { state: 'none'; reason: 'not_found' | 'disabled' | 'unsupported' }
  | { state: 'error' }

interface TraceTarget {
  kind?: ExecTraceOperation
  taskRef: string
  pj: string | null
  running: boolean
  /** 会话收尾的标记（变了就再问一次终局快照） */
  settledKey?: unknown
}

/**
 * 「执行记录」入口（ADR 0151 PR3）：窄栏里只有一行，点开是一张宽的浮动卡（`TraceDialog`，共用 `ui/Dialog`）。
 * 转录里的一轮与智能修复卡共用它；任务历史的一行从悬浮详情卡里的按钮直接开 `TraceDialog`。
 */
export function TraceDetails({ hidden, ...target }: TraceTarget & { hidden?: boolean }) {
  const { t } = useTranslation('ai')
  const [open, setOpen] = useState(false)
  if (hidden) return null
  return (
    <div data-ai-trace>
      <button
        type="button"
        data-ai-trace-toggle
        aria-expanded={open}
        onClick={() => setOpen(true)}
        className="group/row flex h-7 w-full min-w-0 items-center gap-2 rounded-md text-left text-base text-ink-3 outline-none hover:text-ink-2 focus-visible:focus-ring"
      >
        <ChartColumn aria-hidden size={ICON_SIZE.sm} className="shrink-0" />
        <span className="min-w-0 truncate">{t('trace.state.toggle')}</span>
        <ChevronRight
          aria-hidden
          size={ICON_SIZE.sm}
          className="ms-auto shrink-0 opacity-0 transition-opacity duration-fast group-hover/row:opacity-100 group-focus-visible/row:opacity-100"
        />
      </button>
      <TraceDialog {...target} open={open} onOpenChange={setOpen} />
    </div>
  )
}

/**
 * 宽的执行记录浮动卡。打开才去问后端、才画；关了就停轮询。只翻译、不判断：状态 / 耗时 / 费用全来自快照，缺的就说缺。
 * `taskRef` 是该 `kind` 的任务引用（AI 会话 id / 修复的准备会话 id），`pj` 是那次任务所属的项目（不跟着此刻的项目走）。
 */
export function TraceDialog({
  kind = 'ai_task',
  taskRef,
  pj,
  running,
  settledKey,
  open,
  onOpenChange,
}: TraceTarget & { open: boolean; onOpenChange: (v: boolean) => void }) {
  const { t } = useTranslation('ai')
  const [load, setLoad] = useState<Load>({ state: 'idle' })
  const [nowWall, setNowWall] = useState(() => Date.now() / 1000)
  const [attempt, setAttempt] = useState(0)
  const seq = useRef(0)
  const ref = taskRef
  // 展开 / 会话终局变化 / 运行中定时：问一次权威快照。请求序号挡住旧响应，会话的项目挡串项目（发请求那一刻的 pj）
  useEffect(() => {
    if (!open) return
    let stopped = false
    const ask = async () => {
      const mine = ++seq.current
      try {
        const res = await fetchExecTrace(kind, ref, pj)
        if (stopped || mine !== seq.current) return
        if (!res.available) setLoad({ state: 'none', reason: res.reason })
        else if (!supported(res.trace)) setLoad({ state: 'none', reason: 'unsupported' })
        else
          // 运行中每秒都会再取一次：内容没变就沿用上一份对象（同一个引用），整棵树不重建、行不重画——
          // 只有真的有 Span 开始 / 结束才付重画的钱（CI 慢机上每次重建约 100 ms 长任务）
          setLoad((prev) =>
            prev.state === 'ready' && !prev.stale && sameSnapshot(prev.trace, res.trace)
              ? prev
              : { state: 'ready', trace: res.trace },
          )
      } catch {
        if (stopped || mine !== seq.current) return
        // 运行中偶尔取失败：留着上一份，下一秒再取。会话已结束还失败：上一份就成了过期的，明说并给重试（Codex #1454）
        setLoad((prev) => (prev.state === 'ready' ? (running ? prev : { ...prev, stale: true }) : { state: 'error' }))
      }
    }
    setLoad((prev) => (prev.state === 'ready' ? prev : { state: 'loading' }))
    void ask()
    if (!running) {
      return () => {
        stopped = true
      }
    }
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void ask()
    }, LIVE_POLL_MS)
    return () => {
      stopped = true
      window.clearInterval(timer)
    }
  }, [open, running, kind, ref, pj, settledKey, attempt])

  // 「此刻」只在会话真的还在跑、手里的快照也是运行中时往前走；会话结束后绝不靠旧快照继续画
  const live = running && load.state === 'ready' && !load.stale && load.trace.status === 'running'
  useEffect(() => {
    if (!open || !live) return
    const timer = window.setInterval(() => setNowWall(Date.now() / 1000), LIVE_TICK_MS)
    return () => window.clearInterval(timer)
  }, [open, live])

  const exporter = useExporter(kind, ref, pj)

  const finished = load.state === 'ready' && load.trace.status !== 'running'

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      size="shell"
      anchor="exec-trace"
      title={t('trace.state.toggle')}
      footer={{
        start: finished ? <ExportStatus phase={exporter.phase} /> : undefined,
        secondary: finished ? (
          <Button
            variant="secondary"
            size="lg"
            data-ai-trace-export={exporter.phase}
            onClick={exporter.run}
            disabled={exporter.phase === 'busy'}
          >
            {t('trace.state.export')}
          </Button>
        ) : undefined,
        primary: (
          <Button variant="primary" size="lg" onClick={() => onOpenChange(false)}>
            {t('trace.state.close')}
          </Button>
        ),
      }}
    >
      {open && (
        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          <Body
            load={load}
            nowWall={nowWall}
            onRetry={() => {
              setLoad({ state: 'loading' })
              setAttempt((n) => n + 1)
            }}
          />
        </div>
      )}
    </Dialog>
  )
}

function Body({ load, nowWall, onRetry }: { load: Load; nowWall: number; onRetry: () => void }) {
  const { t } = useTranslation('ai')
  if (load.state === 'idle' || load.state === 'loading') {
    return (
      <p data-ai-trace-state="loading" className="text-sm text-ink-3 text-shimmer">
        {t('trace.state.loading')}
      </p>
    )
  }
  if (load.state === 'error') {
    return (
      <div data-ai-trace-state="error" className="flex items-center gap-2 text-sm text-ink-3">
        <span>{t('trace.state.error')}</span>
        <Button variant="ghost" size="sm" onClick={onRetry}>
          {t('trace.state.retry')}
        </Button>
      </div>
    )
  }
  if (load.state === 'none') {
    return (
      <p data-ai-trace-state={load.reason} className="text-sm text-ink-3">
        {t(`trace.state.${load.reason}`)}
      </p>
    )
  }
  const { trace } = load
  return (
    <>
      {load.stale && (
        <div data-ai-trace-stale className="flex items-center gap-2 text-sm text-ink-3">
          <span>{t('trace.state.stale')}</span>
          <Button variant="ghost" size="sm" onClick={onRetry}>
            {t('trace.state.retry')}
          </Button>
        </div>
      )}
      <Ready trace={trace} nowWall={load.stale ? trace.started_at : nowWall} />
    </>
  )
}

function Ready({ trace, nowWall }: { trace: ExecTraceDto; nowWall: number }) {
  const { t } = useTranslation('ai')
  // 展示模型只随快照变（每秒一次）；「此刻」每 250ms 走一格只经 `now` 传下去，不重建整棵树
  const model = useMemo(() => toWaterfall(trace, trace.started_at), [trace])
  const now = model.running ? Math.max(model.duration, nowWall - trace.started_at) : model.duration
  return (
    <div data-ai-trace-state={trace.status} className="flex flex-col gap-1.5">
      {(trace.truncated || (!trace.complete && trace.status !== 'running')) && (
        <p data-ai-trace-incomplete className="text-sm text-ink-3">
          {trace.truncated
            ? t('trace.state.truncated', { count: trace.dropped_count })
            : trace.status === 'interrupted'
              ? t('trace.state.interrupted')
              : t('trace.state.incomplete')}
        </p>
      )}
      <TraceWaterfall model={model} now={now} />
    </div>
  )
}

type ExportPhase = 'idle' | 'busy' | 'done' | 'gone' | 'error'

/** 用户主动导出这一次的执行追踪诊断（下载 JSON，不上传）。只在任务结束后给：运行中的半份不当诊断交出去。 */
function useExporter(kind: ExecTraceOperation, taskRef: string, pj: string | null) {
  const [phase, setPhase] = useState<ExportPhase>('idle')
  const run = () => {
    setPhase('busy')
    void fetchExecTraceDiagnostic(kind, taskRef, pj)
      .then((res) => {
        if (!res.available) {
          setPhase('gone')
          return
        }
        const url = URL.createObjectURL(res.blob)
        try {
          const a = document.createElement('a')
          a.href = url
          a.download = res.filename
          a.click()
        } finally {
          URL.revokeObjectURL(url)
        }
        setPhase('done')
      })
      .catch(() => setPhase('error'))
  }
  return { phase, run }
}

function ExportStatus({ phase }: { phase: ExportPhase }) {
  const { t } = useTranslation('ai')
  if (phase === 'done') return <span className="text-sm text-ink-3">{t('trace.state.exported')}</span>
  if (phase === 'gone') return <span className="text-sm text-ink-3">{t('trace.state.not_found')}</span>
  if (phase === 'error') return <span className="text-sm text-ink-3">{t('trace.state.error')}</span>
  return null
}

/** 两份快照的内容是否相同（JSON 等价；几百个 Span 也只是毫秒级，比重建整棵树便宜得多） */
function sameSnapshot(a: ExecTraceDto, b: ExecTraceDto): boolean {
  if (a === b) return true
  if (a.trace_id !== b.trace_id || a.last_seq !== b.last_seq || a.spans.length !== b.spans.length) return false
  return JSON.stringify(a) === JSON.stringify(b)
}
