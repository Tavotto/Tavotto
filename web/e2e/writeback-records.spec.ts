import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

/**
 * 写回窗口里的「写回记录」页（2026-10-01，设计稿 C11；ADR 0037 末节）的浏览器腿：**真写回、真记录**。
 *
 * 主语：写回窗口（`[data-write-back-tab]` 页签、`[data-write-back-page="records"]` 记录页）；
 * 时刻：写回事务提交、素材列表重拉**之后**——记录页是从后端 `/api/engine/history` 读的，
 * jsdom 里那份是假响应，只有这里证明「真写回一次，窗口里真的冒出记录页，且起点 + 当前两行都在」。
 * 同一份写回之前窗口里**没有**页签（没有记录时这一页不出现）。
 */

const LEGEND = 'axes_0.legend'

const panelBox = (page: Page) =>
  page.evaluate(() => {
    const all = document.querySelectorAll('[data-object-id]')
    if (all.length !== 1) throw new Error(`画布上应恰有一个对象，实际 ${all.length}`)
    const r = all[0].getBoundingClientRect()
    return { x: r.x, y: r.y, w: r.width, h: r.height }
  })

const timingsCount = (page: Page) =>
  page.evaluate(() => {
    const w = window as unknown as { __MM_PREVIEW_TIMINGS__?: unknown[] }
    return w.__MM_PREVIEW_TIMINGS__?.length ?? 0
  })

test('真写回一次：写回之前窗口没有页签；写回之后冒出「写回记录」页，起点 + 当前两行', async ({ app, page }, testInfo) => {
  const a = await app()
  await page.goto(a.baseURL)
  const card = page.locator('[data-card="Fig1_kinetics.pdf"]')
  await card.focus({ timeout: 30_000 })
  await page.keyboard.press('Shift+Enter')
  await expect(page.locator('[data-object-id]')).toHaveCount(1, { timeout: 30_000 })
  await page.waitForTimeout(1500)

  // 进图内编辑，挪一下图例（一处图内修改）
  const p = await panelBox(page)
  await page.mouse.click(p.x + p.w / 2, p.y + p.h / 2)
  await page.keyboard.press('Enter')
  await expect(page.locator('[data-element-svg] > svg')).toBeVisible({ timeout: 60_000 })
  await page.waitForTimeout(1500)
  const lg = await page.evaluate((id) => {
    const r = document.querySelector(`[data-element-svg] [id="${id}"]`)!.getBoundingClientRect()
    return { x: r.x, y: r.y, w: r.width, h: r.height }
  }, LEGEND)
  await page.mouse.click(lg.x + lg.w / 2, lg.y + lg.h / 2)
  await page.waitForTimeout(300)
  const before = await timingsCount(page)
  await page.keyboard.press('ArrowUp')
  await expect.poll(() => timingsCount(page), { timeout: 60_000 }).toBeGreaterThan(before)
  await page.waitForTimeout(300)

  // 写回：⋯ 第一项。此刻没有写回过——窗口里没有页签
  await page.keyboard.press('Escape')
  await page.keyboard.press('Escape')
  await page.locator('[data-more-menu]').click()
  await page.locator('[data-write-back="open"][data-write-back-entry="menu"]').click()
  const confirm = page.locator('[data-write-back="confirm"]')
  await expect(confirm).toHaveCount(1)
  await expect(page.locator('[data-write-back-tab]')).toHaveCount(0)
  await confirm.click()
  await expect(page.locator('[data-write-back="verified"]')).toHaveCount(1, { timeout: 180_000 })

  // 写回完成：窗口只有结果（面板已是写回后的基线，⋯ 里不再有待写回的目标）。关掉
  await page.getByRole('button', { name: /完成|Done/ }).click()
  await expect(confirm).toHaveCount(0)

  // 属性栏「源文件」里的入口：选中这张图，展开，「写回记录」出现（写回之前它不存在）
  const p2 = await panelBox(page)
  await page.mouse.click(p2.x + p2.w / 2, p2.y + p2.h / 2)
  // 折叠头没有 data-* 锚点：按它的名字开头认（「源文件…」），不碰 PanelSection 的结构
  await page.locator('button[aria-expanded]', { hasText: /^源文件/ }).first().click()
  const entry = page.locator('[data-write-back="records"][data-write-back-entry="inspector"]')
  await expect(entry).toHaveCount(1, { timeout: 30_000 })
  await entry.click()

  // 同一个写回窗口，预先落在记录页：两个页签、起点 + 当前两行
  await expect(page.locator('[data-write-back-tab="records"]')).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('[data-write-back-tab="write"]')).toHaveCount(1)
  const pageEl = page.locator('[data-write-back-page="records"]')
  await expect(pageEl).toBeVisible()
  // 起点「脚本原始」+ 这一次写回（当前）；当前那一行没有「恢复」，起点有
  await expect(pageEl.locator('img')).toHaveCount(2)
  await expect(pageEl.locator('[data-write-back="restore"]')).toHaveCount(1)
  const file = testInfo.outputPath('records-page.png')
  await page.locator('[data-dialog]').first().screenshot({ path: file })
  await testInfo.attach('records-page', { path: file, contentType: 'image/png' })
})
