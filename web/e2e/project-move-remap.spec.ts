import { mkdirSync, mkdtempSync, renameSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import type { Page } from '@playwright/test'
import { expect, freePort, test, waitForDocumentReady } from './fixtures'

/**
 * ADR 0211 第一期 5：项目整夹挪走后，指认过的数据（改指表，ADR 0106）随认领迁到新路径——真后端、真 worker、真浏览器。
 *
 * 形状：脚本读 `data/values.txt`，数据其实在项目里的 `raw/` 下（用户指认过一次「到 raw 里找」，规则的 `to` 在项目内）。
 * 整夹挪走、没按 ⌘S；新路径打开时排版从项目自己的时间线找回（认领），改指表随之迁过来、`to` 换成新根。
 * 判据：在新路径上重跑脚本，**不再弹**「找不到脚本要读的数据」，图照常出来。
 *
 * 改造前：改指表按旧绝对路径作键，新路径上是空的，重跑当场又弹缺数据卡。
 */

// 走旧的同步试运行路径（与 missing-input.spec.ts 同一个开关），重跑的结局在 `/api/registry/probe` 的回包里
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('tavotto.preparationPanel', 'off')
    ;(window as unknown as Record<string, unknown>).__TAVOTTO_TIMELINE_TIMING__ = { debounceMs: 300, minGapMs: 600_000 }
  })
})

/** 两种脚本写法：相对路径（规则的 `from` 是相对串）与按 `__file__` 拼出的绝对路径（规则的 `from` 是旧根内的绝对路径） */
const SHAPES = {
  relative: ['with open("data/values.txt") as f:', '    value = f.read().strip()'],
  file_based: [
    'from pathlib import Path',
    'BASE_DIR = Path(__file__).resolve().parent',
    'path = BASE_DIR / "values.txt"',
    'if not path.exists():',
    '    raise FileNotFoundError(f"Data file not found: {path}")',
    'value = path.read_text().strip()',
  ],
} as const

function writeProject(dir: string, shape: keyof typeof SHAPES): void {
  mkdirSync(path.join(dir, 'raw'), { recursive: true })
  writeFileSync(
    path.join(dir, 'fig.py'),
    [
      'import matplotlib',
      'matplotlib.use("Agg")',
      'import matplotlib.pyplot as plt',
      '',
      ...SHAPES[shape],
      'fig, ax = plt.subplots()',
      'ax.plot([0, 1], [0, int(value)])',
      'fig.savefig("values_" + value + ".png")',
      '',
    ].join('\n'),
    'utf-8',
  )
  writeFileSync(path.join(dir, 'raw', 'values.txt'), '42\n', 'utf-8')
  writeFileSync(path.join(dir, 'tavotto_registry.json'), JSON.stringify({ version: 1, scripts: {} }), 'utf-8')
}

const isProbe = (r: { request(): { method(): string }; url(): string }) =>
  r.request().method() === 'POST' && new URL(r.url()).pathname === '/api/registry/probe'

async function runScript(page: Page) {
  const done = page.waitForResponse(isProbe, { timeout: 180_000 })
  await page.locator('[data-script-run="fig.py"]').click()
  return done
}

for (const shape of ['relative', 'file_based'] as const) test(`整夹挪走后重跑：指认过的数据随项目迁过来，不再弹缺数据卡（${shape}）`, async ({ app, page, browser }) => {
  test.setTimeout(360_000)
  const root = mkdtempSync(path.join(os.tmpdir(), 'tavotto-e2e-moveremap-'))
  try {
    const projA = path.join(root, 'projA')
    const projB = path.join(root, 'projB')
    writeProject(projA, shape)
    const env = { TAVOTTO_DATA_DIR: path.join(root, 'data'), TAVOTTO_CONFIG_DIR: path.join(root, 'config') }
    mkdirSync(env.TAVOTTO_DATA_DIR, { recursive: true })
    const port = await freePort()

    // ---- 1. 旧路径：运行 → 缺数据 → 指认项目里的 raw/ → 出图；把图放上画布，留下排版与时间线节点 ----
    const a1 = await app({ figures: projA, env, port })
    await page.goto(a1.baseURL)
    await waitForDocumentReady(page)
    await expect(page.locator('[data-script-row="fig.py"]')).toBeVisible({ timeout: 30_000 })
    await page.locator('[data-script-run="fig.py"]').click()
    const dialog = page.locator('[data-dialog="missing-input"]')
    await expect(dialog).toBeVisible({ timeout: 120_000 })
    await dialog.locator('[data-testid="missing-input-path-input"]').fill(path.join(projA, 'raw'))
    await dialog.locator('[data-testid="missing-input-use-path"]').click()
    await expect(dialog).toHaveCount(0)
    const card = page.locator('[data-card="runtime:fig.py#values_42"]')
    await expect(card).toBeVisible({ timeout: 120_000 })
    const node = page.waitForResponse(
      (r) => r.request().method() === 'POST' && /\/api\/versions\/[^/]+$/.test(new URL(r.url()).pathname) && r.ok(),
      { timeout: 120_000 },
    )
    const saved = page.waitForResponse(
      (r) => r.request().method() === 'PUT' && new URL(r.url()).pathname.startsWith('/api/autosave/') && r.ok(),
      { timeout: 120_000 },
    )
    await card.dblclick()
    await expect(page.locator('[data-object-id]')).toHaveCount(1, { timeout: 60_000 })
    await node
    await saved // 本机槽位里有这份排版、记在旧路径名下：新路径上认领它才是「挪走」
    await page.close()
    await a1.stop()

    // ---- 2. 整夹挪走、没按 ⌘S：新路径打开，排版从项目自己的时间线找回（认领 = 确认是挪走） ----
    renameSync(projA, projB)
    const a2 = await app({ figures: projB, env, port })
    const ctx = await browser.newContext()
    await ctx.addInitScript(() => localStorage.setItem('tavotto.preparationPanel', 'off'))
    const tab = await ctx.newPage()
    const claimed = tab.waitForResponse(
      (r) => new URL(r.url()).pathname === '/api/layout-session/project-history/claim' && r.ok(),
      { timeout: 60_000 },
    )
    await tab.goto(a2.baseURL)
    await waitForDocumentReady(tab)
    await claimed
    await expect(tab.locator('[data-object-id]')).toHaveCount(1, { timeout: 30_000 })

    // ---- 3. 重跑：不再弹缺数据卡，图照常出来 ----
    await expect(tab.locator('[data-script-row="fig.py"]')).toBeVisible({ timeout: 30_000 })
    const res = await runScript(tab)
    const body = (await res.json()) as { error?: { missing_input?: unknown } }
    expect(res.ok(), JSON.stringify(body)).toBe(true)
    expect(body.error?.missing_input, '重跑不该再报缺数据').toBeUndefined()
    await expect(tab.locator('[data-dialog="missing-input"]')).toHaveCount(0)
    await expect(tab.locator('[data-card="runtime:fig.py#values_42"]')).toBeVisible({ timeout: 60_000 })
    await ctx.close()
    await a2.stop()
  } finally {
    rmSync(root, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 })
  }
})

// 复制场景不迁（旧项目还在）：由后端用例 `tests/test_project_move_migrate.py::test_copied_project_migrates_nothing` 钉住。
