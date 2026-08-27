/**
 * 自动化可访问性门禁（审计 P1-09）。
 *
 * 三层：axe 扫描（critical/serious 必须为 0——这是发布门禁，不是建议）、
 * 对话框焦点纪律（trap + Escape 关闭后焦点恢复）、键盘可达性底线。
 *
 * axe 那一层**不只看 violations**（issue #130）：`incomplete` 是「axe 查不了」，
 * 把它当通过就是把「没查到」和「查不了」混成一件事。这里两条一起守——每一类
 * 「查不了」必须在 `INCOMPLETE_COVERED_ELSEWHERE` 里有去处，而对比度另有一条
 * 不依赖 axe 的自算判据。
 * 完整的「纯键盘走完核心流程」在 issue #37 里继续扩：这里守住的是
 * 「不倒退」的底线，先有门禁再逐步抬高。
 *
 * **语言无关写法**：本 spec 同时跑在 zh-CN（chromium / webkit）与 en-US
 * （chromium-en）三个 project 下，选择器一律 role + 双语正则，不写死文案。
 */
import AxeBuilder from '@axe-core/playwright'
import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'
import { lowContrastNodes } from './contrast'

/**
 * **axe 的 `incomplete` 不是「通过」，是「axe 查不了」**（issue #130）。
 *
 * 只断言 `results.violations` 的门禁把两件事混成了一件：「查过了，没问题」和
 * 「根本没查」。实证：我们「整行可点」的写法（`absolute inset-0` 的按钮盖在行
 * 内容上）让 axe 算不出背景色，于是 color-contrast 整片进 `incomplete`；把
 * `--color-warn` 改成明显不达标的 `#e8c98f`，只看 violations 的检查照样绿。
 *
 * 这张表是**逐条定性**的结果：每个 id 都要写明「axe 查不了，但这件事由谁覆盖」。
 * 不在表里的 incomplete 会让用例红——新出现一类「查不了」不许再静默通过。
 */
const INCOMPLETE_COVERED_ELSEWHERE: Record<string, string> = {
  // 被覆盖层遮住的文字 axe 算不出背景色；由本文件的自算对比度断言逐节点覆盖
  'color-contrast': 'lowContrastNodes()（本文件每条用例都跑）',
  // 模态对话框打开时背景整片 aria-hidden，axe 判不出它同时也进不去焦点；
  // 「焦点确实困在对话框里」由「导出对话框」那条用例的 focus trap 断言覆盖
  'aria-hidden-focus': '导出对话框用例的 focus trap + Escape 后焦点恢复断言',
}

interface AxeReport {
  violations: unknown[]
  unexplainedIncomplete: { id: string; impact: string | null | undefined; n: number }[]
}

/** critical / serious 违规 + **未被解释的 incomplete**。
 *  扫描前把动效关掉：对话框/抽屉的进出场动画进行到一半时，axe 对
 *  颜色对比的取样会撞上过渡态，产出不可复现的假阳性；应用本来就支持
 *  prefers-reduced-motion，这也是它的一次真实行使。 */
async function axeReport(page: Page): Promise<AxeReport> {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  const results = await new AxeBuilder({ page }).analyze()
  return {
    violations: results.violations
      .filter((v) => v.impact === 'critical' || v.impact === 'serious')
      .map((v) => ({
        id: v.id,
        impact: v.impact,
        nodes: v.nodes.slice(0, 5).map((n) => ({
          target: n.target.join(' '),
          why: n.failureSummary?.split('\n').slice(0, 3).join(' '),
        })),
      })),
    unexplainedIncomplete: results.incomplete
      .filter((v) => !(v.id in INCOMPLETE_COVERED_ELSEWHERE))
      .map((v) => ({ id: v.id, impact: v.impact, n: v.nodes.length })),
  }
}

/**
 * 一处界面的完整可访问性判据：
 *   ① axe 没有 critical/serious **违规**；
 *   ② axe 说「查不了」的，每一类都在 `INCOMPLETE_COVERED_ELSEWHERE` 里有去处；
 *   ③ **自算对比度**没有不达标的文字——这一条不依赖 axe 能不能算出背景色。
 */
async function expectAccessible(page: Page, root = 'body') {
  const report = await axeReport(page)
  expect(report.violations).toEqual([])
  expect(
    report.unexplainedIncomplete,
    'axe 报了一类新的「查不了」——先定性它由谁覆盖，再加进 INCOMPLETE_COVERED_ELSEWHERE',
  ).toEqual([])
  expect(
    await lowContrastNodes(page, root),
    '自算 WCAG 对比度不达标（axe 可能因为覆盖层根本没测到这些节点）',
  ).toEqual([])
}

test('项目选择器：axe 无违规、无未定性的「查不了」、自算对比度达标', async ({ app, page }) => {
  const a = await app({ noProject: true })
  await page.goto(a.baseURL)
  await expect(page.getByRole('main')).toBeVisible()
  await expectAccessible(page)
})

test('工作台（项目已开、画布有面板）：axe 无违规、无未定性的「查不了」、自算对比度达标', async ({
  app,
  page,
}) => {
  const a = await app()
  await page.goto(a.baseURL)
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  // 等面板真的渲染出来再扫，扫到一半加载的骨架屏没有意义
  await expect(page.locator('[data-canvas-stage] img, [data-canvas-stage] svg').first())
    .toBeVisible({ timeout: 60_000 })
  await expectAccessible(page)
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

  await expectAccessible(page)

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
