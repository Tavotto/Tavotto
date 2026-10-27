/**
 * 执行追踪 DTO → Waterfall 展示模型（ADR 0151 PR3）。**唯一的转换层**：后端负责事实，这里只做格式转换与翻译，
 * 组件只画。三条不做：不重新判定成功 / 失败（状态原样映射）、不凭空补 Span、不拿相邻时间戳算耗时。
 */
import { t } from '@/i18n'
import type { ExecTraceDto, ExecTraceSpanDto } from '@/lib/api'
import type {
  WaterfallFact,
  WaterfallIcon,
  WaterfallKind,
  WaterfallModel,
  WaterfallSpan,
  WaterfallStatus,
} from '@/components/ai/traceWaterfall/types'

/** 本前端认得的快照版本。更新的后端多出来的字段照样忽略；更旧 / 更新的主版本号交给调用方说「不支持」 */
export const EXEC_TRACE_SCHEMA = 1

const KIND: Record<string, WaterfallKind> = {
  task: 'agent',
  agent: 'agent',
  model: 'model',
  tool: 'tool',
  wait: 'wait',
  phase: 'phase',
  process: 'phase',
  verify: 'phase',
}

const STATUS: Record<string, WaterfallStatus> = {
  ok: 'ok',
  failed: 'error',
  timeout: 'error',
  interrupted: 'error',
  cancelled: 'cancelled',
  unknown: 'unknown',
}

const AGENT_ICON: Record<string, WaterfallIcon> = { codex: 'codex', claude: 'claude', builtin: 'builtin' }

/** 闭集键 → 翻译；后端多出一个这里不认得的值时照原样显示（它本身就是闭集里的 id，不是用户内容） */
function label(group: string, key: string): string {
  const k = `ai:trace.${group}.${key}`
  const out = t(k)
  return out === k || out === `trace.${group}.${key}` ? key : out
}

function iconOf(span: ExecTraceSpanDto, kind: WaterfallKind, isRoot: boolean): WaterfallIcon {
  if (isRoot) return 'task'
  if (kind === 'agent') return AGENT_ICON[String(span.attributes.agent ?? '')] ?? 'agent'
  return kind === 'phase' ? 'phase' : kind
}

function factsOf(dto: ExecTraceDto, span: ExecTraceSpanDto, isRoot: boolean): WaterfallFact[] {
  const facts: WaterfallFact[] = []
  const add = (labelKey: string, value: string) => facts.push({ label: t(`ai:trace.fact.${labelKey}`), value })
  const a = span.attributes
  if (span.error_code) add('errorCode', span.error_code)
  if (span.attempt > 1) add('attempt', String(span.attempt))
  if (span.timing !== 'measured') add('timing', t(`ai:trace.timing.${span.timing}`))
  if (a.opaque === true) add('visibility', t('ai:trace.fact.opaque'))
  if (typeof a.agent === 'string') add('agent', label('agent', a.agent))
  if (typeof a.tool === 'string') add('tool', a.tool)
  if (typeof a.model === 'string') add('model', a.model)
  if (typeof a.execution === 'string') add('execution', label('execution', a.execution))
  if (typeof a.approved_by === 'string') add('approvedBy', label('approvedBy', a.approved_by))
  if (typeof a.exit_ok === 'boolean') add('agentExit', t(a.exit_ok ? 'ai:trace.fact.exitOk' : 'ai:trace.fact.exitFailed'))
  if (isRoot) {
    if (typeof a.outcome === 'string') add('outcome', label('outcome', a.outcome))
    if (typeof a.changed === 'boolean') add('changed', t(a.changed ? 'ai:trace.fact.yes' : 'ai:trace.fact.no'))
    if (typeof a.refresh === 'string') add('refresh', label('refresh', a.refresh))
  }
  const u = span.usage
  if (u?.source === 'estimated') add('usage', t('ai:trace.fact.usageEstimated'))
  if (u?.cost) add('cost', t('ai:trace.fact.costEstimated', { version: u.cost.pricing_version }))
  if (isRoot && dto.first_failure) add('firstFailure', label('span', dto.first_failure.name))
  return facts
}

function spanOf(dto: ExecTraceDto, span: ExecTraceSpanDto): WaterfallSpan {
  const isRoot = span.span_id === dto.root_span_id
  const kind = KIND[span.kind] ?? 'phase'
  const running = span.status === 'running'
  const a = span.attributes
  const detail =
    typeof a.tool === 'string'
      ? a.tool
      : typeof a.agent === 'string'
        ? label('agent', a.agent)
        : typeof a.model === 'string'
          ? a.model
          : undefined
  return {
    id: span.span_id,
    parentId: span.parent_span_id ?? undefined,
    kind,
    name: isRoot ? label('operation', dto.operation_kind) : label('span', span.name),
    detail,
    start: span.start_offset_ms / 1000,
    end: running || span.end_offset_ms == null ? undefined : span.end_offset_ms / 1000,
    status: running ? undefined : STATUS[span.status] ?? 'unknown',
    timing: span.timing,
    attempt: span.attempt,
    retry: span.attempt > 1,
    icon: iconOf(span, kind, isRoot),
    cost: span.usage?.cost?.amount ?? null,
    tokensIn: span.usage?.input_tokens ?? null,
    tokensOut: span.usage?.output_tokens ?? null,
    firstFailure: dto.first_failure?.span_id === span.span_id && !isRoot,
    opaque: a.opaque === true,
    facts: factsOf(dto, span, isRoot),
  }
}

/**
 * 旧阶段轨迹映射来的 `stage` 观察点：零宽点，挂在它所属的 Span 下。只知道「这一刻发生了」，所以 `timing='point'`。
 */
function stagePoints(dto: ExecTraceDto): WaterfallSpan[] {
  return dto.events
    .filter((e) => e.name === 'stage' && typeof e.attributes?.phase === 'string')
    .map((e) => {
      const at = e.at_offset_ms / 1000
      const outcome = String(e.attributes?.outcome ?? '')
      return {
        id: `stage-${e.seq}`,
        parentId: e.span_id,
        kind: 'phase' as const,
        name: label('stage', String(e.attributes!.phase)),
        start: at,
        end: at,
        status: STATUS[outcome] ?? 'unknown',
        timing: 'point' as const,
        attempt: 1,
        retry: false,
        icon: 'stage' as const,
        cost: null,
        tokensIn: null,
        tokensOut: null,
        firstFailure: false,
        facts: [],
      }
    })
}

/** `nowWall`：此刻的墙钟秒（运行中的 Trace 画到这里）；缺省 = `Date.now()` */
export function toWaterfall(dto: ExecTraceDto, nowWall: number = Date.now() / 1000): WaterfallModel {
  const running = dto.status === 'running'
  const root = dto.spans.find((s) => s.span_id === dto.root_span_id)
  const spans = [...dto.spans.map((s) => spanOf(dto, s)), ...stagePoints(dto)]
  const last = Math.max(0, ...dto.spans.map((s) => (s.end_offset_ms ?? s.start_offset_ms) / 1000))
  const duration = running
    ? Math.max(last, nowWall - dto.started_at)
    : root?.duration_ms != null
      ? root.duration_ms / 1000
      : last
  return { spans, duration, running }
}

/** 这份快照前端能不能画：主版本号对不上就交给调用方显示「不支持」，不硬画 */
export function supported(dto: ExecTraceDto): boolean {
  return dto.schema_version === EXEC_TRACE_SCHEMA
}
