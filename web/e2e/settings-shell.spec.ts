import AxeBuilder from '@axe-core/playwright'
import { expect, test } from './fixtures'
import { horizontalOffenders } from './overflow'
import type { Page } from '@playwright/test'

/**
 * 设置外壳（ADR 0038）——只有真布局才量得出来的那几件：
 *
 *   * 切遍每个分区，对话框外框的 x / y / 宽 / 高一个像素都不动；
 *   * 内容区自己滚：对话框本体的 scrollHeight 不随内容增长；
 *   * 1024×640 与 150% 缩放（deviceScaleFactor 2 + 窄视口）下不横向溢出、整个外框在视口内；
 *   * 英文界面同样不溢出（英文更长）；
 *   * axe 无 critical / serious。
 */
const SECTION_LABELS = ['常规', '界面', '项目', '样式', '规范', '导出', '编码 Agent', '包管理', '诊断', '更新', '关于与隐私']

async function openSettings(page: Page, baseURL: string) {
  await page.goto(baseURL)
  // <1024 时左栏是覆盖式抽屉，首屏开着、遮罩盖住了设置按钮（遮罩自身的淡入
  // 动画让 Playwright 一直判它"不稳定"）。设置对话框是 z-50 的 portal，在抽屉之上，
  // 所以这里绕过指针拦截直接派发 click——测的是对话框，不是抽屉
  const settings = page.getByRole('button', { name: '设置', exact: true }).first()
  if (await page.getByRole('button', { name: '收起侧栏' }).count()) {
    await settings.dispatchEvent('click')
  } else {
    await settings.click()
  }
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible({ timeout: 30_000 })
  // 进场动画（pop-in 缩放）跑完再量：动画中的 boundingBox 是缩过的
  await dialog.evaluate((el) => Promise.all(el.getAnimations().map((a) => a.finished)))
  return dialog
}

test('设置：切遍每个分区，外框不跳、内容区自己滚', async ({ app, page }) => {
  const a = await app()
  const dialog = await openSettings(page, a.baseURL)
  const nav = dialog.getByRole('navigation')
  const box0 = (await dialog.boundingBox())!
  for (const label of SECTION_LABELS) {
    await nav.getByRole('button', { name: label, exact: true }).click()
    // 分区里有异步加载（Agent 探测 / 包清单），等一拍再量
    await page.waitForTimeout(150)
    const box = (await dialog.boundingBox())!
    expect(box, label).toEqual(box0)
    // 对话框本体不滚（滚的是 data-settings-content）
    const scrolls = await dialog.evaluate((el) => el.scrollHeight - el.clientHeight)
    expect(scrolls, `${label} 让对话框本体长出了滚动`).toBeLessThanOrEqual(1)
    expect(await horizontalOffenders(page, '[role="dialog"]'), label).toEqual([])
  }
})

test('设置：1024×640 小窗口整个外框在视口内且不横向溢出', async ({ app, page }) => {
  const a = await app()
  await page.setViewportSize({ width: 1024, height: 640 })
  const dialog = await openSettings(page, a.baseURL)
  const box = (await dialog.boundingBox())!
  expect(box.x).toBeGreaterThanOrEqual(0)
  expect(box.y).toBeGreaterThanOrEqual(0)
  expect(box.x + box.width).toBeLessThanOrEqual(1024)
  expect(box.y + box.height).toBeLessThanOrEqual(640)
  for (const label of ['包管理', '编码 Agent', '诊断']) {
    await dialog.getByRole('navigation').getByRole('button', { name: label, exact: true }).click()
    await page.waitForTimeout(150)
    expect(await horizontalOffenders(page, '[role="dialog"]'), label).toEqual([])
  }
})

test('设置：窄窗口（<640 CSS px，等价于高缩放）导航变成顶部一条、仍可切页', async ({ app, page }) => {
  const a = await app()
  await page.setViewportSize({ width: 600, height: 700 })
  const dialog = await openSettings(page, a.baseURL)
  const nav = dialog.getByRole('navigation')
  const navBox = (await nav.boundingBox())!
  const contentBox = (await dialog.locator('[data-settings-content]').boundingBox())!
  expect(navBox.y + navBox.height).toBeLessThanOrEqual(contentBox.y + 1) // 导航在内容上方
  await nav.getByRole('button', { name: '包管理', exact: true }).click()
  await expect(dialog.getByText('内置包')).toBeVisible()
  const box = (await dialog.boundingBox())!
  expect(box.x + box.width).toBeLessThanOrEqual(600)
  expect(await horizontalOffenders(page, '[role="dialog"]')).toEqual([])
})

test('设置：英文界面同样不溢出', async ({ app, page }) => {
  const a = await app()
  await page.goto(a.baseURL)
  await page.evaluate(() => localStorage.setItem('tavotto.locale', 'en-US'))
  await page.reload()
  await page.getByRole('button', { name: 'Settings', exact: true }).first().click()
  const dialog = page.getByRole('dialog', { name: 'Settings' })
  await expect(dialog).toBeVisible({ timeout: 30_000 })
  for (const label of ['Packages', 'Coding Agents', 'Diagnostics', 'Specs']) {
    await dialog.getByRole('navigation').getByRole('button', { name: label, exact: true }).click()
    await page.waitForTimeout(150)
    expect(await horizontalOffenders(page, '[role="dialog"]'), label).toEqual([])
  }
})

test('设置：方向键在导航里走，Enter 不需要——落地即切页', async ({ app, page }) => {
  const a = await app()
  const dialog = await openSettings(page, a.baseURL)
  const nav = dialog.getByRole('navigation')
  await nav.getByRole('button', { name: '常规', exact: true }).focus()
  await page.keyboard.press('ArrowDown')
  await expect(nav.getByRole('button', { name: '界面', exact: true })).toHaveAttribute('aria-current', 'true')
  await expect(nav.getByRole('button', { name: '界面', exact: true })).toBeFocused()
  await page.keyboard.press('End')
  await expect(nav.getByRole('button', { name: '关于与隐私', exact: true })).toHaveAttribute('aria-current', 'true')
})

/**
 * axe 覆盖的分区清单。
 *
 * 「更新」「关于与隐私」是 2026-09-06 补进来的：它们此前从没被 axe 跑过，而
 * 关于页正好挂着一条 serious（句子里的链接只靠颜色区分）——**没被跑过的门禁
 * 不会保持正确，它只是没说话**。剩下的分区留给后续，别把这条用例拉成十一页
 * 串行的慢用例。
 */
const AXE_SECTIONS = ['包管理', '诊断', '编码 Agent', '更新', '关于与隐私']

test('设置：五个分区 axe 无 critical/serious', async ({ app, page }) => {
  const a = await app()
  const dialog = await openSettings(page, a.baseURL)
  for (const label of AXE_SECTIONS) {
    await dialog.getByRole('navigation').getByRole('button', { name: label, exact: true }).click()
    await page.waitForTimeout(300)
    const results = await new AxeBuilder({ page }).include('[role="dialog"]').analyze()
    const bad = results.violations.filter((v) => v.impact === 'critical' || v.impact === 'serious')
    expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`), label).toEqual([])
  }
})
