/**
 * 自动化可访问性门禁（审计 P1-09）。
 *
 * 三层：axe 扫描（critical/serious 必须为 0——这是发布门禁，不是建议）、
 * 对话框焦点纪律（trap + Escape 关闭后焦点恢复）、键盘可达性底线。
 * 完整的「纯键盘走完核心流程」在 issue #37 里继续扩：这里守住的是
 * 「不倒退」的底线，先有门禁再逐步抬高。
 *
 * **语言无关写法**：本 spec 同时跑在 zh-CN（chromium / webkit）与 en-US
 * （chromium-en）三个 project 下，选择器一律 role + 双语正则，不写死文案。
 */
import AxeBuilder from '@axe-core/playwright'
import { expect, test } from './fixtures'

/** critical / serious 违规过滤（axe 的 minor/moderate 不在 1.0 门禁内）。
 *  扫描前把动效关掉：对话框/抽屉的进出场动画进行到一半时，axe 对
 *  颜色对比的取样会撞上过渡态，产出不可复现的假阳性；应用本来就支持
 *  prefers-reduced-motion，这也是它的一次真实行使。 */
async function seriousViolations(page: import('@playwright/test').Page) {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  const results = await new AxeBuilder({ page }).analyze()
  return results.violations
    .filter((v) => v.impact === 'critical' || v.impact === 'serious')
    .map((v) => ({
      id: v.id,
      impact: v.impact,
      nodes: v.nodes.slice(0, 5).map((n) => ({
        target: n.target.join(' '),
        why: n.failureSummary?.split('\n').slice(0, 3).join(' '),
      })),
    }))
}

test('项目选择器：axe 无 critical/serious 违规', async ({ app, page }) => {
  const a = await app({ noProject: true })
  await page.goto(a.baseURL)
  await expect(page.getByRole('main')).toBeVisible()
  expect(await seriousViolations(page)).toEqual([])
})

test('工作台（项目已开、画布有面板）：axe 无 critical/serious 违规', async ({
  app,
  page,
}) => {
  const a = await app()
  await page.goto(a.baseURL)
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  // 等面板真的渲染出来再扫，扫到一半加载的骨架屏没有意义
  await expect(page.locator('[data-canvas-stage] img, [data-canvas-stage] svg').first())
    .toBeVisible({ timeout: 60_000 })
  expect(await seriousViolations(page)).toEqual([])
})

test('导出对话框：axe 干净 + 焦点 trap + Escape 关闭后焦点恢复', async ({
  app,
  page,
}) => {
  const a = await app()
  await page.goto(a.baseURL)
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })

  const exportButton = page.getByRole('button', { name: /导出|Export/ }).first()
  await exportButton.focus()
  await page.keyboard.press('Enter') // 键盘打开——鼠标才能开的导出不算可达
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()

  expect(await seriousViolations(page)).toEqual([])

  // 焦点 trap：连按 Tab 一整圈，焦点永远落在对话框里
  for (let i = 0; i < 25; i++) {
    await page.keyboard.press('Tab')
    const inside = await page.evaluate(() => {
      const el = document.activeElement
      return !!el?.closest('[role="dialog"]')
    })
    expect(inside, `第 ${i + 1} 次 Tab 后焦点跑出了对话框`).toBe(true)
  }

  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
  // 关闭后焦点回到触发它的控件（Radix 的承诺，这里钉死成回归门禁）
  await expect(exportButton).toBeFocused()
})

test('图标按钮都有可访问名（axe button-name / 顶栏抽查）', async ({ app, page }) => {
  const a = await app()
  await page.goto(a.baseURL)
  await expect(page.getByRole('banner')).toBeVisible() // 顶栏（<header>）
  const results = await new AxeBuilder({ page })
    .withRules(['button-name', 'link-name', 'aria-command-name'])
    .analyze()
  expect(results.violations.map((v) => ({
    id: v.id,
    nodes: v.nodes.slice(0, 8).map((n) => n.target.join(' ')),
  }))).toEqual([])
})

test('键盘可达性底线：Tab 能进入界面且焦点可见', async ({ app, page }) => {
  const a = await app({ noProject: true })
  await page.goto(a.baseURL)
  await expect(page.getByRole('main')).toBeVisible()
  await page.keyboard.press('Tab')
  const focused = await page.evaluate(() => {
    const el = document.activeElement as HTMLElement | null
    if (!el || el === document.body) return null
    const r = el.getBoundingClientRect()
    return { tag: el.tagName, visible: r.width > 0 && r.height > 0 }
  })
  expect(focused, 'Tab 之后焦点仍在 body 上——键盘用户进不了界面').not.toBeNull()
  expect(focused!.visible).toBe(true)
})
