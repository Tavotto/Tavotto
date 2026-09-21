/**
 * 画布启动：host 递来的 tool-result 三种来货各走哪条路（issue #457）。
 *
 * 钉的是：完整结果零往返；只有把手就经 `tavotto_session_state` 取件（**绝不自己
 * 发起 open**）；空壳当场判成失败并把形状写进诊断——之前它是无限等待。
 */
import { describe, expect, it, vi } from 'vitest'
import type { Manifest } from '@/lib/api'
import type { AppsBridge, ToolCallResult } from './appsBridge'
import { classifyToolResult, resolveToolResult, WAITING_NOTICE_MS } from './boot'
import type { OpenFigureResult } from './session'

const manifest = (): Manifest =>
  ({
    stem: 'FigM',
    size_mm: [80, 60],
    elements: [
      {
        gid: 'axes_0.xticks',
        role: 'ticks',
        label: 'x 刻度',
        bbox: [0.1, 0.9, 0.8, 0.05],
        draggable: false,
        editable: [{ prop: 'fontsize', type: 'number', value: 9 }],
      },
    ],
  }) as unknown as Manifest

const full = (): OpenFigureResult => ({
  ok: true,
  session_id: 's-abc',
  project: '/tmp/figures',
  stem: 'FigM',
  script: 'figm.py',
  manifest: manifest(),
  svg: '<svg width="1pt" height="1pt"><g/></svg>',
  patch_hash: 'sha256:0',
  render_revision: 1,
  profile: { profile_id: 'lab-publication-v1', profile_version: '1.0.0' },
})

/** server 按宿主体积上限省略过 manifest / svg 的 open 结果：把手齐全，负载不在。 */
const handle = () => {
  const { manifest: _m, svg: _s, ...rest } = full()
  return {
    ...rest,
    elided: {
      fields: ['svg', 'manifest'],
      reason: 'inline_budget',
      inline_bytes: 1_400_000,
      budget_bytes: 786_432,
      fetch_with: 'tavotto_session_state',
    },
  }
}

/** Codex 把超过 1 MiB 的结果清空之后递给 iframe 的那份：只剩一段截断的文本预览。 */
const stripped = () => ({
  content: [{ type: 'text', text: '{"content":[{"type":"text","text":"已打开 FigM' + 'x'.repeat(4000) }],
  structuredContent: null,
  _meta: null,
})

function bridgeWith(handler: (name: string, args: Record<string, unknown>) => ToolCallResult) {
  const calls: { name: string; args: Record<string, unknown> }[] = []
  const callTool = vi.fn(async (name: string, args: Record<string, unknown>) => {
    calls.push({ name, args })
    return handler(name, args)
  })
  return { bridge: { callTool } as unknown as Pick<AppsBridge, 'callTool'>, calls }
}

const never = () => {
  throw new Error('这条路不该发任何 tools/call')
}

describe('classifyToolResult', () => {
  it('完整的 open 结果 → open（params 就是 CallToolResult，2026-01-26）', () => {
    expect(classifyToolResult({ content: [], structuredContent: full() })).toMatchObject({
      kind: 'open',
    })
  })

  it('老的 { result: CallToolResult } 包装也认', () => {
    expect(
      classifyToolResult({ result: { content: [], structuredContent: full() } }),
    ).toMatchObject({ kind: 'open' })
  })

  it('省略过 manifest 的结果 → 把手', () => {
    expect(classifyToolResult({ content: [], structuredContent: handle() })).toEqual({
      kind: 'handle',
      sessionId: 's-abc',
    })
  })

  it('只省了 svg（manifest 还在、六项齐全）→ 仍是把手：种下去是空画布（Codex 评审 P1）', () => {
    const { svg: _s, ...rest } = full()
    const onlySvgElided = {
      ...rest,
      elided: {
        fields: ['svg'],
        reason: 'inline_budget',
        inline_bytes: 900_000,
        budget_bytes: 786_432,
        fetch_with: 'tavotto_session_state',
      },
    }
    expect(classifyToolResult({ content: [], structuredContent: onlySvgElided })).toEqual({
      kind: 'handle',
      sessionId: 's-abc',
    })
    // 老 server 不写 elided：矢量图缺 svg 字段同样不完整
    expect(classifyToolResult({ content: [], structuredContent: rest })).toEqual({
      kind: 'handle',
      sessionId: 's-abc',
    })
  })

  it('raster 档：svg 为 null 而 preview.mode 是 raster → 完整（位图在同一次响应里）', () => {
    const raster = {
      ...full(),
      svg: null,
      preview: { mode: 'raster', reason: 'complexity_budget', svg_bytes: 0, rasterized_artist_count: 1 },
      preview_png_base64: 'AAAA',
    }
    expect(classifyToolResult({ content: [], structuredContent: raster })).toMatchObject({
      kind: 'open',
    })
    // 矢量图（没有 preview 元数据 = 老引擎的 vector）svg 为 null 不算完整
    expect(classifyToolResult({ content: [], structuredContent: { ...full(), svg: null } })).toEqual({
      kind: 'handle',
      sessionId: 's-abc',
    })
  })

  it('apply 形状的结果（有 manifest 没 profile）也是把手，不是 open', () => {
    const { profile: _p, project: _q, script: _r, ...apply } = full()
    expect(classifyToolResult({ content: [], structuredContent: apply })).toEqual({
      kind: 'handle',
      sessionId: 's-abc',
    })
  })

  it('宿主清空后的空壳 → unusable，诊断里写明形状与预览开头', () => {
    const cls = classifyToolResult(stripped())
    expect(cls.kind).toBe('unusable')
    const detail = (cls as { detail: string }).detail
    expect(detail).toContain('structuredContent: null')
    expect(detail).toContain('_meta: null')
    expect(detail).toContain('content[0].text (')
    expect(detail).toContain('已打开 FigM')
    // 只取开头，不把整段预览搬上屏
    expect(detail.length).toBeLessThan(400)
  })

  it('structuredContent 缺失 / 没有 session_id / params 不是对象——都是 unusable', () => {
    expect(classifyToolResult({ content: [] })).toMatchObject({
      kind: 'unusable',
      detail: expect.stringContaining('structuredContent: missing'),
    })
    expect(classifyToolResult({ structuredContent: { ok: true, stem: 'FigM' } })).toMatchObject({
      kind: 'unusable',
      detail: expect.stringContaining('structuredContent keys: [ok, stem]'),
    })
    expect(classifyToolResult(null)).toMatchObject({ kind: 'unusable' })
    expect(classifyToolResult('x')).toMatchObject({ kind: 'unusable' })
  })
})

describe('resolveToolResult', () => {
  it('完整结果：零往返，直接可种', async () => {
    const { bridge, calls } = bridgeWith(never)
    const res = await resolveToolResult(bridge, { structuredContent: full() })
    expect(res).toMatchObject({ ok: true, fetched: false })
    expect(calls).toEqual([])
  })

  it('把手：经 tavotto_session_state 取件，回来的带 patches；绝不自己发起 open', async () => {
    const state = { ...full(), patches: [{ gid: 'axes_0.xticks', prop: 'fontsize', value: 7 }] }
    const { bridge, calls } = bridgeWith((name) => {
      expect(name).toBe('tavotto_session_state')
      return { content: [], structuredContent: state as unknown as Record<string, unknown> }
    })
    const onFetching = vi.fn()
    const res = await resolveToolResult(bridge, { structuredContent: handle() }, onFetching)
    expect(onFetching).toHaveBeenCalledTimes(1)
    expect(res).toMatchObject({ ok: true, fetched: true })
    expect((res as { payload: OpenFigureResult }).payload.patches).toEqual(state.patches)
    expect(calls).toEqual([{ name: 'tavotto_session_state', args: { session_id: 's-abc' } }])
  })

  it('空壳：当场失败为 stripped，不发任何 tools/call（等是等不来的）', async () => {
    const { bridge, calls } = bridgeWith(never)
    const onFetching = vi.fn()
    const res = await resolveToolResult(bridge, stripped(), onFetching)
    expect(res).toMatchObject({ ok: false, code: 'stripped' })
    expect(onFetching).not.toHaveBeenCalled()
    expect(calls).toEqual([])
  })

  it('取件失败（工具报错）→ fetch_failed，诊断带 code 与会话 id', async () => {
    const { bridge } = bridgeWith(() => ({
      isError: true,
      content: [{ type: 'text', text: '没有这个会话' }],
      structuredContent: { ok: false, code: 'unknown_session', error: '没有这个会话: s-abc' },
    }))
    const res = await resolveToolResult(bridge, { structuredContent: handle() })
    expect(res).toMatchObject({ ok: false, code: 'fetch_failed' })
    const detail = (res as { detail: string }).detail
    expect(detail).toContain('tavotto_session_state(s-abc)')
    expect(detail).toContain('unknown_session')
  })

  it('取件回来的不完整（老 server 没有这个工具 / 形状不对）→ fetch_failed，不硬种', async () => {
    const { bridge } = bridgeWith(() => ({
      content: [],
      structuredContent: { ok: true, session_id: 's-abc', stem: 'FigM' },
    }))
    const res = await resolveToolResult(bridge, { structuredContent: handle() })
    expect(res).toMatchObject({
      ok: false,
      code: 'fetch_failed',
      detail: expect.stringContaining('bad_session_state'),
    })
  })
})

describe('WAITING_NOTICE_MS', () => {
  it('是提示不是放弃：数量级是几十秒（Codex 在工具返回时才建 iframe，结果本该随握手立刻到）', () => {
    expect(WAITING_NOTICE_MS).toBeGreaterThanOrEqual(10_000)
    expect(WAITING_NOTICE_MS).toBeLessThanOrEqual(60_000)
  })
})
