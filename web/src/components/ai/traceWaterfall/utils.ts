import { t } from '@/i18n'
import { formatNumber } from '@/i18n/format'
import { formatDuration as formatMs } from '../transcriptModel'
import type { WaterfallModel, WaterfallSpan } from './types'

/** 行高（px）：Handoffs 的连线按它算 y，所以每一行必须正好这么高 */
export const ROW = 36
/** 智能体泳道条高度的一半 */
export const LANE_HALF = 10

export interface TraceNode {
  span: WaterfallSpan
  parent?: TraceNode
  children: TraceNode[]
  depth: number
  /** 智能体色阶下标（子孙叶子沿用所在泳道） */
  lane: number
  /** 汇总：自己有就用自己的，智能体没有则累加子孙已知的；全不知道 = null */
  cost: number | null
  tokens: number | null
}

const known = (...xs: (number | null)[]) => {
  const ks = xs.filter((x): x is number => x !== null)
  return ks.length ? ks.reduce((a, b) => a + b, 0) : null
}

/** spans → 森林。父不存在的当根；兄弟按开始时刻排。只在 model 变时算一次，与 `now` 无关 */
export function buildTree(spans: WaterfallSpan[]): TraceNode[] {
  const nodes = new Map<string, TraceNode>()
  for (const span of spans) {
    nodes.set(span.id, { span, children: [], depth: 0, lane: 0, cost: null, tokens: null })
  }
  const roots: TraceNode[] = []
  for (const node of nodes.values()) {
    const parent = node.span.parentId ? nodes.get(node.span.parentId) : undefined
    if (parent && parent !== node) {
      node.parent = parent
      parent.children.push(node)
    } else {
      roots.push(node)
    }
  }
  const resolve = (node: TraceNode, depth: number, agentDepth: number, seen: Set<string>) => {
    seen.add(node.span.id)
    node.depth = depth
    const isAgent = node.span.kind === 'agent'
    node.lane = Math.max(0, agentDepth - 1) % LANES.length
    node.children.sort((a, b) => a.span.start - b.span.start)
    const own = node.span.cost
    const ownTokens = known(node.span.tokensIn, node.span.tokensOut)
    const kids = node.children.filter((c) => !seen.has(c.span.id))
    for (const child of kids) resolve(child, depth + 1, agentDepth + (child.span.kind === 'agent' ? 1 : 0), seen)
    node.cost = own ?? (isAgent ? known(...kids.map((c) => c.cost)) : null)
    node.tokens = ownTokens ?? (isAgent ? known(...kids.map((c) => c.tokens)) : null)
  }
  roots.sort((a, b) => a.span.start - b.span.start)
  const seen = new Set<string>()
  for (const root of roots) resolve(root, 0, root.span.kind === 'agent' ? 1 : 0, seen)
  return roots
}

export function walk(nodes: TraceNode[], visit: (node: TraceNode) => void) {
  for (const node of nodes) {
    visit(node)
    walk(node.children, visit)
  }
}

/** 智能体色阶：accent 家族三档（强 = 模型调用，中 = 工具，淡 = 思考间隙）。完整类名写在这里，Tailwind 才扫得到 */
export const LANES = [
  { strong: 'bg-accent', mid: 'bg-accent/50', faint: 'bg-accent/25', stroke: 'stroke-accent', fill: 'fill-accent' },
  { strong: 'bg-sel', mid: 'bg-sel/50', faint: 'bg-sel/25', stroke: 'stroke-sel', fill: 'fill-sel' },
  { strong: 'bg-dim', mid: 'bg-dim/50', faint: 'bg-dim/25', stroke: 'stroke-dim', fill: 'fill-dim' },
] as const

// ---- 时间 -------------------------------------------------------------------

/** 有区间可画（measured / reported）。point 与 unmeasured 不画实心条 */
export const hasInterval = (s: WaterfallSpan) => s.timing === 'measured' || s.timing === 'reported'

/** 还在跑：没有结束时刻、区间是量到的，且整条 Trace 还在跑 */
export const isRunning = (s: WaterfallSpan, model: WaterfallModel, now = 0) =>
  hasInterval(s) &&
  (model.replay ? s.start <= now && (s.end === undefined || s.end > now) : s.end === undefined && model.running)

/** 结束时刻；没有结束时刻的画到 `now`。回放中：结束得比 `now` 晚的也只画到 `now`（那一刻它还没结束） */
export const endOf = (s: WaterfallSpan, now: number, replay = false) => {
  if (s.end === undefined) return Math.max(now, s.start)
  return replay ? Math.max(s.start, Math.min(s.end, now)) : s.end
}

/**
 * 回放中这个 Span 在 `now` 那一刻是不是已经有定论（状态 / 费用 / 失败标记才可以露出来）：
 * 点 / 虚线框这类没有区间的，出现即定论；有区间的要等到它结束。非回放一律是。
 */
export const settledAt = (s: WaterfallSpan, now: number, replay: boolean) =>
  !replay || !hasInterval(s) || (s.end !== undefined && s.end <= now)

/** 回放中：这个 Span 在 `now` 那一刻还没开始（不画） */
export const notStartedAt = (s: WaterfallSpan, now: number, replay: boolean) => replay && s.start > now

const extentCache = new WeakMap<TraceNode, { from: number; to: number }>()
/** 这一行（含子孙）涉及的时间范围；有区间却没结束的，终点 = ∞。回放时行 memo 靠它判断「这一格对这行有没有影响」 */
export function subtreeExtent(node: TraceNode): { from: number; to: number } {
  let v = extentCache.get(node)
  if (!v) {
    const { span } = node
    let to = span.end ?? (hasInterval(span) ? Infinity : span.start)
    let from = span.start
    for (const c of node.children) {
      const e = subtreeExtent(c)
      from = Math.min(from, e.from)
      to = Math.max(to, e.to)
    }
    v = { from, to }
    extentCache.set(node, v)
  }
  return v
}

/** 回放时，`a` → `b` 两个时刻之间这一行的画面会不会变：都在它开始之前，或都在它彻底结束之后，就不变 */
export function rowUnchangedBetween(node: TraceNode, a: number, b: number): boolean {
  const { from, to } = subtreeExtent(node)
  return (a >= to && b >= to) || (a < from && b < from)
}

/**
 * `wait` 只给真的等待（`kind==='wait'` 的 Span，例如等用户确认）——斜纹的意思就是「在等」。
 * `delegate`：这段时间由子智能体接手（它自己的泳道在下一行画），父泳道只是安静地陪着，不是在等。
 */
export type SegType = 'model' | 'tool' | 'wait' | 'think' | 'delegate' | 'failed' | 'muted'

export interface LaneSegment {
  key: string
  start: number
  end: number
  type: SegType
  node?: TraceNode
  running: boolean
}

/** 叶子条该画成什么 */
export function leafType(span: WaterfallSpan, open = false): SegType {
  // 回放中还没结束的调用：结局（失败 / 取消）此刻还不知道，按种类画
  if (!open && span.status === 'error') return 'failed'
  if (!open && (span.status === 'cancelled' || span.status === 'unknown')) return 'muted'
  if (span.kind === 'model') return 'model'
  if (span.kind === 'wait') return 'wait'
  return 'tool'
}

/**
 * 把智能体的区间切成「此刻它在做什么」：自己的一次调用 / 子智能体接手 / 调用之间的思考。
 * 只用有区间的子项；point 与 unmeasured 的子项另画（标记 / 虚线框）。
 */
export function laneSegments(agent: TraceNode, now: number, model: WaterfallModel): LaneSegment[] {
  if (!hasInterval(agent.span)) return []
  const a0 = agent.span.start
  const replay = !!model.replay
  const a1 = endOf(agent.span, now, replay)
  const kids = agent.children.filter((c) => hasInterval(c.span) && !notStartedAt(c.span, now, replay))
  const leaves = kids.filter((c) => c.span.kind !== 'agent')
  const subs = kids.filter((c) => c.span.kind === 'agent')
  const cuts = new Set([a0, a1])
  for (const c of kids) {
    cuts.add(Math.min(a1, Math.max(a0, c.span.start)))
    cuts.add(Math.min(a1, Math.max(a0, endOf(c.span, now, replay))))
  }
  const points = [...cuts].sort((x, y) => x - y)
  const out: LaneSegment[] = []
  for (let i = 0; i < points.length - 1; i++) {
    const [x, y] = [points[i], points[i + 1]]
    if (y - x < 1e-6) continue
    const mid = (x + y) / 2
    const within = (c: TraceNode) => c.span.start <= mid && endOf(c.span, now, replay) >= mid
    const leaf = leaves.find(within)
    const busy = subs.filter(within)
    let type: SegType
    let key: string
    if (leaf) {
      type = leafType(leaf.span, replay && !settledAt(leaf.span, now, replay))
      key = leaf.span.id
    } else if (busy.length) {
      type = 'delegate'
      key = `delegate:${busy.map((c) => c.span.id).join('+')}`
    } else {
      type = 'think'
      key = 'think'
    }
    const prev = out.at(-1)
    if (prev && prev.key.split('@')[0] === key && Math.abs(prev.end - x) < 1e-6) {
      prev.end = y
    } else {
      out.push({ key: `${key}@${x}`, start: x, end: y, type, node: leaf, running: false })
    }
  }
  const last = out.at(-1)
  if (last && isRunning(agent.span, model, now)) last.running = true
  return out
}

// ---- 指标 -------------------------------------------------------------------

export interface TraceMetrics {
  extent: number
  cost: number | null
  tokens: number | null
  /** null = 有不可观察的 Agent，数不出来（不是 0） */
  llmCalls: number | null
  /** 经我们自己的工具入口观察到的工具调用数；有不可观察的 Agent 时只是下界（`toolCallsLowerBound`），一个都没观察到 = null */
  toolCalls: number | null
  /** true = `toolCalls` 是「至少」：不可观察的 Agent 自己用的工具我们看不见 */
  toolCallsLowerBound: boolean
  errors: number
  slowestId?: string
}

export function computeMetrics(roots: TraceNode[], model: WaterfallModel, now: number): TraceMetrics {
  const m: TraceMetrics = {
    extent: model.duration,
    cost: known(...roots.map((r) => r.cost)),
    tokens: known(...roots.map((r) => r.tokens)),
    llmCalls: 0,
    toolCalls: 0,
    toolCallsLowerBound: false,
    errors: 0,
  }
  let slowest = 0
  let opaque = false
  let rootErrors = 0
  walk(roots, (node) => {
    const { span } = node
    m.extent = Math.max(m.extent, span.end ?? span.start)
    if (span.end === undefined && hasInterval(span) && model.running) m.extent = Math.max(m.extent, now)
    if (span.opaque) opaque = true
    if (span.kind === 'model') m.llmCalls = (m.llmCalls ?? 0) + 1
    if (span.kind === 'tool') m.toolCalls = (m.toolCalls ?? 0) + 1
    // 根的失败是子的失败连带出来的：只数根以下的；根以下一个都没失败（例如进程没起来）才数根
    if (span.status === 'error') {
      if (span.parentId === undefined) rootErrors++
      else m.errors++
    }
    if (span.kind !== 'agent' && hasInterval(span) && span.end !== undefined && span.end - span.start > slowest) {
      slowest = span.end - span.start
      m.slowestId = span.id
    }
  })
  if (m.errors === 0) m.errors = rootErrors
  if (opaque) {
    // 模型调用在 Agent 内部，数不出来；工具调用经我们自己的入口走的那些是看得见的——只能当下界，一个都没看见就是不知道
    m.llmCalls = null
    if ((m.toolCalls ?? 0) > 0) m.toolCallsLowerBound = true
    else m.toolCalls = null
  }
  return m
}

/** 命中项及其祖先；null = 不过滤 */
export function matchingIds(roots: TraceNode[], query: string): Set<string> | null {
  const q = query.trim().toLowerCase()
  if (!q) return null
  const keep = new Set<string>()
  walk(roots, (node) => {
    const text = `${node.span.name} ${node.span.detail ?? ''}`.toLowerCase()
    if (!text.includes(q)) return
    for (let n: TraceNode | undefined = node; n; n = n.parent) keep.add(n.span.id)
  })
  return keep
}

// ---- 坐标轴 -----------------------------------------------------------------

const TICK_STEPS = [0.5, 1, 2, 5, 10, 15, 20, 30, 60, 120, 300, 600, 1800, 3600]

/** 运行中给右侧留出生长空间；跑完只留一点边 */
/** 运行中的时间轴只取这张阶梯上的值：此刻每走一格不改坐标系，已结束的行就不必重画（大 Trace 的实时刷新预算） */
const LIVE_DOMAINS = [8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256, 384, 512, 768, 1024, 1536, 2048, 3072, 4096]

/** 已结束的时间轴在最后一条之后至少留这么多像素：最后一条后面的时长小字（「2m 34s」）要放得下 */
export const END_LABEL_PX = 64

export function traceDomain(extent: number, running: boolean, trackWidth = 0) {
  if (!running) {
    // 轨道宽度量到了就按像素留边（窄轨道留得多一些），没量到先按比例
    const share = trackWidth > 0 ? Math.min(0.35, END_LABEL_PX / trackWidth) : 0.06
    return Math.max(extent / (1 - share), extent * 1.06, 1)
  }
  const want = extent * 1.3
  return LIVE_DOMAINS.find((d) => d >= want) ?? Math.ceil(want / 1024) * 1024
}

/** 这一行（含子孙）有没有还没结束的 Span：只有它们的画面随「此刻」变 */
const openCache = new WeakMap<TraceNode, boolean>()
export function subtreeOpen(node: TraceNode): boolean {
  let v = openCache.get(node)
  if (v === undefined) {
    v = node.span.end === undefined || node.children.some(subtreeOpen)
    openCache.set(node, v)
  }
  return v
}

export function ticksFor(start: number, domain: number) {
  const step = TICK_STEPS.find((s) => Math.floor(domain / s) <= 4) ?? TICK_STEPS.at(-1)!
  const ticks: number[] = []
  for (let x = Math.ceil(start / step) * step; x <= start + domain + 1e-6; x += step) ticks.push(Number(x.toFixed(3)))
  return ticks
}

/** 时刻 → 视口内的百分比 */
export const pct = (time: number, view: { start: number; domain: number }) => ((time - view.start) / view.domain) * 100

// ---- 格式 -------------------------------------------------------------------

export const formatSeconds = (seconds: number) => formatMs(seconds * 1000)

export function formatTick(seconds: number) {
  return seconds >= 120
    ? t('trace.ui.tick.minutes', { ns: 'ai', value: formatNumber(Math.round(seconds / 60)) })
    : t('trace.ui.tick.seconds', { ns: 'ai', value: formatNumber(Number(seconds.toFixed(1))) })
}

export function formatCost(cost: number) {
  const digits = cost > 0 && cost < 0.01 ? 3 : 2
  return `$${formatNumber(cost, { minimumFractionDigits: digits, maximumFractionDigits: digits })}`
}

export function formatTokens(n: number) {
  return formatNumber(n, { notation: 'compact', maximumFractionDigits: 1 })
}

/** 行尾小字：时长 · 费用；point 只写时刻；unmeasured 写 — */
export function spanFigure(node: TraceNode, now: number, replay = false): string {
  const { span } = node
  const dash = t('trace.ui.unknown', { ns: 'ai' })
  if (span.timing === 'point') return t('trace.ui.startOffset', { ns: 'ai', value: formatSeconds(span.start) })
  if (span.timing === 'unmeasured') return dash
  const text = formatSeconds(Math.max(0, endOf(span, now, replay) - span.start))
  // 回放中还没结束的：费用此刻不知道，只写已经过去的时长
  if (!settledAt(span, now, replay)) return text
  return node.cost !== null && node.cost > 0 ? `${text} · ${formatCost(node.cost)}` : text
}
