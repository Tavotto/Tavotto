/**
 * 属性栏的横向溢出门禁（2026-09-12 impeccable critique P2「空间预算」）。
 *
 * 实测过的红：属性栏拖到最窄档 320px 时，刻度页「长度 / 宽度」那一行溢出 26px、
 * 宽度框被裁掉右半边，两种语言都是。修法是那一对在放不下时折成两行；这条用例
 * 把尺子（`e2e/overflow.ts` 的 `horizontalOffenders`，全仓只有这一把）在**出厂宽度
 * 360 与最窄档 320** 各指一遍最容易撑破的三屏：标题（折叠区全展开）、刻度页、
 * 子图页。zh-CN 与 en-US 各跑一遍（后者由 `chromium-en` project 带）。
 *
 * 头部三颗图标钮以前用 4px 的负外边距贴右缘，负外边距让 scrollWidth 恒多 4px
 * ——不是真溢出，但这把尺子指过来就红；本轮把 header 改成左 12 / 右 8 的不对称内边距，几何不变。
 */
import { expect, test } from './fixtures'
import { horizontalOffenders } from './overflow'

const PANEL = '[data-inspector-panel]'
const FOLD = 'button[aria-expanded="false"]:not([disabled]):not([aria-haspopup]):not([role="combobox"])'

test('标题 / 刻度 / 子图三屏在 360 与 320 两档宽度下都不横向溢出', async ({ app, page }) => {
  const a = await app()
  await page.goto(a.baseURL)
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-canvas-stage] img, [data-canvas-stage] svg').first())
    .toBeVisible({ timeout: 60_000 })

  const nav = page.getByRole('navigation').getByRole('button', { name: /图内元素|Figure elements/ })
  if ((await nav.getAttribute('aria-expanded')) !== 'true') await nav.click()
  await expect(nav).toHaveAttribute('aria-expanded', 'true')
  const items = page.getByRole('treeitem')
  await expect(items.first()).toBeVisible({ timeout: 60_000 })
  // 不按 Escape 关气泡：这个应用里 Escape 会把选区往上退
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())

  const inspector = page.locator(PANEL)
  const expandAll = async () => {
    for (let i = 0; i < 16; i++) {
      const c = inspector.locator(FOLD).first()
      if (!(await c.count())) break
      await c.click()
      await page.waitForTimeout(80)
    }
  }
  // 树行的锚点是 `data-el`：元素行 = gid（axes_0.title），分组行 = 父 gid#簇名
  // （axes_0#text）。不按文案找——en 里「Axes 2」（分组 + 计数）与「Axes 1」（子图）
  // 同一个正则都匹配，取第一个匹配就点错了对象（这条用例第一版就是这么红的）
  const row = (el: string) => page.locator(`[role="treeitem"][data-el="${el}"]`)
  const select = async (group: string | null, item: string, heading: RegExp) => {
    if (group && (await row(group).getAttribute('aria-expanded')) !== 'true') {
      await row(group).click()
      await page.keyboard.press('ArrowRight')
    }
    await row(item).click()
    await inspector.locator('[data-inspector-tab="properties"]').click()
    // 主语核验：头部 h2 必须是这一屏的对象，否则量的不是它
    await expect(inspector.locator('h2')).toContainText(heading)
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
    await expandAll()
  }
  const widthOf = async () => (await inspector.boundingBox())!.width
  const narrow = async () => {
    const sep = page.locator('[role="separator"][aria-valuemax="480"]')
    await sep.focus()
    for (let i = 0; i < 4; i++) await page.keyboard.press('ArrowRight')
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
    expect(await widthOf()).toBe(320)
  }

  const screens: [string, string | null, string, RegExp][] = [
    ['标题', 'axes_0#text', 'axes_0.title', /Reaction kinetics/],
    ['刻度', 'axes_0#axis', 'axes_0.xticks', /刻度|ticks/i],
    ['子图', null, 'axes_0', /子图 1|Axes 1/],
  ]

  // 出厂宽度：先证明尺子看到的就是默认那档
  expect(await widthOf()).toBe(360)
  for (const [name, group, item, heading] of screens) {
    await select(group, item, heading)
    expect(await horizontalOffenders(page, PANEL), `${name} @360`).toEqual([])
  }
  await narrow()
  for (const [name, group, item, heading] of screens) {
    await select(group, item, heading)
    expect(await horizontalOffenders(page, PANEL), `${name} @320`).toEqual([])
  }
})
