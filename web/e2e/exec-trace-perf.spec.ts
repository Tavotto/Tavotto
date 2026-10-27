import { chmodSync, mkdtempSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, test, waitForFigureEdit } from './fixtures'

/**
 * 大 Trace 的性能测量（ADR 0151 PR3 §3.6）：接近存储上限（`MAX_SPANS = 512`）的一份**运行中**快照——8 个 Agent 运行、
 * 每个 60 个模型 / 工具调用（相邻两个并行）、200 个阶段观察点。只替换 `/api/exec-trace` 的响应体（功能验收在
 * `exec-trace.spec.ts` 走真后端），量：展开到画完的耗时、运行中 5 秒实时刷新期间的长任务（>50ms）。数字写进 test-results。
 */
test.skip(process.platform === 'win32', '假 CLI 是 shell 脚本，只在 posix-e2e 腿执行')

function bigTrace(startedAt: number) {
  const spans: Record<string, unknown>[] = []
  const events: Record<string, unknown>[] = []
  const base = (o: Record<string, unknown>) => ({
    parent_span_id: 'root',
    error_code: null,
    attempt: 1,
    timing: 'measured',
    attributes: {},
    usage: null,
    ...o,
  })
  spans.push(base({ span_id: 'root', parent_span_id: null, name: 'task', kind: 'task', start_offset_ms: 0, end_offset_ms: null, duration_ms: null, status: 'running' }))
  let seq = 0
  for (let a = 0; a < 8; a++) {
    const id = `run${a}`
    const t0 = a * 6000
    spans.push(base({ span_id: id, name: 'agent_run', kind: 'agent', start_offset_ms: t0, end_offset_ms: a === 7 ? null : t0 + 6000, duration_ms: a === 7 ? null : 6000, status: a === 7 ? 'running' : 'ok', attributes: { agent: 'builtin', opaque: false } }))
    for (let i = 0; i < 60; i++) {
      const s = t0 + Math.floor(i / 2) * 190
      const model = i % 2 === 0
      spans.push(
        base({
          span_id: `${id}-${i}`,
          parent_span_id: id,
          name: model ? 'model_request' : 'tool_call',
          kind: model ? 'model' : 'tool',
          start_offset_ms: s,
          end_offset_ms: s + 170,
          duration_ms: 170,
          status: i % 23 === 0 ? 'failed' : 'ok',
          error_code: i % 23 === 0 ? 'tool_failed' : null,
          attributes: model ? {} : { tool: 'read_file' },
          usage: model
            ? { source: 'reported', input_tokens: 1200, output_tokens: 80, cached_input_tokens: 900, cost: { amount: 0.0011, currency: 'USD', pricing_version: '2026-10-27', estimated: true } }
            : null,
        }),
      )
    }
    for (let k = 0; k < 25; k++) {
      events.push({ seq: ++seq, name: 'stage', span_id: id, at_offset_ms: t0 + k * 230, attributes: { phase: 'execute', outcome: 'ok' } })
    }
  }
  return {
    schema_version: 1,
    trace_id: 'f'.repeat(32),
    operation_kind: 'ai_task',
    started_at: startedAt,
    ended_at: null,
    status: 'running',
    error_code: null,
    root_span_id: 'root',
    first_failure: { span_id: 'run0-0', name: 'model_request', error_code: 'tool_failed', attempt: 1 },
    truncated: false,
    dropped_count: 0,
    complete: false,
    spans,
    events,
    last_seq: seq,
  }
}

test('大 Trace：展开耗时与实时刷新期间的长任务', async ({ app, page }, info) => {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'tavotto-fake-codex-'))
  const bin = path.join(dir, 'codex')
  writeFileSync(bin, '#!/bin/sh\ncase "$1" in\n  --version) echo "codex-cli 0.0.1" ;;\n  login) echo "Logged in" ;;\n  *) sleep 60 ;;\nesac\n')
  chmodSync(bin, 0o755)
  const a = await app({ env: { PATH: `${dir}${path.delimiter}${process.env.PATH ?? ''}` } })
  const startedAt = Date.now() / 1000 - 46
  await page.route('**/api/exec-trace?**', (route) => route.fulfill({ json: { available: true, trace: bigTrace(startedAt) } }))
  await page.goto(a.baseURL)
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  await waitForFigureEdit(page)
  await page.locator('[data-inspector-tab="assistant"]').click()
  await page.locator('[data-ai-input]').fill('把线条加粗一点')
  await page.locator('[data-ai-send="send"]').click()
  await expect(page.locator('[data-ai-session="running"]')).toBeVisible({ timeout: 30_000 })

  await page.evaluate(() => {
    const w = window as unknown as { __long: number[] }
    w.__long = []
    new PerformanceObserver((list) => list.getEntries().forEach((e) => w.__long.push(e.duration))).observe({ type: 'longtask', buffered: false })
  })
  const t0 = Date.now()
  await page.locator('[data-ai-trace-toggle]').click()
  await expect(page.locator('[data-trace-row]').first()).toBeVisible({ timeout: 15_000 })
  const openMs = Date.now() - t0
  const rows = await page.locator('[data-trace-row]').count()
  // 首次渲染的长任务属于「展开」，不属于「实时刷新」：静置 1s 后清零再开窗（CI 慢机上首渲会拆成好几个长任务，混进来就量错了对象）
  await page.waitForTimeout(1000)
  await page.evaluate(() => {
    ;(window as unknown as { __long: number[] }).__long = []
  })
  await page.waitForTimeout(5000) // 运行中：每 250ms 往前走一格、每秒重取一次快照
  const long = await page.evaluate(() => (window as unknown as { __long: number[] }).__long)
  const result = {
    spans: bigTrace(startedAt).spans.length,
    stagePoints: 200,
    renderedRows: rows,
    openToFirstRowMs: openMs,
    liveWindowMs: 5000,
    longTasks: long.length,
    longTaskMaxMs: Math.round(Math.max(0, ...long)),
    longTaskTotalMs: Math.round(long.reduce((x, y) => x + y, 0)),
  }
  writeFileSync(info.outputPath('exec-trace-perf.json'), JSON.stringify(result, null, 2))
  console.log('exec-trace-perf', JSON.stringify(result))
  // 预算：展开 < 3s；实时刷新的 5 秒里长任务 ≤ 3 个、单个 < 200ms（2026-10-27 基线：行不做 memo、时间轴随此刻连续伸缩时
  // 是 24 个 / 累计 1.56s——每 250ms 一格全部 689 行重画；改成阶梯时间轴 + 行 memo 后 1 个 / 148ms，见 PR 正文的三次测量）
  expect(openMs).toBeLessThan(3000)
  expect(result.longTasks).toBeLessThanOrEqual(3)
  expect(result.longTaskMaxMs).toBeLessThan(200)
})
