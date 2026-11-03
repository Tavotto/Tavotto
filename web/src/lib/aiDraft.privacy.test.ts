/**
 * 草稿全文与请求 id 不进日志、诊断追踪环、诊断包的 frontend-state（ADR 0132）。
 * 跑一遍完整的「发出 → 未确认 → 查询 → 没收到 → 同 id 重发 → 被接受」，期间 console 的每一次调用、
 * 诊断追踪环与 frontend-state 快照里都搜不到提示词和 rid。后端那一半在
 * `tests/test_ai_idempotency.py::test_prompt_and_request_id_never_reach_the_log`。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { buildFrontendDiagnosticSnapshot } from '@/diagnostics/snapshot'
import { __resetDiagnosticsForTests, readDiagnosticTrace } from '@/diagnostics/store'
import { currentProjectId, setCurrentProjectId } from '@/lib/session'
import '@/lib/aiDraftLedger'
import { useAiStore } from '@/store/aiStore'

const SECRET = '未发表结论-SECRET-PROMPT'
let mode: 'down' | 'notfound' | 'ok' = 'down'
let rid = ''
const json = (b: unknown, status = 200) =>
  new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } })

globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
  const u = String(url)
  if (u.includes('/api/ai/run')) {
    rid = JSON.parse(String(init?.body)).client_request_id
    if (mode === 'down') throw new TypeError('Failed to fetch')
    return json({ session: 's-1', script: 'fig1.py' })
  }
  if (u.includes('/api/ai/requests/')) return json({ error: 'x', code: 'ai_request_not_found' }, 404)
  return json({})
}) as typeof fetch

const spies = (['log', 'info', 'warn', 'error', 'debug'] as const).map((m) => vi.spyOn(console, m).mockImplementation(() => {}))

beforeEach(() => {
  localStorage.clear()
  setCurrentProjectId('pa')
  __resetDiagnosticsForTests()
  useAiStore.setState({
    sessions: [],
    submissions: {},
    agent: 'codex',
    caps: { agents: [{ id: 'codex', name: 'Codex', usable: true, models: [], efforts: [] }] } as never,
  })
  spies.forEach((s) => s.mockClear())
})
afterEach(() => vi.clearAllMocks())

describe('提示词与请求 id 的去向', () => {
  it('整条提交流程里，console / 追踪环 / frontend-state 都搜不到', async () => {
    const st = useAiStore.getState()
    mode = 'down'
    await st.start({
      project: currentProjectId(), generation: st.generation,
      prompt: SECRET, fileId: 'Fig1.pdf', panelId: 'p1', gid: null, label: null,
      scope: 'figure', target: '整张图', overrides: [],
    })
    // 对照：账本里确实有它们（尺子量得到——否则下面的「搜不到」是空话）
    const sub = Object.values(useAiStore.getState().submissions)[0]
    expect(sub.prompt).toBe(SECRET)
    expect(sub.rid).toBe(rid)

    const snapshots = [JSON.stringify(buildFrontendDiagnosticSnapshot())]
    await st.queryRequest(sub.rid)
    mode = 'ok'
    await st.resend(sub.rid)
    snapshots.push(JSON.stringify(buildFrontendDiagnosticSnapshot()))

    const logged = JSON.stringify(spies.flatMap((s) => s.mock.calls))
    const trace = JSON.stringify(readDiagnosticTrace())
    for (const [where, text] of [['console', logged], ['trace', trace], ['frontend-state', snapshots.join('\n')]]) {
      expect(text, `${where} 里不该有提示词`).not.toContain(SECRET)
      expect(text, `${where} 里不该有请求 id`).not.toContain(rid)
    }
  })
})
