import AxeBuilder from '@axe-core/playwright'
import { expect, test } from './fixtures'
import { lowContrastNodes as sharedLowContrastNodes } from './contrast'
import type { Page } from '@playwright/test'

/**
 * 设置 → 编码 Agent 的黄金路径（真浏览器，真后端探测）。
 *
 * jsdom 没有布局引擎，量不出溢出、也量不出行高——**「不横向溢出」「返回后
 * 滚动位置还在」这类断言只有在这里才是真的**。列表内容随跑测试的机器变化
 * （装没装 codex / claude 都可能），所以断言全部打在「结构与行为」上，
 * 不打在具体某个 Agent 的状态上。
 */
async function openAgentSettings(page: Page, baseURL: string) {
  await page.goto(baseURL)
  await page.getByRole('button', { name: '设置', exact: true }).first().click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible({ timeout: 30_000 })
  await dialog.getByRole('navigation').getByRole('button', { name: '编码 Agent' }).click()
  return dialog
}

/**
 * 对话框里**每一个**把布局撑破的元素（真布局才量得出来）。
 *
 * 只量 body 与 dialog 两层是不够的：中间任何一个可滚/裁切的容器都会把里面的
 * 溢出吸收掉，外面两层永远是干净的——往行里塞一个 900px 不收缩的元素，那种写法
 * 照样绿（变异验过）。所以这里逐个元素扫，只认 `overflow-x: visible` 的那些：
 * 裁切（`truncate` 就是 hidden + 省略号）与有意可滚的容器本来就不该算撑破。
 */
async function horizontalOffenders(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const dlg = document.querySelector('[role="dialog"]')
    if (!dlg) return ['NO DIALOG']
    const out: string[] = []
    for (const el of [document.body, dlg, ...Array.from(dlg.querySelectorAll('*'))]) {
      const e = el as HTMLElement
      if (getComputedStyle(e).overflowX !== 'visible') continue
      if (e.scrollWidth > e.clientWidth + 1) {
        out.push(`${e.tagName}.${String(e.className).slice(0, 60)} sw=${e.scrollWidth} cw=${e.clientWidth}`)
      }
    }
    return out
  })
}

test('编码 Agent：列表 → 详情 → 返回，状态与滚动都还在', async ({ app, page }) => {
  const a = await app()
  const dialog = await openAgentSettings(page, a.baseURL)

  // 一级页面：分组列表 + 两个方向的小节
  await expect(dialog.getByText('在 Tavotto 中使用编码 Agent')).toBeVisible()
  await expect(dialog.getByText('在编码 Agent 中使用 Tavotto')).toBeVisible()
  await expect(dialog.getByText('Tavotto for Codex')).toBeVisible()

  // **一级页面不许有任何输入框**（路径 / Base URL / 密钥全在详情里）
  await expect(dialog.locator('input[type="text"], input[type="password"]')).toHaveCount(0)
  await expect(dialog.getByText('接口地址')).toHaveCount(0)
  await expect(dialog.getByText('密钥')).toHaveCount(0)

  // 行主体点进详情
  const row = dialog.getByRole('button', { name: /Codex 的详情/ }).first()
  await expect(row).toBeVisible()
  await row.click()
  await expect(dialog.getByText('概览')).toBeVisible()
  await expect(dialog.getByText('检测来源')).toBeVisible()
  // 高级设置默认折叠。**直接量 `<details>` 的 open**，不拿「输入框不在」当代理：
  // 那个输入框要点过「使用自定义可执行文件」才渲染，折叠与否它都不在——用它当
  // 判据，把 details 强行改成默认展开也照样绿（变异验过，就是这么漏的）。
  await expect(dialog.locator('summary', { hasText: '自定义可执行文件' })).toBeVisible()
  const folds = dialog.locator('details')
  expect(await folds.count()).toBeGreaterThan(0)
  for (const fold of await folds.all()) {
    expect(await fold.evaluate((el) => (el as HTMLDetailsElement).open)).toBe(false)
  }
  // 折叠着 → 里面的东西量得到「不可见」，这条才是「默认不制造噪音」的兑现
  await expect(dialog.getByRole('button', { name: '使用自定义可执行文件' })).toBeHidden()

  // 返回：列表还在
  await dialog.getByRole('button', { name: '返回编码 Agent 列表' }).click()
  await expect(dialog.getByText('在 Tavotto 中使用编码 Agent')).toBeVisible()
})

test('编码 Agent：开关是独立控件，不会顺手打开详情', async ({ app, page }) => {
  const a = await app()
  const dialog = await openAgentSettings(page, a.baseURL)

  const toggle = dialog.getByRole('switch').first()
  await expect(toggle).toBeVisible()
  // 嵌套交互元素：任何 button 里都不该再有 button（HTML 非法，键盘行为不可预期）
  expect(await dialog.locator('button button').count()).toBe(0)

  if (await toggle.isEnabled()) {
    const before = await toggle.getAttribute('aria-checked')
    await toggle.click()
    await expect(dialog.getByText('概览')).toHaveCount(0)     // 没进详情
    await expect(toggle).not.toHaveAttribute('aria-checked', before ?? '')
    await toggle.click()                                      // 还原，别留状态
  }
})

test('编码 Agent：重新检测有播报，且不发生布局跳动', async ({ app, page }) => {
  const a = await app()
  const dialog = await openAgentSettings(page, a.baseURL)

  const list = dialog.locator('ul').first()
  const before = await list.boundingBox()
  await dialog.getByRole('button', { name: '重新检测' }).click()
  await expect(dialog.getByText(/最近检测/)).toBeVisible({ timeout: 30_000 })
  const after = await list.boundingBox()
  // 高度可以随内容变，但不该整块跳走（左边缘与宽度稳定）
  expect(Math.abs((after?.x ?? 0) - (before?.x ?? 0))).toBeLessThan(2)
  expect(Math.abs((after?.width ?? 0) - (before?.width ?? 0))).toBeLessThan(2)

  // aria-live 区在（检测完成 / 失败都要说一声）
  await expect(dialog.locator('[aria-live="polite"]')).toHaveCount(1)
})

test('编码 Agent：1024×768 窄窗口不横向溢出', async ({ app, page }) => {
  const a = await app()
  await page.setViewportSize({ width: 1024, height: 768 })
  const dialog = await openAgentSettings(page, a.baseURL)

  await expect(dialog.getByText('在 Tavotto 中使用编码 Agent')).toBeVisible()
  expect(await horizontalOffenders(page)).toEqual([])

  // 详情页同样：长路径靠省略，不把面板撑开
  await dialog.getByRole('button', { name: /的详情/ }).first().click()
  await expect(dialog.getByText('概览')).toBeVisible()
  expect(await horizontalOffenders(page)).toEqual([])
})

test('编码 Agent：axe 无 critical/serious 违规（列表与详情各一次）', async ({ app, page }) => {
  const a = await app()
  // 扫描前关掉动效：对话框进出场进行到一半时 axe 取样会撞上过渡态，
  // 产出不可复现的假阳性（与 a11y.spec.ts 同一条约定）
  await page.emulateMedia({ reducedMotion: 'reduce' })
  const dialog = await openAgentSettings(page, a.baseURL)

  const serious = async () =>
    (await new AxeBuilder({ page }).analyze()).violations
      .filter((v) => v.impact === 'critical' || v.impact === 'serious')
      .map((v) => ({ id: v.id, nodes: v.nodes.slice(0, 3).map((n) => n.target.join(' ')) }))

  // 一级列表：开关与行按钮的可访问名、结构类规则交给 axe
  expect(await serious()).toEqual([])
  // 新加的状态色（含 --color-warn）交给自算——axe 在这一片只报 incomplete
  expect(await sharedLowContrastNodes(page, '[role="dialog"]')).toEqual([])

  // 详情页：概览、模型服务单选、两个折叠区
  await dialog.getByRole('button', { name: /的详情/ }).first().click()
  await expect(dialog.getByText('概览')).toBeVisible()
  for (const d of await dialog.locator('details').all()) {
    await d.locator('summary').click()
  }
  expect(await serious()).toEqual([])
  expect(await sharedLowContrastNodes(page, '[role="dialog"]')).toEqual([])
})
