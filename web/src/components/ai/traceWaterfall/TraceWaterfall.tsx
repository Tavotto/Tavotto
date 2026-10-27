import {
  memo,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type HTMLAttributes,
  type PointerEvent as ReactPointerEvent,
} from 'react'
import { useTranslation } from 'react-i18next'
import { Badge } from '@/components/ui/Badge'
import { ArrowRight, ChevronDown, LoaderCircle, RotateCcw, SearchX, TriangleAlert, X } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { Kbd } from '@/components/ui/Kbd'
import { SearchInput } from '@/components/ui/SearchInput'
import { cn, MOD } from '@/lib/utils'
import { Handoffs } from './Handoffs'
import { MorphText } from './MorphText'
import { SpanIcon } from './SpanIcon'
import { SpanPopover } from './SpanPopover'
import type { WaterfallModel } from './types'
import {
  LANES,
  ROW,
  buildTree,
  computeMetrics,
  endOf,
  formatCost,
  formatSeconds,
  formatTick,
  formatTokens,
  hasInterval,
  isRunning,
  laneSegments,
  leafType,
  matchingIds,
  pct,
  spanFigure,
  subtreeOpen,
  ticksFor,
  traceDomain,
  walk,
  type LaneSegment,
  type SegType,
  type TraceNode,
} from './utils'

export interface TraceWaterfallProps extends Omit<HTMLAttributes<HTMLDivElement>, 'children'> {
  model: WaterfallModel
  /** 距 Trace 起点的秒数；没有结束时刻的 span 画到这里 */
  now: number
}

const MIN_ZOOM = 1
type Scope = 'lane' | 'step'
type Details = { id: string; scope: Scope; anchor: HTMLElement }
type View = { start: number; domain: number }
const clampSplit = (p: number) => Math.min(70, Math.max(20, p))

export function TraceWaterfall({ model, now, className, style, ...props }: TraceWaterfallProps) {
  const { t } = useTranslation('ai')
  const rootRef = useRef<HTMLDivElement>(null)
  const bodyRef = useRef<HTMLDivElement>(null)
  const viewportRef = useRef<HTMLDivElement>(null)
  const axisRef = useRef<HTMLSpanElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const hovered = useRef(false)
  const following = useRef(true)

  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set())
  const [query, setQuery] = useState('')
  const [split, setSplit] = useState<number | null>(null)
  const [track, setTrack] = useState({ left: 0, width: 0 })
  const [details, setDetails] = useState<Details | null>(null)
  const [zoom, setZoom] = useState<View | null>(null)

  const roots = useMemo(() => buildTree(model.spans), [model.spans])
  const byId = useMemo(() => {
    const map = new Map<string, TraceNode>()
    walk(roots, (n) => map.set(n.span.id, n))
    return map
  }, [roots])
  const metrics = useMemo(() => computeMetrics(roots, model, now), [roots, model, now])
  const matches = useMemo(() => matchingIds(roots, query), [roots, query])
  const whole = traceDomain(metrics.extent, model.running)
  const view: View = useMemo(() => zoom ?? { start: 0, domain: whole }, [zoom, whole])
  const ticks = useMemo(() => ticksFor(view.start, view.domain), [view])
  const running = useCallback((n: TraceNode) => isRunning(n.span, model), [model])

  // 搜索时，折叠的智能体里的命中项也要露出来
  const isOpen = (n: TraceNode) => matches !== null || !collapsed.has(n.span.id)
  const isShown = (n: TraceNode) => matches === null || matches.has(n.span.id)

  const flat = useMemo(() => {
    const out: TraceNode[] = []
    const visit = (nodes: TraceNode[]) => {
      for (const n of nodes) {
        if (matches !== null && !matches.has(n.span.id)) continue
        out.push(n)
        if (matches !== null || !collapsed.has(n.span.id)) visit(n.children)
      }
    }
    visit(roots)
    return out
  }, [roots, collapsed, matches])

  // ---- 测量 -----------------------------------------------------------------

  useLayoutEffect(() => {
    const el = axisRef.current
    const body = bodyRef.current
    if (!el || !body || typeof ResizeObserver === 'undefined') return
    const measure = () => setTrack({ left: el.offsetLeft, width: el.offsetWidth })
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    ro.observe(body)
    return () => ro.disconnect()
  }, [split])

  /** 标出真正吸住的智能体行（只遍历带 data-trace-pinned 的行） */
  const syncScroll = useCallback(() => {
    const vp = viewportRef.current
    if (!vp) return
    const vpTop = vp.getBoundingClientRect().top
    vp.querySelectorAll<HTMLElement>('[data-trace-pinned]').forEach((row) => {
      const natural = row.parentElement!.getBoundingClientRect().top - vpTop
      row.toggleAttribute('data-stuck', natural < Number(row.dataset.top) - 0.5)
    })
  }, [])
  useLayoutEffect(syncScroll)

  // 新步骤出现时跟到底部，除非读者往上翻了
  const started = model.spans.length
  useLayoutEffect(() => {
    const vp = viewportRef.current
    if (vp && following.current) vp.scrollTop = vp.scrollHeight
  }, [started])

  // ---- 输入 -----------------------------------------------------------------

  // ⌘F / Ctrl+F 只在指针悬停或焦点在组件内时抢过来
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey) || e.key.toLowerCase() !== 'f') return
      if (!hovered.current && !rootRef.current?.contains(document.activeElement)) return
      e.preventDefault()
      searchRef.current?.focus()
      searchRef.current?.select()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // ⌘/Ctrl + 滚轮绕指针缩放；放大后横向滚轮平移
  const viewRef = useRef({ view, whole, track })
  viewRef.current = { view, whole, track }
  useEffect(() => {
    const body = bodyRef.current
    if (!body) return
    const onWheel = (e: WheelEvent) => {
      const { view: v, whole: w, track: tr } = viewRef.current
      const zooming = e.ctrlKey || e.metaKey
      const panning = !zooming && v.domain < w && Math.abs(e.deltaX) > Math.abs(e.deltaY)
      if (!zooming && !panning) return
      e.preventDefault()
      const px = e.clientX - body.getBoundingClientRect().left - tr.left
      const f = Math.min(1, Math.max(0, px / Math.max(1, tr.width)))
      let { start, domain } = v
      if (zooming) {
        const pivot = start + f * domain
        domain = Math.min(w, Math.max(MIN_ZOOM, domain * Math.exp(e.deltaY * 0.01)))
        start = pivot - f * domain
      } else {
        start += (e.deltaX / Math.max(1, tr.width)) * domain
      }
      start = Math.min(Math.max(0, start), w - domain)
      setZoom(domain >= w - 1e-3 ? null : { start, domain })
    }
    body.addEventListener('wheel', onWheel, { passive: false })
    return () => body.removeEventListener('wheel', onWheel)
  }, [])

  const toggle = useCallback(
    (id: string) =>
      setCollapsed((prev) => {
        const next = new Set(prev)
        if (!next.delete(id)) next.add(id)
        return next
      }),
    [],
  )

  const openDetails = useCallback(
    (node: TraceNode, scope: Scope, anchor: HTMLElement) =>
      setDetails((d) => (d?.id === node.span.id && d.scope === scope ? null : { id: node.span.id, scope, anchor })),
    [],
  )
  const closeDetails = useCallback(() => setDetails(null), [])

  const startResize = (e: ReactPointerEvent<HTMLDivElement>) => {
    const body = bodyRef.current
    if (!body) return
    e.preventDefault()
    const rect = body.getBoundingClientRect()
    const move = (ev: PointerEvent) => setSplit(clampSplit(((ev.clientX - rect.left) / rect.width) * 100))
    const up = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  const startPan = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (!zoom) return
    e.preventDefault()
    const x0 = e.clientX
    const start0 = zoom.start
    const move = (ev: PointerEvent) => {
      const { whole: w, track: tr } = viewRef.current
      const start = start0 + ((ev.clientX - x0) / Math.max(1, tr.width)) * w
      setZoom((z) => z && { ...z, start: Math.min(Math.max(0, start), w - z.domain) })
    }
    const up = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  // ---- 渲染 -----------------------------------------------------------------

  const empty = matches?.size === 0
  const detailsNode = details ? byId.get(details.id) : undefined
  const dash = t('trace.ui.unknown')

  const renderLevel = (nodes: TraceNode[]) =>
    nodes.filter(isShown).map((node) => {
      const hasChildren = node.children.length > 0
      const open = hasChildren && isOpen(node)
      return (
        <li key={node.span.id} className="motion-safe:animate-settle-in">
          <Row
            node={node}
            open={open}
            hasChildren={hasChildren}
            ticks={ticks}
            view={view}
            now={now}
            model={model}
            slowest={node.span.id === metrics.slowestId}
            details={details}
            onToggle={toggle}
            onOpen={openDetails}
          />
          {open && (
            <ul>{renderLevel(node.children)}</ul>
          )}
        </li>
      )
    })

  const bodyStyle = split === null ? undefined : ({ '--trace-label': `${split}%` } as CSSProperties)
  const splitNow = Math.round(split ?? (track.width ? (track.left / (track.left + track.width)) * 100 : 40))

  return (
    <div
      ref={rootRef}
      data-trace-waterfall
      data-running={model.running || undefined}
      onPointerEnter={() => (hovered.current = true)}
      onPointerLeave={() => (hovered.current = false)}
      className={cn('@container/trace w-full overflow-hidden rounded-lg border border-border bg-surface text-base text-ink', className)}
      style={style}
      {...props}
    >
      {/* 头部：标题 + 搜索 */}
      <div className="flex items-center gap-3 border-b border-border px-4 py-2.5">
        <span className="type-title">{t('trace.ui.title')}</span>
        <SearchInput
          ref={searchRef}
          data-trace-search
          value={query}
          onValueChange={setQuery}
          aria-label={t('trace.ui.search.label')}
          placeholder={t('trace.ui.search.placeholder')}
          spellCheck={false}
          autoComplete="off"
          className="ms-auto max-w-64 flex-none basis-1/3"
        />
        <span aria-hidden className="hidden items-center gap-0.5 @lg/trace:inline-flex">
          <Kbd>{MOD}</Kbd>
          <Kbd>F</Kbd>
        </span>
      </div>

      {/* 指标条：未知显示 —，绝不写 0 */}
      <dl data-trace-metrics className="grid grid-cols-3 gap-px bg-border @xl/trace:grid-cols-6">
        <Metric id="duration" label={t('trace.ui.metric.duration')} value={formatSeconds(metrics.extent)} />
        <Metric id="cost" label={t('trace.ui.metric.cost')} value={metrics.cost === null ? dash : formatCost(metrics.cost)} />
        <Metric id="tokens" label={t('trace.ui.metric.tokens')} value={metrics.tokens === null ? dash : formatTokens(metrics.tokens)} />
        <Metric id="modelCalls" label={t('trace.ui.metric.modelCalls')} value={metrics.llmCalls == null ? '—' : String(metrics.llmCalls)} />
        <Metric id="toolCalls" label={t('trace.ui.metric.toolCalls')} value={metrics.toolCalls == null ? '—' : String(metrics.toolCalls)} />
        <Metric id="errors" label={t('trace.ui.metric.errors')} value={String(metrics.errors)} />
      </dl>

      {/* 主体 */}
      <div
        ref={bodyRef}
        data-trace-body
        data-empty={empty || undefined}
        className="group/body relative border-t border-border [--trace-label:52%] @xs/trace:[--trace-label:46%] @xl/trace:[--trace-label:40%] @3xl/trace:[--trace-label:30%]"
        style={bodyStyle}
      >
        {/* 网格线 */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-y-0 start-(--trace-label) end-0 border-s border-border group-data-empty/body:opacity-0"
        >
          <GridLines ticks={ticks} view={view} />
        </div>

        {/* 坐标轴 */}
        <div className="relative flex h-9 items-center border-b border-border" onDoubleClick={() => setZoom(null)}>
          <span className="flex w-(--trace-label) shrink-0 items-center ps-4 text-xs text-ink-3">{t('trace.ui.steps')}</span>
          <span ref={axisRef} className="relative h-full flex-1">
            <span className="absolute inset-0 overflow-hidden">
              {ticks
                .filter((x) => (x - view.start) / view.domain < 0.94)
                .map((x) => (
                  <span
                    key={x}
                    data-trace-tick
                    className="absolute top-1/2 -translate-y-1/2 ps-2 text-xs text-ink-3 tabular-nums"
                    style={{ left: `${pct(x, view)}%` }}
                  >
                    {formatTick(x)}
                  </span>
                ))}
            </span>
          </span>
        </div>

        {/* 行 */}
        <div className="relative group-data-empty/body:hidden">
          <div
            ref={viewportRef}
            data-trace-viewport
            onScroll={(e) => {
              const el = e.currentTarget
              following.current = el.scrollHeight - el.scrollTop - el.clientHeight < ROW
              syncScroll()
            }}
            className="max-h-112 overflow-y-auto overscroll-contain"
          >
            <div className="relative">
              <Handoffs flat={flat} view={view} track={track} now={now} running={running} />
              <ul aria-label={t('trace.ui.treeLabel')}>{renderLevel(roots)}</ul>
            </div>
          </div>
          {/* 现在线：只在运行中画 */}
          {model.running && (
            <div aria-hidden className="pointer-events-none absolute inset-y-0 start-(--trace-label) end-0 overflow-hidden">
              <span
                data-trace-now
                className="absolute inset-y-0 w-px bg-accent"
                style={{ left: `${pct(Math.max(now, metrics.extent), view)}%` }}
              />
            </div>
          )}
        </div>

        {empty && (
          <div data-trace-empty className="flex flex-col items-center gap-1 px-6 py-10 text-center motion-safe:animate-fade-in">
            <SearchX size={ICON_SIZE.lg} aria-hidden className="text-ink-3" />
            <div className="type-title">{t('trace.ui.empty.title')}</div>
            <div className="text-ink-2">{t('trace.ui.empty.body', { query: query.trim() })}</div>
          </div>
        )}

        <div
          role="separator"
          aria-orientation="vertical"
          aria-label={t('trace.ui.resize')}
          aria-valuemin={20}
          aria-valuemax={70}
          aria-valuenow={splitNow}
          tabIndex={0}
          data-trace-separator
          onPointerDown={startResize}
          onDoubleClick={() => setSplit(null)}
          onKeyDown={(e) => {
            const d = e.key === 'ArrowLeft' ? -2 : e.key === 'ArrowRight' ? 2 : 0
            if (!d) return
            e.preventDefault()
            setSplit(clampSplit(splitNow + d))
          }}
          className="group/sep absolute inset-y-0 start-(--trace-label) z-canvas-chrome w-2 -translate-x-1/2 cursor-col-resize outline-none"
        >
          <span className="mx-auto block h-full w-px bg-transparent group-hover/sep:bg-border-strong group-focus-visible/sep:bg-accent" />
        </div>
      </div>

      {/* 缩放滚动条 */}
      {zoom && (
        <div className="relative h-3 border-t border-border">
          <div className="absolute inset-y-[3px] start-(--trace-label) end-3 ms-3">
            <div
              data-trace-scrubber
              onPointerDown={startPan}
              onDoubleClick={() => setZoom(null)}
              className="absolute inset-y-0 min-w-6 cursor-grab touch-none rounded-full bg-border-strong hover:bg-border-control active:cursor-grabbing"
              style={{ left: `${(zoom.start / whole) * 100}%`, width: `${(zoom.domain / whole) * 100}%` }}
            />
          </div>
        </div>
      )}

      {/* 图例 */}
      <Legend />

      {details && detailsNode && details.anchor.isConnected && (
        <SpanPopover
          node={detailsNode}
          anchor={details.anchor}
          running={running(detailsNode)}
          now={now}
          slowest={detailsNode.span.id === metrics.slowestId}
          onClose={closeDetails}
        />
      )}
    </div>
  )
}

function GridLines({ ticks, view }: { ticks: number[]; view: View }) {
  return (
    <div className="absolute inset-0 overflow-hidden">
      {ticks.map((x) => (
        <span key={x} className="absolute inset-y-0 w-px bg-border" style={{ left: `${pct(x, view)}%` }} />
      ))}
    </div>
  )
}

function Metric({ id, label, value }: { id: string; label: string; value: string }) {
  return (
    <div data-trace-metric={id} className="flex min-w-0 flex-col gap-0.5 bg-surface px-4 py-2.5 @xl/trace:px-3 @3xl/trace:px-4">
      <dt className="truncate text-xs text-ink-3">{label}</dt>
      <dd data-value={value} className="text-lg">
        <MorphText value={value} />
      </dd>
    </div>
  )
}

interface RowProps {
  node: TraceNode
  open: boolean
  hasChildren: boolean
  ticks: number[]
  view: View
  now: number
  model: WaterfallModel
  slowest: boolean
  details: Details | null
  onToggle: (id: string) => void
  onOpen: (node: TraceNode, scope: Scope, anchor: HTMLElement) => void
}

/**
 * 一行。`now` 只影响还没结束的 Span：已结束的行在「此刻」走一格时不重画（运行中的大 Trace 每秒四格，689 行全重画会卡住输入）。
 * 其余任何一个属性换了（展开、缩放、选中、快照更新）照常重画。
 */
const Row = memo(RowImpl, (a, b) => {
  for (const k of Object.keys(b) as (keyof RowProps)[]) {
    if (k === 'now') continue
    if (a[k] !== b[k]) return false
  }
  return a.now === b.now || !subtreeOpen(b.node)
})

function RowImpl({ node, open, hasChildren, ticks, view, now, model, slowest, details, onToggle, onOpen }: RowProps) {
  const { t } = useTranslation('ai')
  const { span, depth } = node
  const isAgent = span.kind === 'agent'
  const pinned = isAgent && hasChildren && open
  const top = depth * ROW
  const live = isRunning(span, model)
  const failed = span.status === 'error'
  const popupOpen = (scope: Scope, id = span.id) => details?.id === id && details.scope === scope
  const end = endOf(span, now)

  const label = (
    <>
      {hasChildren && (
        <ChevronDown
          size={ICON_SIZE.xs}
          aria-hidden
          className={cn('shrink-0 text-ink-3 transition-[rotate]', open ? '' : '-rotate-90')}
        />
      )}
      <SpanIcon icon={span.icon} />
      <span className="flex min-w-0 flex-1 items-center gap-1.5">
        {(live || failed) && (
          <span className="flex shrink-0 items-center" data-trace-status-glyph={live ? 'running' : 'error'}>
            {live ? (
              <LoaderCircle size={ICON_SIZE.sm} aria-hidden className="text-ink-3 motion-safe:animate-spin" />
            ) : (
              <TriangleAlert size={ICON_SIZE.sm} aria-hidden className="text-danger" />
            )}
          </span>
        )}
        <span className={cn('truncate', isAgent && 'font-medium', failed && 'text-danger-content')}>{span.name}</span>
        {span.detail && <span className="hidden min-w-0 truncate text-ink-3 @lg/trace:inline">{span.detail}</span>}
      </span>
    </>
  )

  const figureLeft = span.timing === 'point' ? span.start : end

  return (
    <div
      data-trace-row
      data-trace-span-id={span.id}
      data-kind={span.kind}
      data-status={span.status ?? 'none'}
      data-timing={span.timing}
      data-running={live || undefined}
      data-trace-pinned={pinned || undefined}
      data-top={top}
      data-popup-open={popupOpen('lane') || undefined}
      className={cn(
        'group/row relative flex h-9 items-center border-b border-transparent hover:bg-surface-hover data-popup-open:bg-surface-hover',
        pinned && 'sticky z-sticky data-stuck:border-border data-stuck:bg-surface',
      )}
      style={pinned ? { top } : undefined}
    >
      {pinned && (
        <div
          aria-hidden
          className="pointer-events-none absolute inset-y-0 start-(--trace-label) end-0 border-s border-border opacity-0 group-data-stuck/row:opacity-100"
        >
          <GridLines ticks={ticks} view={view} />
        </div>
      )}

      {hasChildren ? (
        <button
          type="button"
          data-trace-toggle
          aria-expanded={open}
          aria-label={t(open ? 'trace.ui.toggle.collapse' : 'trace.ui.toggle.expand', { name: span.name })}
          onClick={() => onToggle(span.id)}
          style={{ paddingInlineStart: 8 + depth * 16 }}
          className="relative flex h-full w-(--trace-label) shrink-0 items-center gap-1.5 pe-3 text-start outline-none focus-visible:focus-ring-inset"
        >
          {label}
        </button>
      ) : (
        <div
          className="relative flex h-full w-(--trace-label) shrink-0 items-center gap-1.5 pe-3"
          style={{ paddingInlineStart: 8 + depth * 16 + (isAgent ? 0 : ICON_SIZE.xs + 6) }}
        >
          {label}
        </div>
      )}

      <div data-trace-timeline className="relative h-full flex-1">
        <div className="absolute inset-0 overflow-hidden">
          {/* 整段的触发区在前：泳道里子步骤的触发区后渲染、叠在它上面，点彩色的子段打开的是那一步（Codex #1454） */}
          <Trigger
            node={node}
            view={view}
            now={now}
            scope="lane"
            open={popupOpen('lane')}
            onOpen={(el) => onOpen(node, 'lane', el)}
          />
          {isAgent ? (
            <AgentLane node={node} view={view} now={now} model={model} details={details} onOpen={onOpen} />
          ) : (
            <LeafMarks node={node} view={view} now={now} live={live} />
          )}
          <span
            className="pointer-events-none absolute top-1/2 hidden -translate-y-1/2 items-center gap-1.5 ps-2 text-xs whitespace-nowrap text-ink-3 tabular-nums @xs/trace:flex"
            style={{ left: `${pct(figureLeft, view)}%` }}
          >
            <span data-trace-figure>{spanFigure(node, now)}</span>
            {span.firstFailure && (
              <Badge tone="danger" data-trace-badge="first-failure" className="hidden gap-1 @sm/trace:inline-flex">
                <TriangleAlert size={ICON_SIZE.xs} aria-hidden />
                {t('trace.ui.badge.firstFailure')}
              </Badge>
            )}
            {slowest && (
              <Badge data-trace-badge="slowest" className="hidden motion-safe:animate-pop-in @sm/trace:inline-flex">
                {t('trace.ui.badge.slowest')}
              </Badge>
            )}
          </span>
        </div>
      </div>
    </div>
  )
}

/** 叶子行：实心条 / 虚线框 / 点 */
function LeafMarks({ node, view, now, live }: { node: TraceNode; view: View; now: number; live: boolean }) {
  const { span } = node
  if (span.timing === 'point') return <Marker node={node} view={view} />
  if (span.timing === 'unmeasured') return <Bar node={node} view={view} start={span.start} end={endOf(span, now)} type="muted" dashed />
  return (
    <Bar
      node={node}
      view={view}
      start={span.start}
      end={endOf(span, now)}
      type={leafType(span)}
      running={live}
      retry={span.kind === 'model' && span.retry}
    />
  )
}

/** 智能体泳道：分段条 + 每段的触发钮；point / unmeasured 的子项另标 */
function AgentLane({
  node,
  view,
  now,
  model,
  details,
  onOpen,
}: {
  node: TraceNode
  view: View
  now: number
  model: WaterfallModel
  details: Details | null
  onOpen: (node: TraceNode, scope: Scope, anchor: HTMLElement) => void
}) {
  const { span } = node
  if (span.timing === 'point') return <Marker node={node} view={view} />
  if (span.timing === 'unmeasured') return <Bar node={node} view={view} start={span.start} end={endOf(span, now)} type="muted" dashed lane />
  const segs: LaneSegment[] = laneSegments(node, now, model)
  const extras = node.children.filter((c) => c.span.kind !== 'agent' && !hasInterval(c.span))
  return (
    <>
      {segs.map((seg) => (
        <span key={seg.key} className="contents">
          <Bar
            node={seg.node ?? node}
            view={view}
            start={seg.start}
            end={seg.end}
            type={seg.type}
            running={seg.running}
            retry={seg.type === 'model' && !!seg.node?.span.retry}
            lane
            laneIndex={node.lane}
          />
          {seg.node && (
            <Trigger
              node={seg.node}
              view={view}
              now={now}
              scope="step"
              start={seg.start}
              end={seg.end}
              open={details?.id === seg.node.span.id && details.scope === 'step'}
              onOpen={(el) => onOpen(seg.node!, 'step', el)}
            />
          )}
        </span>
      ))}
      {extras.map((c) =>
        c.span.timing === 'point' ? (
          <span key={c.span.id} className="contents">
            <Marker node={c} view={view} />
            <Trigger
              node={c}
              view={view}
              now={now}
              scope="step"
              open={details?.id === c.span.id && details.scope === 'step'}
              onOpen={(el) => onOpen(c, 'step', el)}
            />
          </span>
        ) : (
          <span key={c.span.id} className="contents">
            <Bar node={c} view={view} start={c.span.start} end={endOf(c.span, now)} type="muted" dashed lane />
            <Trigger
              node={c}
              view={view}
              now={now}
              scope="step"
              open={details?.id === c.span.id && details.scope === 'step'}
              onOpen={(el) => onOpen(c, 'step', el)}
            />
          </span>
        ),
      )}
    </>
  )
}

const HATCH: CSSProperties = {
  backgroundImage: 'repeating-linear-gradient(-45deg, var(--color-border-strong) 0 1px, transparent 1px 6px)',
}

function fillClass(type: SegType, laneIndex: number): string {
  const lane = LANES[laneIndex]
  switch (type) {
    case 'model': return lane.strong
    case 'tool': return lane.mid
    case 'think': return lane.faint
    case 'failed': return 'bg-danger'
    case 'muted': return 'bg-ink-faint/40'
    case 'wait': return 'border border-border-strong'
  }
}

function Bar({
  node,
  view,
  start,
  end,
  type,
  running,
  retry,
  dashed,
  lane,
  laneIndex,
}: {
  node: TraceNode
  view: View
  start: number
  end: number
  type: SegType
  running?: boolean
  retry?: boolean
  dashed?: boolean
  lane?: boolean
  laneIndex?: number
}) {
  const a = pct(start, view)
  const b = pct(end, view)
  if (b < 0 || a > 100) return null
  return (
    <span
      data-trace-bar
      data-trace-span-id={node.span.id}
      data-timing={node.span.timing}
      data-bar-type={dashed ? 'unmeasured' : type}
      data-bar-style={dashed ? 'dashed' : 'solid'}
      data-trace-start={start}
      data-trace-end={end}
      data-running={running || undefined}
      className={cn(
        'pointer-events-none absolute top-1/2 flex min-w-1 -translate-y-1/2 items-center justify-center overflow-hidden group-hover/row:brightness-110',
        lane ? 'h-5 rounded-sm' : 'h-4 rounded-xs',
        dashed ? 'border border-dashed border-border-control bg-transparent' : fillClass(type, laneIndex ?? node.lane),
      )}
      style={{
        left: `calc(${a}% + 1px)`,
        width: `max(4px, calc(${b - a}% - 2px))`,
        ...(!dashed && type === 'wait' ? HATCH : null),
      }}
    >
      {!dashed && type === 'failed' && (lane ? <X size={ICON_SIZE.sm} aria-hidden className="shrink-0 text-surface" /> : <X size={ICON_SIZE.xs} aria-hidden className="shrink-0 text-surface" />)}
      {!dashed && retry && type !== 'failed' && (lane ? <RotateCcw size={ICON_SIZE.sm} aria-hidden className="shrink-0 text-surface" /> : <RotateCcw size={ICON_SIZE.xs} aria-hidden className="shrink-0 text-surface" />)}
      {running && (
        <span
          aria-hidden
          className="absolute inset-y-0 start-0 w-1/3 bg-linear-to-r from-transparent via-surface/50 to-transparent motion-safe:animate-sweep"
        />
      )}
    </span>
  )
}

/** point：只知道时刻，画一个点，不画宽条 */
function Marker({ node, view }: { node: TraceNode; view: View }) {
  const p = pct(node.span.start, view)
  if (p < 0 || p > 100) return null
  const failed = node.span.status === 'error'
  return (
    <span
      data-trace-marker
      data-trace-span-id={node.span.id}
      data-timing="point"
      className={cn(
        'pointer-events-none absolute top-1/2 size-2 -translate-x-1/2 -translate-y-1/2 rounded-full',
        failed ? 'bg-danger' : 'bg-ink-3',
      )}
      style={{ left: `${p}%` }}
    />
  )
}

/** 透明的可聚焦按钮，盖在条上；point 是以时刻为中心的 16px 命中区 */
function Trigger({
  node,
  view,
  now,
  scope,
  start,
  end,
  open,
  onOpen,
}: {
  node: TraceNode
  view: View
  now: number
  scope: Scope
  start?: number
  end?: number
  open: boolean
  onOpen: (el: HTMLElement) => void
}) {
  const { t } = useTranslation('ai')
  const { span } = node
  const a = pct(start ?? span.start, view)
  const point = span.timing === 'point'
  const b = pct(end ?? endOf(span, now), view)
  const style: CSSProperties = point
    ? { left: `calc(${a}% - 8px)`, width: 16 }
    : { left: `${a}%`, width: `max(8px, ${b - a}%)` }
  return (
    <button
      type="button"
      data-trace-trigger
      data-trace-span-id={span.id}
      data-scope={scope}
      aria-expanded={open}
      aria-label={t('trace.ui.details', { name: span.name })}
      onClick={(e) => onOpen(e.currentTarget)}
      className={cn(
        'absolute rounded-sm outline-none focus-visible:focus-ring',
        point ? 'inset-y-2.5' : scope === 'step' ? 'inset-y-1.5' : 'inset-y-1',
      )}
      style={style}
    />
  )
}

function Legend() {
  const { t } = useTranslation('ai')
  const swatch = 'h-2.5 w-4 rounded-xs'
  return (
    <ul data-trace-legend className="flex flex-wrap items-center gap-x-3.5 gap-y-1 border-t border-border px-3 py-2 text-xs text-ink-3">
      <li className="flex items-center gap-1.5">
        <span aria-hidden className={cn(swatch, LANES[0].strong)} />
        {t('trace.ui.legend.model')}
      </li>
      <li className="flex items-center gap-1.5">
        <span aria-hidden className={cn(swatch, LANES[0].mid)} />
        {t('trace.ui.legend.tool')}
      </li>
      <li className="flex items-center gap-1.5">
        <span aria-hidden className={cn(swatch, 'border border-border-strong')} style={HATCH} />
        {t('trace.ui.legend.wait')}
      </li>
      <li className="flex items-center gap-1.5">
        <ArrowRight size={ICON_SIZE.xs} aria-hidden />
        {t('trace.ui.legend.handoff')}
      </li>
      <li className="flex items-center gap-1.5">
        <span aria-hidden className={cn('flex items-center justify-center bg-danger', swatch)}>
          <X size={ICON_SIZE.xs} className="text-surface" />
        </span>
        {t('trace.ui.legend.failed')}
      </li>
      <li className="flex items-center gap-1.5">
        <span aria-hidden className="size-2 rounded-full bg-ink-3" />
        {t('trace.ui.legend.point')}
      </li>
      <li className="flex items-center gap-1.5">
        <span aria-hidden className={cn(swatch, 'border border-dashed border-border-control')} />
        {t('trace.ui.legend.unmeasured')}
      </li>
    </ul>
  )
}
