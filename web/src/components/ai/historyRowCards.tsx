import {
  cloneElement,
  useCallback,
  useEffect,
  useRef,
  useState,
  type FocusEvent,
  type MouseEvent,
  type PointerEvent,
  type ReactElement,
  type ReactNode,
} from 'react'
import { useTranslation } from 'react-i18next'
import { AgentIcon } from '@/components/settings/AgentIcon'
import { BrandLogo, hasBrandLogo } from '@/components/settings/BrandLogo'
import { ChartColumn } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { fetchExecTrace, type AiAgentCaps, type AiHistoryEntry } from '@/lib/api'
import { supported, toWaterfall } from '@/lib/execTrace'
import { modelBrandKey } from '@/lib/modelVendor'
import { cn } from '@/lib/utils'
import { Button } from '../ui/Button'
import { Popover } from '../ui/Popover'
import { buildTree, computeMetrics, formatCost, formatSeconds, formatTokens, hasInterval, leafType, walk } from './traceWaterfall/utils'

type Mode = 'closed' | 'hover' | 'pinned'

/**
 * 悬停 / 聚焦 / 点击都能打开的小卡（共用 `ui/Popover`）：
 * 指针停一下（`delay`）出现、离开收起；键盘聚焦（focus-visible）同样出现；点击（触屏的轻点）把它钉住，
 * 钉住后只在点外面 / Esc / 再点一次时关。不抢焦点（悬停开的），钉住后焦点进卡里，卡里的按钮 Tab 得到。
 */
export function HoverPopover({
  trigger,
  children,
  delay = 250,
  width = 284,
  side = 'bottom',
  align = 'start',
  ariaLabel,
}: {
  trigger: ReactElement<Record<string, unknown>>
  children: ReactNode
  delay?: number
  width?: number
  side?: 'top' | 'bottom'
  align?: 'start' | 'center' | 'end'
  ariaLabel?: string
}) {
  const [mode, setMode] = useState<Mode>('closed')
  const modeRef = useRef<Mode>('closed')
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const set = useCallback((m: Mode) => {
    modeRef.current = m
    setMode(m)
  }, [])
  const clear = () => clearTimeout(timer.current)
  useEffect(() => () => clearTimeout(timer.current), [])
  const later = (fn: () => void, ms: number) => {
    clear()
    timer.current = setTimeout(fn, ms)
  }
  const enter = () => {
    clear()
    if (modeRef.current === 'closed') later(() => modeRef.current === 'closed' && set('hover'), delay)
  }
  const leave = () => {
    clear()
    if (modeRef.current === 'hover') later(() => modeRef.current === 'hover' && set('closed'), 140)
    else if (modeRef.current === 'closed') clear()
  }
  const props = trigger.props as {
    onPointerEnter?: (e: PointerEvent) => void
    onPointerLeave?: (e: PointerEvent) => void
    onFocus?: (e: FocusEvent) => void
    onBlur?: (e: FocusEvent) => void
    onClick?: (e: MouseEvent) => void
  }
  const el = cloneElement(trigger, {
    onPointerEnter: (e: PointerEvent) => {
      props.onPointerEnter?.(e)
      if (e.pointerType !== 'touch') enter()
    },
    onPointerLeave: (e: PointerEvent) => {
      props.onPointerLeave?.(e)
      leave()
    },
    onFocus: (e: FocusEvent<HTMLElement>) => {
      props.onFocus?.(e)
      if (e.currentTarget.matches(':focus-visible') && modeRef.current === 'closed') set('hover')
    },
    onBlur: (e: FocusEvent<HTMLElement>) => {
      props.onBlur?.(e)
      if (modeRef.current === 'hover') set('closed')
    },
    onClick: (e: MouseEvent) => {
      props.onClick?.(e)
      // 自己管开合：不让 Radix 的触发器再切一次
      e.preventDefault()
      clear()
      set(modeRef.current === 'pinned' ? 'closed' : 'pinned')
    },
    'aria-expanded': mode !== 'closed',
  })
  return (
    <Popover
      trigger={el}
      open={mode !== 'closed'}
      onOpenChange={(v) => {
        if (!v) set('closed')
      }}
      keepFocus={mode !== 'pinned'}
      width={width}
      side={side}
      align={align}
      ariaLabel={ariaLabel}
    >
      <div className="-m-2 p-2" onPointerEnter={clear} onPointerLeave={leave}>
        {children}
      </div>
    </Popover>
  )
}

// ---- 执行记录摘要（悬浮卡打开时才去取） ----------------------------------------

export interface TraceSummary {
  extent: number
  errors: number
  cost: number | null
  tokens: number | null
  segs: { left: number; width: number; type: 'model' | 'tool' | 'wait' | 'failed' | 'muted' | 'think' | 'delegate' }[]
}

type SummaryState = { state: 'loading' } | { state: 'none' } | { state: 'ready'; summary: TraceSummary }

/** 悬浮卡挂载时取一次权威快照（不为一行文字常驻请求）；取不到 / 没有记录 = none，卡上什么都不说 */
export function useTraceSummary(taskRef: string, pj: string | null): SummaryState {
  const [st, setSt] = useState<SummaryState>({ state: 'loading' })
  useEffect(() => {
    let stopped = false
    fetchExecTrace('ai_task', taskRef, pj)
      .then((res) => {
        if (stopped) return
        if (!res.available || !supported(res.trace)) return setSt({ state: 'none' })
        const model = toWaterfall(res.trace, res.trace.started_at)
        const roots = buildTree(model.spans)
        const m = computeMetrics(roots, model, model.duration)
        const segs: TraceSummary['segs'] = []
        const total = Math.max(m.extent, 1e-6)
        walk(roots, (n) => {
          const { span } = n
          if (span.kind === 'agent' || !hasInterval(span) || span.end === undefined) return
          segs.push({
            left: (span.start / total) * 100,
            width: Math.max(1.5, ((span.end - span.start) / total) * 100),
            type: leafType(span),
          })
        })
        setSt({ state: 'ready', summary: { extent: m.extent, errors: m.errors, cost: m.cost, tokens: m.tokens, segs } })
      })
      .catch(() => !stopped && setSt({ state: 'none' }))
    return () => {
      stopped = true
    }
  }, [taskRef, pj])
  return st
}

const SEG: Record<TraceSummary['segs'][number]['type'], string> = {
  model: 'bg-accent/45',
  tool: 'bg-accent/70',
  think: 'bg-accent/25',
  delegate: 'bg-accent/25',
  wait: 'border border-border-strong bg-transparent',
  failed: 'bg-danger',
  muted: 'bg-ink-faint/40',
}

/** 行里「脚本 / 快照 / 失败原因 / 执行记录缩略条」的详情卡内容 */
export function HistoryDetailsCard({
  entry,
  pj,
  onOpenTrace,
}: {
  entry: AiHistoryEntry
  pj: string | null
  onOpenTrace: () => void
}) {
  const { t } = useTranslation('ai')
  const trace = useTraceSummary(entry.id, pj)
  const failed = entry.status === 'failed' || entry.status === 'timeout' || entry.status === 'interrupted'
  const script = entry.script?.split(/[\\/]/).pop()
  return (
    <div data-ai-history-card="details" className="flex flex-col gap-3 p-1 text-sm text-ink-2">
      {failed && entry.error && (
        <p data-ai-history-error className="m-0 text-danger-content [overflow-wrap:anywhere] whitespace-pre-wrap">
          {entry.error}
        </p>
      )}
      <dl className="m-0 grid grid-cols-[3.25rem_1fr] gap-x-2 gap-y-1.5">
        <dt className="text-ink-3">{t('panel.scriptLabel')}</dt>
        <dd className="m-0 min-w-0">
          <code className="inline-block max-w-full truncate rounded-xs bg-surface-2 px-1 align-bottom font-mono text-ink-2">
            {script || t('panel.none')}
          </code>
        </dd>
        <dt className="text-ink-3">{t('history.snapshot')}</dt>
        <dd className="m-0 text-ink">
          {t(entry.revert_available ? 'history.snapshotAvailableShort' : 'history.snapshotClearedShort')}
        </dd>
      </dl>
      {trace.state === 'ready' && (
        <div data-ai-history-strip className="flex flex-col gap-1.5 border-t border-border pt-2.5">
          <div className="flex items-baseline justify-between">
            <span className="text-ink-3">{t('trace.state.toggle')}</span>
            <b className="font-medium text-ink tabular-nums">
              {formatSeconds(trace.summary.extent)}
              {trace.summary.errors > 0 && ` · ${t('trace.ui.metric.errors')} ${trace.summary.errors}`}
            </b>
          </div>
          <div className="relative h-2 overflow-hidden rounded-xs bg-accent/15">
            {trace.summary.segs.map((s, i) => (
              <span
                key={i}
                className={cn('absolute inset-y-0 rounded-xs', SEG[s.type])}
                style={{ left: `${s.left}%`, width: `${s.width}%` }}
              />
            ))}
          </div>
        </div>
      )}
      <Button variant="secondary" size="md" data-ai-history-trace className="w-full" onClick={onOpenTrace}>
        <ChartColumn aria-hidden size={ICON_SIZE.sm} />
        {t('history.openTrace')}
      </Button>
    </div>
  )
}

const LEVEL: Record<string, number> = { minimal: 1, low: 1, medium: 2, high: 3, xhigh: 3, max: 3, ultra: 3 }

/**
 * 这一条记录的标志（无框）：先认**型号**的厂商（`deepseek-v4-pro` → DeepSeek，不管是哪个执行器代跑的），
 * 认不出再用它接管的第三方接口的图标键，最后才是执行器自己的图标（Codex / Claude Code 没带型号时）。
 */
export function EntryLogo({
  entry,
  agent,
  iconKey,
  size,
}: {
  entry: AiHistoryEntry
  agent: AiAgentCaps | undefined
  iconKey: string
  size: number
}) {
  const endpointKey = agent?.endpoint?.icon_key
  const brand = modelBrandKey(entry.model) ?? (hasBrandLogo(endpointKey) ? endpointKey : null)
  // 没有可认的厂商：执行器自己的图标（Claude 的橙色标志、Codex 的 OpenAI 标志；没有标志的落到通用图标）
  if (!brand) return <AgentIcon iconKey={iconKey} size={size} glyphSize={size} className="text-current" />
  return <BrandLogo logo={brand} size={size} />
}

/** 元信息最前面的 Agent 标（无框）与它的模型卡 */
export function AgentBadge({
  entry,
  agent,
  name,
  pj,
}: {
  entry: AiHistoryEntry
  agent: AiAgentCaps | undefined
  name: string
  pj: string | null
}) {
  const iconKey = agent?.icon_key ?? entry.provider
  return (
    <HoverPopover
      delay={150}
      width={224}
      side="top"
      ariaLabel={name}
      trigger={
        <button
          type="button"
          data-ai-history-agent
          aria-label={name}
          className="flex size-5 shrink-0 items-center justify-center rounded-sm text-ink-2 outline-none focus-visible:focus-ring"
        >
          <EntryLogo entry={entry} agent={agent} iconKey={iconKey} size={ICON_SIZE.md} />
        </button>
      }
    >
      <ModelCard entry={entry} agent={agent} name={name} iconKey={iconKey} pj={pj} />
    </HoverPopover>
  )
}

function ModelCard({
  entry,
  agent,
  name,
  iconKey,
  pj,
}: {
  entry: AiHistoryEntry
  agent: AiAgentCaps | undefined
  name: string
  iconKey: string
  pj: string | null
}) {
  const { t } = useTranslation('ai')
  const trace = useTraceSummary(entry.id, pj)
  // 连接方式只在 caps 里查得到这家 Agent 时说：接管了第三方接口 = 接口，否则 = 本机 CLI 的登录态
  const via = agent ? t(agent.endpoint ? 'history.via.endpoint' : 'history.via.cli') : null
  const level = entry.effort ? (LEVEL[entry.effort] ?? 0) : 0
  const usage = trace.state === 'ready' ? trace.summary : null
  return (
    <div data-ai-history-card="model" className="flex flex-col gap-2 p-1">
      <div className="flex items-center gap-2">
        <EntryLogo entry={entry} agent={agent} iconKey={iconKey} size={ICON_SIZE.lg} />
        <div className="min-w-0">
          <b className="block truncate text-base font-medium text-ink">{name}</b>
          {via && <small className="block text-xs text-ink-3">{via}</small>}
        </div>
      </div>
      <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
        <dt className="text-ink-3">{t('history.model')}</dt>
        <dd data-ai-history-model className="m-0 min-w-0 truncate text-end text-ink tabular-nums">
          {entry.model || t('history.modelDefault')}
        </dd>
        <dt className="text-ink-3">{t('history.effortTitle')}</dt>
        <dd className="m-0 text-end text-ink">
          {entry.effort ? (
            <span className="inline-flex items-center gap-1.5">
              {t(`effortLabel.${entry.effort}`, { defaultValue: entry.effort })}
              <span aria-hidden className="inline-flex gap-0.5">
                {[1, 2, 3].map((i) => (
                  <i key={i} className={cn('h-2 w-1 rounded-xs', i <= level ? 'bg-accent' : 'bg-border')} />
                ))}
              </span>
            </span>
          ) : (
            '—'
          )}
        </dd>
        {usage?.tokens != null && (
          <>
            <dt className="text-ink-3">{t('trace.ui.metric.tokens')}</dt>
            <dd className="m-0 text-end text-ink tabular-nums">{formatTokens(usage.tokens)}</dd>
          </>
        )}
        {usage?.cost != null && (
          <>
            <dt className="text-ink-3">{t('trace.ui.metric.cost')}</dt>
            <dd className="m-0 text-end text-ink tabular-nums">≈ {formatCost(usage.cost)}</dd>
          </>
        )}
      </dl>
    </div>
  )
}
