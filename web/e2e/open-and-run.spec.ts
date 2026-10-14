import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, test } from './fixtures'
import { card, primary } from './prepCard'

/**
 * 「打开并运行」黄金路径（V2 PR4，ADR 0124 / 0127）——真后端、真 worker、真浏览器，待测项目开始时**只有脚本**（没有图、没有注册表）。
 *
 * 判据的主语是脚本自己写在项目**之外**的运行日志（每次执行一行）与浏览器发出的真实请求，不是 toast：
 *
 *   1. 素材库脚本行 ▶ **点一次**：检查 → 认领后端的 `run_when_ready`（回显授权摘要）→ 运行 → 自动进入编辑，之后不再点任何东西；
 *      脚本恰好执行一次；浏览器发的每一个准备会话请求里都没有 `execution`（回退是后端同一会话里的事，前端永不发）；
 *   2. 要先回答运行目录：停在目录卡上（不代认领、不执行），答完卡上的主按钮是「打开并运行」，点它之后不再点任何东西就进入编辑。
 *
 * 没有覆盖（如实）：safe→native 静默回退只对桌面窗口发起的运行生效（`--desktop-sidecar` + 窗口 cookie，浏览器模式 fail closed），
 * 这里的浏览器 e2e 触发不了——后端回退由 `tests/test_execution_*` 等真子进程用例钉，前端「回退不多开标签页」由 `store/openAndRun.test.ts` 模拟钉。
 */

const logLines = (log: string): { event: string; argv: string[] }[] =>
  existsSync(log)
    ? readFileSync(log, 'utf-8')
        .split('\n')
        .filter(Boolean)
        .map((l) => JSON.parse(l) as { event: string; argv: string[] })
    : []

const script = (log: string, readsData: boolean): string =>
  [
    'import json, sys',
    'import matplotlib',
    'matplotlib.use("Agg")',
    'import matplotlib.pyplot as plt',
    '',
    `with open(${JSON.stringify(log)}, "a", encoding="utf-8") as f:`,
    '    f.write(json.dumps({"event": "run", "argv": sys.argv[1:]}) + "\\n")',
    readsData
      ? 'values = [float(x) for x in open("data/values.txt", encoding="utf-8").read().split()]'
      : 'values = [1.0, 2.0, 3.0]',
    'fig, ax = plt.subplots(figsize=(3, 2))',
    'ax.plot(range(len(values)), values)',
    'fig.savefig("fig_out.pdf")',
    '',
  ].join('\n')

/** 记下浏览器发给准备会话端点的每个 POST：创建（会话）与动作 */
function watchSessions(page: import('@playwright/test').Page) {
  const created: string[] = []
  const actions: string[] = []
  page.on('request', (r) => {
    if (r.method() !== 'POST') return
    const p = new URL(r.url()).pathname
    if (p === '/api/engine/preparation-sessions') created.push(r.postData() ?? '')
    else if (/^\/api\/engine\/preparation-sessions\/[^/]+\/actions$/.test(p)) actions.push(r.postData() ?? '')
  })
  return { created, actions }
}

test(
  '素材库 ▶ 点一次：检查 → 认领 run_when_ready → 运行 → 自动进入编辑，脚本恰好执行一次、请求里没有 execution',
  { tag: '@feature:assets.open-and-run' },
  async ({ app, page }) => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'tavotto-openrun-'))
  const project = path.join(root, 'proj')
  const log = path.join(root, 'runs.jsonl')
  mkdirSync(project, { recursive: true })
  writeFileSync(path.join(project, 'fig.py'), script(log, false), 'utf-8')
  let a: Awaited<ReturnType<typeof app>> | undefined
  try {
    a = await app({ figures: project })
    await page.setViewportSize({ width: 1400, height: 900 })
    const seen = watchSessions(page)
    await page.goto(a.baseURL)
    const run = page.locator('[data-script-run="fig.py"]')
    await expect(run).toBeVisible({ timeout: 30_000 })
    await run.click() // 唯一的一次点击

    // 之后不再点任何东西：自动进入编辑，图内元素可见，卡片随即收起
    await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 180_000 })
    await expect(card(page)).toHaveCount(0)

    // 真值：脚本恰好执行一次；创建一个会话、认领一个动作（带授权摘要）；任何请求都没有 execution
    expect(logLines(log)).toEqual([{ event: 'run', argv: [] }])
    expect(seen.created).toHaveLength(1)
    expect(seen.actions).toHaveLength(1)
    const claim = JSON.parse(seen.actions[0]) as Record<string, unknown>
    expect(typeof claim.impact_digest).toBe('string')
    expect(claim.transfer_from).toBeUndefined()
    expect([...seen.created, ...seen.actions].join('')).not.toContain('execution')
  } finally {
    await a?.stop().catch(() => undefined)
    rmSync(root, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 })
  }
  },
)

test('要先回答运行目录：停在目录卡上，答完是「打开并运行」，点它之后不再点任何东西就进入编辑', async ({ app, page }) => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'tavotto-openrun-w-'))
  const project = path.join(root, 'proj')
  const log = path.join(root, 'runs.jsonl')
  mkdirSync(path.join(project, 'tools'), { recursive: true })
  mkdirSync(path.join(project, 'data'), { recursive: true })
  writeFileSync(path.join(project, 'tools', 'plot.py'), script(log, true), 'utf-8')
  writeFileSync(path.join(project, 'data', 'values.txt'), '3 1 4\n', 'utf-8')
  let a: Awaited<ReturnType<typeof app>> | undefined
  try {
    a = await app({ figures: project })
    await page.setViewportSize({ width: 1400, height: 900 })
    const seen = watchSessions(page)
    await page.goto(a.baseURL)
    // 引导卡「打开并运行」：弹出时不建会话；点它才建。有要先答的事（运行目录）时不代认领，停在目录卡上
    await expect(card(page)).toHaveAttribute('data-prep-state', 'discover', { timeout: 30_000 })
    expect(seen.created).toEqual([])
    await primary(page).click()
    await expect(card(page)).toHaveAttribute('data-prep-state', 'workdir', { timeout: 60_000 })
    expect(logLines(log)).toEqual([]) // 问目录之前不执行
    expect(seen.actions).toEqual([]) // 也没有认领任何动作
    await expect(primary(page)).toHaveText('用项目根目录')
    await primary(page).click() // 答目录（只重新检查）
    await expect(card(page)).toHaveAttribute('data-prep-state', 'ready', { timeout: 60_000 })
    expect(logLines(log)).toEqual([])
    await expect(primary(page)).toHaveAttribute('data-prep-primary', 'run_when_ready')
    await expect(primary(page)).toHaveText('打开并运行')
    await primary(page).click() // 最后一次点击

    await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 180_000 })
    await expect(card(page)).toHaveCount(0)
    expect(logLines(log)).toEqual([{ event: 'run', argv: [] }])
    expect(seen.created).toHaveLength(1)
    expect([...seen.created, ...seen.actions].join('')).not.toContain('execution')
    // 认领的是 run_when_ready（带授权摘要），不是 run；答目录走的是 recheck
    expect(seen.actions.filter((b) => b.includes('impact_digest'))).toHaveLength(1)
  } finally {
    await a?.stop().catch(() => undefined)
    rmSync(root, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 })
  }
})

test('运行中卡上就是「停止运行」，没有「tavotto run」式卡；点它当场停下', async ({ app, page }) => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'tavotto-openrun-s-'))
  const project = path.join(root, 'proj')
  const log = path.join(root, 'runs.jsonl')
  const hold = path.join(root, 'HOLD')
  mkdirSync(project, { recursive: true })
  writeFileSync(hold, 'hold')
  writeFileSync(
    path.join(project, 'fig.py'),
    [
      'import os, time',
      `open(${JSON.stringify(log)}, "a").write("x")`,
      `while os.path.exists(${JSON.stringify(hold)}):`,
      '    time.sleep(0.1)',
      '',
    ].join('\n'),
    'utf-8',
  )
  let a: Awaited<ReturnType<typeof app>> | undefined
  try {
    a = await app({ figures: project })
    await page.setViewportSize({ width: 1400, height: 900 })
    await page.goto(a.baseURL)
    await page.locator('[data-script-run="fig.py"]').click({ timeout: 30_000 })
    await expect(card(page)).toHaveAttribute('data-prep-state', 'running', { timeout: 120_000 })
    await expect(card(page).locator('[data-prep-stop]')).toHaveText('停止运行')
    await expect(page.locator('[data-native-session-cards]')).toHaveCount(0)
    expect(JSON.stringify(await card(page).textContent())).not.toMatch(/native|tavotto run/i)
    await card(page).locator('[data-prep-stop]').click()
    await expect(card(page)).toHaveAttribute('data-prep-state', 'cancelled', { timeout: 30_000 })
  } finally {
    rmSync(hold, { force: true })
    await a?.stop().catch(() => undefined)
    rmSync(root, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 })
  }
})
