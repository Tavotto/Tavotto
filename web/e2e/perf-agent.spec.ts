import { randomBytes } from 'node:crypto'
import { mkdirSync, renameSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import type { Locator, Page } from '@playwright/test'
import { addSecondFigure, expect, test, waitForDocumentReady } from './fixtures'

/**
 * Agent 性能脚本：真实浏览器 + 真实 Tavotto 后端 + 用户点击。
 * 只在 playwright.perf.config.ts 被选中；标准 e2e 的 config 不运行它。
 * 浏览器双 rAF = UI 回调后两次帧调度代理，并不代表 GPU 实际呈现。
 */
type Key = 'dialog.presets_open' | 'symbol.alpha' | 'symbol.superscript_2'
  | 'symbol.micron' | 'preset.dimension'
const keys: Key[] = [
  'dialog.presets_open', 'symbol.alpha', 'symbol.superscript_2',
  'symbol.micron', 'preset.dimension',
]
const results: Record<Key, number[]> = {
  'dialog.presets_open': [],
  'symbol.alpha': [],
  'symbol.superscript_2': [],
  'symbol.micron': [],
  'preset.dimension': [],
}

const countObjects = (page: Page) => page.locator('[data-canvas-stage] [data-object-id]').count()

/** 在真实 click capture 时启动计时，等待两个动画帧；不进入 App 源码/调试专有 API。 */
async function measuredClick(page: Page, target: Locator): Promise<number> {
  await target.evaluate((el) => {
    document.documentElement.removeAttribute('data-agent-two-raf')
    el.addEventListener('click', () => {
      const started = performance.now()
      requestAnimationFrame(() => requestAnimationFrame(() => {
        document.documentElement.setAttribute(
          'data-agent-two-raf', String(performance.now() - started),
        )
      }))
    }, { capture: true, once: true })
  })
  await target.click()
  await expect.poll(() => page.evaluate(() =>
    document.documentElement.getAttribute('data-agent-two-raf'),
  )).not.toBeNull()
  const n = await page.evaluate(() =>
    Number(document.documentElement.getAttribute('data-agent-two-raf')),
  )
  expect(Number.isFinite(n) && n >= 0).toBe(true)
  return n
}

async function openPresets(page: Page): Promise<number> {
  await page.locator('[data-tool-menu="annotate"]').click()
  const item = page.locator('[data-perf-open-presets]')
  const delay = await measuredClick(page, item)
  await expect(page.locator('[data-dialog="presets"]')).toBeVisible()
  return delay
}

/** 原子写报告：没有完全跑通的轮次就不会留下伪装成成功的结果。 */
function save(output: string, value: object) {
  mkdirSync(path.dirname(output), { recursive: true })
  // 固定 output.tmp 可恰好等于 --baseline 文件名（甚至是其软/硬链接）。
  // 只有独占创建的随机临时文件才能避免先截断历史样本。
  const tmp = `${output}.${randomBytes(16).toString('hex')}.tmp`
  try {
    writeFileSync(tmp, JSON.stringify(value, null, 2), { encoding: 'utf8', flag: 'wx' })
    renameSync(tmp, output)
  } finally {
    rmSync(tmp, { force: true })
  }
}

test('agent: 自动重复符号插入与预设 UI 交互', async ({ app, page, browser }) => {
  const iterations = Number(process.env.TAVOTTO_PERF_AGENT_REPEATS ?? 16)
  const out = process.env.TAVOTTO_PERF_AGENT_OUTPUT
  expect(out, '必须明确指定输出 JSON 路径').toBeTruthy()
  expect(Number.isInteger(iterations) && iterations >= 3 && iterations <= 60).toBe(true)
  test.setTimeout(180_000 + iterations * 12_000)

  const a = await app()
  await page.goto(a.baseURL)
  await page.locator('[data-card^="Fig1_kinetics."]').dblclick({ timeout: 30_000 })
  await addSecondFigure(page)
  await waitForDocumentReady(page)
  await expect(page.locator('[data-canvas-toolbar]')).toBeVisible()

  // 预热：安装 font cache、首次对话框创建、React 首次布局不纳入热态对比。
  await openPresets(page)
  await page.locator('[data-dialog="presets"] [data-symbol="α"]').click()
  await expect(page.locator('[data-dialog="presets"]')).toBeHidden()
  await page.evaluate(() => new Promise<void>((resolve) =>
    requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
  ))

  const cases = [
    { key: 'symbol.alpha', symbol: 'α' },
    { key: 'symbol.superscript_2', symbol: '²' },
    { key: 'symbol.micron', symbol: 'μm' },
  ] as const

  // 循环每一档，交错而不是先测完 alpha 再测 superscript，减少热态顺序偏差。
  for (let i = 0; i < iterations; i++) {
    for (const item of cases) {
      results['dialog.presets_open'].push(await openPresets(page))
      const before = await countObjects(page)
      const click = page.locator(`[data-dialog="presets"] [data-symbol="${item.symbol}"]`)
      const measured = await measuredClick(page, click)
      results[item.key].push(measured)
      await expect.poll(() => countObjects(page)).toBe(before + 1)
      await expect(page.locator('[data-dialog="presets"]')).toBeHidden()
    }
    // 九种预设采用真实对象视图；量一次自定义组合插入。
    results['dialog.presets_open'].push(await openPresets(page))
    const before = await countObjects(page)
    const click = page.locator('[data-dialog="presets"] [data-preset="dimension"]')
    results['preset.dimension'].push(await measuredClick(page, click))
    await expect.poll(() => countObjects(page)).toBeGreaterThan(before)
    await expect(page.locator('[data-dialog="presets"]')).toBeHidden()
  }

  // dialog 的采样每轮 4 次，按相同数量记录成该场景的 4*iterations。
  const report = {
    schema: 'tavotto-perf-agent/1',
    generated_at: new Date().toISOString(),
    source_sha: /^[a-f0-9]{40}$/.test(process.env.GITHUB_SHA ?? '')
      ? process.env.GITHUB_SHA
      : null,
    iterations,
    coverage: 'browser_webview_proxy_only',
    environment: {
      platform: process.platform,
      arch: process.arch,
      browser: browser.browserType().name(),
      browser_major: Number(browser.version().split('.')[0]),
      browser_version: browser.version(),
      viewport_w: page.viewportSize()?.width ?? null,
      viewport_h: page.viewportSize()?.height ?? null,
      dpr: await page.evaluate(() => window.devicePixelRatio),
      headless: true,
      os_release: os.release(),
      cpu_model: os.cpus()[0]?.model ?? null,
      cpu_count: os.cpus().length,
      total_mem_gib: Math.round(os.totalmem() / 1024 ** 3),
    },
    scenarios: keys.map((id) => ({ id, two_raf_ms: results[id] })),
  }
  save(out!, report)
  expect(results['symbol.alpha']).toHaveLength(iterations)
})
