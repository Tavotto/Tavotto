import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import type { Page } from '@playwright/test'
import { expect, ONBOARDING_FLOW_VERSION, test } from './fixtures'

/**
 * 脚本即图片（ADR 0184）：主页拖入一个 .py，素材区只显示这个脚本的图——
 * 同文件夹里没有脚本画的散图不列出来；同一张图存成 PDF / PNG / SVG 三份（PNG、SVG 在格式名子目录里）只是一张卡，
 * 卡上放的是 PDF。判据的主语是素材区里真实渲染出来的卡（`data-card` = 文件 id），不是后端响应。
 */

const COMPLETED = JSON.stringify({
  schemaVersion: 1,
  flowVersion: ONBOARDING_FLOW_VERSION,
  status: 'completed',
  currentStep: null,
  completedSteps: [],
  skippedSteps: [],
  hintSeen: {},
  startedAt: 1,
  completedAt: 2,
  tutorialProjectId: null,
  tutorialDocumentId: null,
  pausedBy: null,
})

const SCRIPT = [
  'import os',
  'import matplotlib',
  'matplotlib.use("Agg")',
  'import matplotlib.pyplot as plt',
  '',
  'os.makedirs("png", exist_ok=True)',
  'os.makedirs("svg", exist_ok=True)',
  'fig, ax = plt.subplots(figsize=(3, 2))',
  'ax.plot([0, 1, 2], [1, 3, 2])',
  'fig.savefig("fig1.pdf")',
  'fig.savefig("png/fig1.png", dpi=100)',
  'fig.savefig("svg/fig1.svg")',
  '',
].join('\n')

/** 1×1 的合法 PNG：没有脚本画它，从脚本进来时不该出现在素材区 */
const TINY_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64',
)

/** 一页 72×54 pt 的最小 PDF：代替脚本之前存好的那份 */
const TINY_PDF = Buffer.from(
  [
    '%PDF-1.4',
    '1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj',
    '2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj',
    '3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 72 54]>>endobj',
    'trailer<</Root 1 0 R>>',
    '%%EOF',
    '',
  ].join('\n'),
  'latin1',
)

const TINY_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" width="72pt" height="54pt" viewBox="0 0 72 54"><rect width="72" height="54" fill="#fff"/></svg>\n'

/**
 * 打开素材抽屉（幂等）：按左栏按钮的 `aria-expanded` 判断开没开——抽屉有开合动画，`isVisible` 会在
 * 合上的过程中仍为真，按它决定点不点会把抽屉点关（Windows CI 上实际发生过）。
 */
async function showAssets(page: Page) {
  const rail = page.locator('[data-rail="assets"]')
  await expect(async () => {
    if ((await rail.getAttribute('aria-expanded')) !== 'true') await rail.click()
    await expect(rail).toHaveAttribute('aria-expanded', 'true', { timeout: 2_000 })
    await expect(page.locator('[data-asset-list]')).toBeVisible({ timeout: 2_000 })
  }).toPass({ timeout: 30_000 })
}

/** 素材区里真实渲染出来的图卡的文件 id（只取卡本身：`role="option"`） */
const cardIds = (page: Page) =>
  page
    .locator('[data-asset-list] [role="option"][data-card]')
    .evaluateAll((els) => els.map((e) => e.getAttribute('data-card')))

async function dropScript(page: Page, file: string) {
  const zone = page.locator('[data-home-dropzone]')
  await expect(zone).toBeVisible({ timeout: 30_000 })
  const dt = await page.evaluateHandle(
    ([uri, name]) => {
      const d = new DataTransfer()
      d.setData('text/uri-list', uri)
      d.items.add(new File(['# py\n'], name, { type: 'text/x-python' }))
      return d
    },
    [pathToFileURL(file).href, path.basename(file)] as const,
  )
  await zone.dispatchEvent('dragenter', { dataTransfer: dt })
  await zone.dispatchEvent('drop', { dataTransfer: dt })
}

test(
  '功能：拖入脚本后素材区只显示这个脚本的图，同一张图的 PDF / PNG / SVG 是一张卡',
  { tag: '@feature:assets.script-is-figure' },
  async ({ app, page }) => {
    const root = mkdtempSync(path.join(os.tmpdir(), 'tavotto-scriptfig-'))
    const project = path.join(root, 'proj')
    mkdirSync(path.join(project, 'raw'), { recursive: true })
    writeFileSync(path.join(project, 'plot.py'), SCRIPT, 'utf-8')
    // 用户以前跑过这个脚本：三份输出已经在磁盘上（Tavotto 的安全运行会拦下 savefig，不会替用户写这些文件）
    mkdirSync(path.join(project, 'png'))
    mkdirSync(path.join(project, 'svg'))
    writeFileSync(path.join(project, 'fig1.pdf'), TINY_PDF)
    writeFileSync(path.join(project, 'png', 'fig1.png'), TINY_PNG)
    writeFileSync(path.join(project, 'svg', 'fig1.svg'), TINY_SVG, 'utf-8')
    for (let i = 1; i <= 5; i++) writeFileSync(path.join(project, 'raw', `img_${i}.png`), TINY_PNG)
    let a: Awaited<ReturnType<typeof app>> | undefined
    try {
      await page.addInitScript((blob) => window.localStorage.setItem('tavotto.onboarding', blob), COMPLETED)
      a = await app({ noProject: true })
      await page.setViewportSize({ width: 1400, height: 900 })
      await page.goto(a.baseURL)

      await dropScript(page, path.join(project, 'plot.py'))
      // 跑完、恰好一张图 → 放进画布并进入编辑；退回排版再看素材区
      await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 180_000 })
      await page.keyboard.press('Escape')
      await showAssets(page)

      // 只有一张卡，是 PDF（PNG、SVG 是同一张图），散图一张都没有
      await expect.poll(() => cardIds(page), { timeout: 60_000 }).toEqual(['fig1.pdf'])
      await expect(page.locator('[data-asset-list] [data-card="fig1.pdf"]')).toBeInViewport()
      await expect(page.locator('[data-asset-list]').getByText('img_1.png')).toHaveCount(0)
    } finally {
      await a?.stop().catch(() => undefined)
      rmSync(root, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 })
    }
  },
)

const R_SCRIPT = [
  'library(ggplot2)',
  'df <- read.csv("data.csv")',
  'p <- ggplot(df, aes(x, y)) + geom_line()',
  'ggsave("fig2.pdf", p, width = 4, height = 3)',
  'ggsave(file.path("png", "fig2.png"), p, width = 4, height = 3, dpi = 100)',
  '',
].join('\n')

async function dropFile(page: Page, file: string, type: string) {
  const zone = page.locator('[data-home-dropzone]')
  await expect(zone).toBeVisible({ timeout: 30_000 })
  const dt = await page.evaluateHandle(
    ([uri, name, mime]) => {
      const d = new DataTransfer()
      d.setData('text/uri-list', uri)
      d.items.add(new File(['#\n'], name, { type: mime }))
      return d
    },
    [pathToFileURL(file).href, path.basename(file), type] as const,
  )
  await zone.dispatchEvent('dragenter', { dataTransfer: dt })
  await zone.dispatchEvent('drop', { dataTransfer: dt })
}

test(
  '功能：拖入 R 脚本，素材区同样只显示它存的图，PDF 与 PNG 是一张卡',
  { tag: '@feature:assets.script-is-figure' },
  async ({ app, page }) => {
    const root = mkdtempSync(path.join(os.tmpdir(), 'tavotto-scriptfig-r-'))
    const project = path.join(root, 'proj')
    mkdirSync(path.join(project, 'raw'), { recursive: true })
    mkdirSync(path.join(project, 'png'), { recursive: true })
    writeFileSync(path.join(project, 'plot.R'), R_SCRIPT, 'utf-8')
    writeFileSync(path.join(project, 'fig2.pdf'), TINY_PDF)
    writeFileSync(path.join(project, 'png', 'fig2.png'), TINY_PNG)
    for (let i = 1; i <= 5; i++) writeFileSync(path.join(project, 'raw', `img_${i}.png`), TINY_PNG)
    let a: Awaited<ReturnType<typeof app>> | undefined
    try {
      await page.addInitScript((blob) => window.localStorage.setItem('tavotto.onboarding', blob), COMPLETED)
      a = await app({ noProject: true })
      await page.setViewportSize({ width: 1400, height: 900 })
      await page.goto(a.baseURL)

      await dropFile(page, path.join(project, 'plot.R'), 'text/plain')
      // 先等「生成可编辑版本」卡出现：打开项目后的这一阵界面还在变，之后再看素材区
      await expect(page.locator('[data-foreign-card="r"]')).toBeVisible({ timeout: 60_000 })
      await showAssets(page)
      await expect.poll(() => cardIds(page), { timeout: 60_000 }).toEqual(['fig2.pdf'])
      await expect(page.locator('[data-asset-list] [data-card="fig2.pdf"]')).toBeInViewport()
      await expect(page.locator('[data-asset-list]').getByText('img_1.png')).toHaveCount(0)
    } finally {
      await a?.stop().catch(() => undefined)
      rmSync(root, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 })
    }
  },
)
