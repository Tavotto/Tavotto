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
  const drawer = await only(page.locator('[data-timeline-panel]'))
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

test('单击一行弹出对比卡片（悬停不冒按钮）；⋯ 里的「改名」进入改名，卡片的「放大查看」打开预览', async ({ app, page }) => {
  const a = await app()
  await page.goto(a.baseURL)
  const { drawer, node } = await drawerWithOneNode(page)
  const row = await only(node.locator('[data-timeline-row]'))
  // 悬停只有轻微底色，不再冒「预览」「恢复到这里」两颗按钮
  await row.hover()
  await expect(node.locator('[data-timeline-row-actions]')).toHaveCount(0)
  await row.click()
  await expect(row).toHaveAttribute('aria-expanded', 'true')
  await expect(await only(page.locator('[data-timeline-card]'))).toBeVisible()
  await expect(page.locator('[data-dialog="timeline-preview"]')).toHaveCount(0)
  await page.keyboard.press('Escape') // Esc 收起卡片，时间线页签还在
  await expect(page.locator('[data-timeline-card]')).toHaveCount(0)
  await expect(drawer).toBeVisible()
  // 一行的动作只有一颗 ⋯（2026-10-07 设计审计 §10.2）；菜单 portal 在 body 上
  await (await only(node.locator('[data-timeline-more]'))).click()
  await (await only(page.locator('[data-timeline-rename-button]'))).click()
  await expect(await only(drawer.locator('[data-timeline-rename]'))).toBeFocused()
  await page.keyboard.press('Escape') // 放弃改名
  await expect(drawer.locator('[data-timeline-rename]')).toHaveCount(0)
  await row.click()
  await (await only(page.locator('[data-timeline-card-enlarge]'))).click()
  await expect(await only(page.locator('[data-dialog="timeline-preview"]'))).toBeVisible()
})

for (const [locale, width] of [['zh-CN', 320], ['en-US', 320], ['zh-CN', 480]] as const) {
  test(`时间线是右栏页签，最窄 ${width}px 下列表与页签行不溢出，预览对话框照常打开 (${locale})`, async ({ app, page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.addInitScript(
      ({ locale, width }) => {
        localStorage.setItem('tavotto.locale', locale)
        localStorage.setItem('tavotto.ui', JSON.stringify({ prefsVersion: 2, rightWidth: width }))
      },
      { locale, width },
    )
    const a = await app()
    await page.goto(a.baseURL)
    const { node } = await drawerWithOneNode(page)
    // 在右栏里，而不是盖在画布上的浮卡
    await expect(page.locator('[data-inspector-panel] [data-timeline-panel]')).toHaveCount(1)
    await expect(page.locator('[data-timeline-drawer]')).toHaveCount(0)
    await expect(page.locator('[data-inspector-tab="timeline"]')).toHaveAttribute('aria-selected', 'true')
    const { horizontalOffenders } = await import('./overflow')
    expect(await horizontalOffenders(page, '[data-inspector-panel]')).toEqual([])
    await row(node).click()
    expect(await horizontalOffenders(page, '[data-inspector-panel]')).toEqual([])
    const shots = process.env.TAVOTTO_SHOTS
    if (shots) await page.screenshot({ path: `${shots}/timeline-tab-${locale}-${width}.png` })
    const card = await only(page.locator('[data-timeline-card]'))
    await expect(card).toBeVisible()
    // 最窄的右栏下卡片也整张落在视口里（Radix 会翻边 / 贴边）
    const box = (await card.boundingBox())!
    const vp = page.viewportSize()!
    expect(box.x).toBeGreaterThanOrEqual(0)
    expect(box.x + box.width).toBeLessThanOrEqual(vp.width)
    if (shots) await page.screenshot({ path: `${shots}/timeline-card-${locale}-${width}.png` })
    await (await only(card.locator('[data-timeline-card-enlarge]'))).click()
    await expect(await only(page.locator('[data-dialog="timeline-preview"]'))).toBeVisible()
  })
}

const row = (node: Locator) => node.locator('[data-timeline-row]')

test('多张画布：时间线默认只显示当前画布自己的节点，切到「全部画布」才混在一起', async ({ app, page }) => {
  const a = await app()
  await page.goto(a.baseURL)
  const { drawer } = await drawerWithOneNode(page)
  // 只有一张画布：没有范围切换
  await expect(drawer.locator('[data-timeline-scope]')).toHaveCount(0)
  await page.getByRole('button', { name: '新建画布', exact: true }).first().click()
  const scope = await only(drawer.locator('[data-timeline-scope]'), 15_000)
  // 新画布还没有自己的节点：第一张画布的那条「打开项目」不混进来
  await expect(drawer.locator('[data-timeline-node]')).toHaveCount(0)
  const shots = process.env.TAVOTTO_SHOTS
  if (shots) await page.screenshot({ path: `${shots}/timeline-scope-new-canvas.png` })
  await (await only(scope.locator('[data-value="all"]'))).click()
  const node = await only(drawer.locator('[data-timeline-node]'))
  // 看全部时，每个节点说自己来自哪张画布
  await expect(node).toContainText('Figure 1')
  if (shots) await page.screenshot({ path: `${shots}/timeline-scope-all.png` })
})
