import { expect, test } from './fixtures'

/**
 * 时间线行上的操作在真浏览器里走得通（Codex #679）。
 *
 * jsdom 的用例派发的是裸 `dblclick`，量不到真实双击：浏览器先发两次 click 再发 dblclick。
 * 改造前单击就开模态预览，遮罩盖住这一行，第二下落不到行上——「双击改名」宣称有、实际
 * 用不了，而单测一直是绿的。这里用 Playwright 的 `dblclick()`（带前导 click 序列）量。
 *
 * 定位一律认 data-* 钩子并先断言恰好一个；按文字核对的只有用户敲进去的节点名。
 */

type Page = import('@playwright/test').Page
type Locator = import('@playwright/test').Locator

async function only(loc: Locator, timeout?: number): Promise<Locator> {
  await expect(loc).toHaveCount(1, timeout ? { timeout } : undefined)
  return loc
}

/**
 * 打开时间线，拿到那唯一的一个节点：第一次启动时的「打开项目」关键时刻（空画布也打）。
 * 画布一直空着，自动节点不拍（空画布不拍），行数稳定。
 */
async function drawerWithOneNode(page: Page) {
  await (await only(page.locator('[data-timeline-button]'), 30_000)).click()
  const drawer = await only(page.locator('[data-timeline-drawer]'))
  const node = await only(drawer.locator('[data-timeline-node]'), 15_000)
  return { drawer, node }
}

test('双击一行进入改名（前导 click 不开模态），回车存成命名节点', async ({ app, page }) => {
  const a = await app()
  await page.goto(a.baseURL)
  const { drawer, node } = await drawerWithOneNode(page)
  await (await only(node.locator('[data-timeline-row]'))).dblclick()
  // 双击的前两下 click 只选中：没有模态盖住这一行
  await expect(page.locator('[data-dialog="timeline-preview"]')).toHaveCount(0)
  const input = await only(drawer.locator('[data-timeline-rename]'))
  await expect(input).toBeFocused()
  await input.fill('投稿前')
  await input.press('Enter')
  const named = await only(drawer.locator('[data-timeline-node][data-timeline-named]'), 15_000)
  await expect(named.locator('[data-timeline-name]')).toHaveText('投稿前')
})

test('单击一行只选中；行上的「预览」钮打开预览，「改名」钮进入改名', async ({ app, page }) => {
  const a = await app()
  await page.goto(a.baseURL)
  const { drawer, node } = await drawerWithOneNode(page)
  const row = await only(node.locator('[data-timeline-row]'))
  await row.click()
  await expect(row).toHaveAttribute('aria-pressed', 'true')
  await expect(page.locator('[data-dialog="timeline-preview"]')).toHaveCount(0)
  await (await only(node.locator('[data-timeline-rename-button]'))).click()
  await expect(await only(drawer.locator('[data-timeline-rename]'))).toBeFocused()
  await page.keyboard.press('Escape') // 放弃改名
  await expect(drawer.locator('[data-timeline-rename]')).toHaveCount(0)
  await (await only(node.locator('[data-timeline-preview-button]'))).click()
  await expect(await only(page.locator('[data-dialog="timeline-preview"]'))).toBeVisible()
})
