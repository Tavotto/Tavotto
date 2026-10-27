import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ChartColumn, ChevronRight } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { fetchExecTrace, type ExecTraceDto } from '@/lib/api'
import { supported, toWaterfall } from '@/lib/execTrace'
import { cn } from '@/lib/utils'
import { Button } from '../ui/Button'
import { Reveal } from '../ui/Field'
import { TraceWaterfall } from './traceWaterfall/TraceWaterfall'

/** 运行中且展开时多久问一次权威快照（SSE 会丢、不承担完整性；收起 / 页面不可见时不问） */
const LIVE_POLL_MS = 1000
/** 运行中的「此刻」多久往前走一格：时间轴与运行中的条跟着长，不必每帧重画整棵树 */
const LIVE_TICK_MS = 250

type Load =
  | { state: 'idle' }
  | { state: 'loading' }
  | { state: 'ready'; trace: ExecTraceDto }
  | { state: 'none'; reason: 'not_found' | 'disabled' | 'unsupported' }
  | { state: 'error' }

/**
 * 「执行记录」（ADR 0151 PR3）：一次 AI 任务的完整执行追踪。默认收起，普通用户看到的仍是那一行过程与结局；
 * 展开才去问后端、才画 Waterfall。只翻译、不判断：状态 / 耗时 / 费用全来自快照，缺的就说缺。
 * 转录里的一轮与任务历史的一行共用它：`taskRef` 是 AI 会话 id，`pj` 是那次任务所属的项目（不跟着此刻的项目走）。
 */
export function TraceDetails({
  taskRef,
  pj,
  running,
  settledKey,
  hidden,
}: {
  taskRef: string
  pj: string | null
  running: boolean
  /** 会话收尾的标记（变了就再问一次终局快照） */
  settledKey?: unknown
  /** 还没拿到后端会话 id 的占位轮：不显示 */
  hidden?: boolean
}) {
  const { t } = useTranslation('ai')
  const [open, setOpen] = useState(false)
  const [load, setLoad] = useState<Load>({ state: 'idle' })
  const [nowWall, setNowWall] = useState(() => Date.now() / 1000)
  const [attempt, setAttempt] = useState(0)
  const seq = useRef(0)
  const ref = taskRef

  // 展开 / 会话终局变化 / 运行中定时：问一次权威快照。请求序号挡住旧响应，会话的项目挡串项目（发请求那一刻的 pj）
  useEffect(() => {
    if (!open || hidden) return
    let stopped = false
    const ask = async () => {
      const mine = ++seq.current
      try {
        const res = await fetchExecTrace('ai_task', ref, pj)
        if (stopped || mine !== seq.current) return
        if (!res.available) setLoad({ state: 'none', reason: res.reason })
        else if (!supported(res.trace)) setLoad({ state: 'none', reason: 'unsupported' })
        else setLoad({ state: 'ready', trace: res.trace })
      } catch {
        if (!stopped && mine === seq.current) setLoad((prev) => (prev.state === 'ready' ? prev : { state: 'error' }))
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
  }, [open, running, ref, pj, hidden, settledKey, attempt])

  const live = load.state === 'ready' && load.trace.status === 'running'
  useEffect(() => {
    if (!open || !live) return
    const timer = window.setInterval(() => setNowWall(Date.now() / 1000), LIVE_TICK_MS)
    return () => window.clearInterval(timer)
  }, [open, live])

  if (hidden) return null

  return (
    <div data-ai-trace>
      <button
        type="button"
        data-ai-trace-toggle
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="group/row flex h-7 w-full min-w-0 items-center gap-2 rounded-md text-left text-base text-ink-3 outline-none hover:text-ink-2 focus-visible:focus-ring"
      >
        <span aria-hidden className="grid size-3.5 shrink-0 place-items-center">
          <ChartColumn size={ICON_SIZE.sm} className="col-start-1 row-start-1 transition-opacity duration-fast group-hover/row:opacity-0" />
          <ChevronRight
            size={ICON_SIZE.sm}
            className={cn(
              'col-start-1 row-start-1 opacity-0 transition-[opacity,transform] duration-fast group-hover/row:opacity-100',
              open && 'rotate-90',
            )}
          />
        </span>
        <span className="min-w-0 truncate">{t('trace.state.toggle')}</span>
      </button>
      <Reveal open={open}>
        <div className="pt-1 pb-2">{open && <Body load={load} nowWall={nowWall} onRetry={() => {
              setLoad({ state: 'loading' })
              setAttempt((n) => n + 1)
            }} />}</div>
      </Reveal>
    </div>
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
  return <Ready trace={trace} nowWall={nowWall} />
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
