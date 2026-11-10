import { mkdirSync } from 'node:fs'
import path from 'node:path'
import { expect, test, waitForDocumentReady } from './fixtures'
import type { Page } from '@playwright/test'

/**
 * 上次的排版没能恢复时，用户的动作不丢（ADR 0120 修订 2026-11-10）。
 *
 * 真实后端 + 真实界面走一遍用户报的那条路：重开项目 → 上次的排版读不出来 → 把图加进画布。
 * 修订前：五个入口全被拒，只在状态条说一句「上次的排版还没打开。」，画布是一片空白。
 * 修订后钉三件事：
 *   1. 空画布正中就是出口（`data-canvas-restore-issue`：一句话 + 主按钮），不摆点了也放不进去的选图卡；
 *   2. 用户动了手：动作记住（卡片 `data-restore-held`），暂时读不到那种把「继续新建」摆到「重试」旁边；
 *   3. 用户选了出口，图**不用再加一次**就在画布上。
 *
 * 主语：恢复门认舞台的 `data-document-state`，卡片与按钮认 `data-*`，不认文案。
 * 「读不出来」用拦截正文 GET 回 500 造（后端与磁盘都是真的，只有这一个响应是假的）。
 */

const SHOTS = process.env.TAVOTTO_SHOTS

async function shot(page: Page, name: string) {
  if (!SHOTS) return
  mkdirSync(SHOTS, { recursive: true })
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme })
    await page.screenshot({ path: path.join(SHOTS, `${name}-${scheme}.png`) })
  }
  await page.emulateMedia({ colorScheme: 'light' })
}

/** 素材卡聚焦后 Shift+Enter = 「添加到画布」（与 `addSecondFigure` 同一个动作） */
async function addFromLibrary(page: Page, cardId: string) {
  const rail = page.locator('[data-rail="assets"]')
  if ((await rail.getAttribute('aria-expanded')) !== 'true') await rail.click()
  const card = page.locator(`[data-card="${cardId}"]`)
  await card.focus()
  await page.keyboard.press('Shift+Enter')
}

test('上次的排版读不出来：出口在画布正中；加图的动作记住，选完就在画布上', async ({ page, app }) => {
  const a = await app()
  await page.goto(a.baseURL)
  await waitForDocumentReady(page)

  // 先攒一份「上次的排版」：放一张图，等后端真的收下那次保存
  const saved = page.waitForResponse(
    (r) => r.url().includes('/api/autosave/') && r.request().method() === 'PUT' && r.ok(),
    { timeout: 60_000 },
  )
  await addFromLibrary(page, 'Fig2_correlation.pdf')
  await expect(page.locator('[data-canvas-stage] [data-object-id]')).toHaveCount(1, { timeout: 60_000 })
  await saved

  // 重开：正文读不出来（一直 500）
  await page.route('**/api/autosave/**', (route) =>
    route.request().method() === 'GET' ? route.fulfill({ status: 500, body: '{"error":"boom"}' }) : route.fallback(),
  )
  await page.reload()
  const stage = page.locator('[data-canvas-stage]')
  await expect(stage).toHaveAttribute('data-document-state', 'needs_user', { timeout: 60_000 })

  // 1. 出口在画布正中，没有点了也放不进去的选图卡
  const card = page.locator('[data-canvas-restore-issue]')
  await expect(card).toBeVisible()
  await expect(page.locator('[data-canvas-empty-hint]')).toHaveCount(0)
  await expect(card.locator('[data-restore-action][data-variant="primary"]')).toHaveCount(1)
  await expect(card).not.toHaveAttribute('data-restore-held', '')
  await shot(page, '1-idle')

  // 2. 用户动手：动作记住（自动再试一次仍读不出来 → 回到等用户选）
  await addFromLibrary(page, 'Fig2_correlation.pdf')
  await expect(card).toHaveAttribute('data-restore-held', '', { timeout: 30_000 })
  await expect(stage).toHaveAttribute('data-document-state', 'needs_user')
  await expect(page.locator('[data-canvas-stage] [data-object-id]')).toHaveCount(0)
  const useNew = card.locator('[data-restore-action="continue-new"]')
  await expect(useNew).toBeVisible()
  await shot(page, '2-held')

  // 3. 选了出口：图不用再加一次
  await useNew.click()
  await expect(stage).toHaveAttribute('data-document-state', 'ready', { timeout: 30_000 })
  await expect(page.locator('[data-canvas-stage] [data-object-id]')).toHaveCount(1, { timeout: 60_000 })
  await expect(card).toHaveCount(0)
  await shot(page, '3-after')
})
