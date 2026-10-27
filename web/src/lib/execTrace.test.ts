import { describe, expect, it } from 'vitest'
import type { ExecTraceDto, ExecTraceSpanDto } from '@/lib/api'
import { supported, toWaterfall } from './execTrace'

function span(over: Partial<ExecTraceSpanDto> & { span_id: string }): ExecTraceSpanDto {
  return {
    parent_span_id: 'root',
    name: 'tool_call',
    kind: 'tool',
    start_offset_ms: 0,
    end_offset_ms: 100,
    duration_ms: 100,
    status: 'ok',
    error_code: null,
    attempt: 1,
    timing: 'measured',
    attributes: {},
    usage: null,
    ...over,
  }
}

function dto(spans: ExecTraceSpanDto[], over: Partial<ExecTraceDto> = {}): ExecTraceDto {
  return {
    schema_version: 1,
    trace_id: 'a'.repeat(32),
    operation_kind: 'ai_task',
    started_at: 1000,
    ended_at: 1005,
    status: 'ok',
    error_code: null,
    root_span_id: 'root',
    first_failure: null,
    truncated: false,
    dropped_count: 0,
    complete: true,
    spans: [span({ span_id: 'root', parent_span_id: null, name: 'task', kind: 'task', end_offset_ms: 5000, duration_ms: 5000 }), ...spans],
    events: [],
    last_seq: 0,
    ...over,
  }
}

const byId = (m: ReturnType<typeof toWaterfall>, id: string) => m.spans.find((s) => s.id === id)!

describe('执行追踪 DTO → Waterfall（只翻译不判断）', () => {
  it('父子、kind、秒、状态原样映射；根是任务泳道', () => {
    const m = toWaterfall(
      dto([
        span({ span_id: 'run', name: 'agent_run', kind: 'agent', attributes: { agent: 'codex', opaque: true } }),
        span({ span_id: 'm', parent_span_id: 'run', name: 'model_request', kind: 'model', start_offset_ms: 200, end_offset_ms: 1700 }),
        span({ span_id: 'w', name: 'user_wait', kind: 'wait', status: 'timeout', error_code: 'approval_timeout' }),
        span({ span_id: 'c', status: 'cancelled' }),
        span({ span_id: 'u', status: 'unknown', timing: 'unmeasured', error_code: 'unclosed' }),
      ]),
    )
    expect(byId(m, 'root')).toMatchObject({ kind: 'agent', icon: 'task', parentId: undefined })
    expect(byId(m, 'run')).toMatchObject({ kind: 'agent', icon: 'codex' })
    expect(byId(m, 'm')).toMatchObject({ parentId: 'run', kind: 'model', start: 0.2, end: 1.7, status: 'ok' })
    expect(byId(m, 'w').status).toBe('error') // 超时是失败的一种，不是成功
    expect(byId(m, 'c').status).toBe('cancelled')
    expect(byId(m, 'u')).toMatchObject({ status: 'unknown', timing: 'unmeasured' })
    expect(m.duration).toBe(5)
    expect(m.running).toBe(false)
  })

  it('运行中的 Span 没有终点；运行中的 Trace 画到此刻', () => {
    const m = toWaterfall(
      dto([span({ span_id: 'r', status: 'running', end_offset_ms: null, duration_ms: null })], {
        status: 'running',
        ended_at: null,
      }),
      1012.5,
    )
    expect(byId(m, 'r').end).toBeUndefined()
    expect(byId(m, 'r').status).toBeUndefined()
    expect(m.running).toBe(true)
    expect(m.duration).toBe(12.5)
  })

  it('不知道的就是不知道：没有用量 = null，不是 0；估算用量与估算费用都说明', () => {
    const m = toWaterfall(
      dto([
        span({ span_id: 'none', kind: 'model', name: 'model_request' }),
        span({
          span_id: 'est',
          kind: 'model',
          name: 'model_request',
          usage: { source: 'estimated', input_tokens: 10, output_tokens: 2, cached_input_tokens: null, cost: null },
        }),
        span({
          span_id: 'priced',
          kind: 'model',
          name: 'model_request',
          usage: {
            source: 'reported',
            input_tokens: 1000,
            output_tokens: 10,
            cached_input_tokens: 0,
            cost: { amount: 0.0021, currency: 'USD', pricing_version: '2026-10-27', estimated: true },
          },
        }),
      ]),
    )
    expect(byId(m, 'none')).toMatchObject({ cost: null, tokensIn: null, tokensOut: null })
    expect(byId(m, 'est')).toMatchObject({ cost: null, tokensIn: 10 })
    expect(byId(m, 'est').facts.length).toBeGreaterThan(0)
    expect(byId(m, 'priced').cost).toBeCloseTo(0.0021)
    expect(byId(m, 'priced').facts.some((f) => f.value.includes('2026-10-27'))).toBe(true)
  })

  it('旧阶段轨迹的观察点是零宽的点，挂在所属 Span 下，不造区间', () => {
    const m = toWaterfall(
      dto([span({ span_id: 'run', name: 'script_run', kind: 'process', start_offset_ms: 0, end_offset_ms: 3000 })], {
        events: [
          { seq: 1, name: 'stage', span_id: 'run', at_offset_ms: 2500, attributes: { phase: 'plan', outcome: 'ok' } },
          { seq: 2, name: 'stage', span_id: 'run', at_offset_ms: 2600, attributes: { phase: 'execute', outcome: 'failed' } },
          { seq: 3, name: 'cancel_requested', span_id: 'root', at_offset_ms: 2700 },
        ],
      }),
    )
    const points = m.spans.filter((s) => s.id.startsWith('stage-'))
    expect(points.map((p) => [p.parentId, p.timing, p.start, p.end, p.status])).toEqual([
      ['run', 'point', 2.5, 2.5, 'ok'],
      ['run', 'point', 2.6, 2.6, 'error'],
    ])
  })

  it('第一次失败只标那一个 Span；不认得的闭集值照原样显示，不丢', () => {
    const m = toWaterfall(
      dto([span({ span_id: 'v', name: 'validate', kind: 'verify', status: 'failed' }), span({ span_id: 'n', name: 'brand_new_phase' })], {
        first_failure: { span_id: 'v', name: 'validate', error_code: 'precommit_not_verified', attempt: 1 },
      }),
    )
    expect(byId(m, 'v').firstFailure).toBe(true)
    expect(m.spans.filter((s) => s.firstFailure)).toHaveLength(1)
    expect(byId(m, 'n').name).toBe('brand_new_phase')
  })

  it('主版本号对不上的快照不硬画', () => {
    expect(supported(dto([]))).toBe(true)
    expect(supported(dto([], { schema_version: 2 }))).toBe(false)
  })
})
