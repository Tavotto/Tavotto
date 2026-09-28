import type { Page } from '@playwright/test'
import { expect, showAllProjects, test } from './fixtures'
import { horizontalOffenders } from './overflow'

/**
 * 主页（没有打开项目时的整屏）：新手版 / 老手版在真浏览器里的版式与三条导入路径。
 *
 * jsdom 量不到的在这里量：窄窗口（~900px）与宽窗口都不横向溢出；拖放是真的
 * `DragEvent` + `DataTransfer`（浏览器不给本机路径 → 退回目录浏览器，并说清是哪个文件）；
 * 老手版的示例按钮只打开示例项目、不重开教程（onboarding 仍是 completed）。
 */

/** 本机 onboarding 那一格：走完了教程 → 老手版（格式见 store/onboardingStore） */
const COMPLETED = JSON.stringify({
  schemaVersion: 1,
  flowVersion: 2,
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

async function asReturningUser(page: Page) {
  await page.addInitScript((blob) => window.localStorage.setItem('tavotto.onboarding', blob), COMPLETED)
}

for (const width of [900, 1440]) {
  test(`新手版 ${width}px：三步卡片与两个主动作都在，不横向溢出`, async ({ app, page }) => {
    await page.setViewportSize({ width, height: 800 })
    const a = await app({ noProject: true })
    await page.goto(a.baseURL)
    const main = page.locator('main[data-home-variant="newcomer"]')
    await expect(main).toBeVisible()
    await expect(main.locator('ol > li')).toHaveCount(3)
    await expect(page.getByRole('button', { name: '用示例体验一次' })).toBeVisible()
    await expect(page.getByRole('button', { name: '导入我的脚本' })).toBeVisible()
    // 资源验过才说「已内置」：源码 / wheel / 桌面包里 resources/tutorial_project 都在
    await expect(page.getByText('已随安装包内置示例脚本')).toBeVisible()
    expect(await horizontalOffenders(page, 'main')).toEqual([])
  })

  test(`老手版 ${width}px：拖放区 + 示例按钮，不横向溢出`, async ({ app, page }) => {
    await asReturningUser(page)
    await page.setViewportSize({ width, height: 800 })
    const a = await app({ noProject: true })
    await page.goto(a.baseURL)
    await expect(page.locator('main[data-home-variant="returning"]')).toBeVisible()
    await expect(page.locator('[data-home-dropzone]')).toBeVisible()
    await expect(page.locator('[data-home-sample]')).toBeVisible()
    expect(await horizontalOffenders(page, 'main')).toEqual([])
  })
}

test('老手版「使用示例脚本试试看」：打开示例项目，不弹引导，onboarding 仍是 completed', async ({ app, page }) => {
  test.setTimeout(120_000)
  await asReturningUser(page)
  await page.setViewportSize({ width: 1400, height: 900 })
  const a = await app({ noProject: true })
  await page.goto(a.baseURL)
  await page.locator('[data-home-sample]').click()
  await expect(page.getByRole('button', { name: '导出', exact: true })).toBeVisible({ timeout: 60_000 })
  await expect(page.locator('[data-card="Fig1_kinetics.pdf"]')).toBeVisible({ timeout: 30_000 })
  await expect(page.locator('[data-onboarding-coachmark]')).toHaveCount(0)
  expect(
    await page.evaluate(() => JSON.parse(localStorage.getItem('tavotto.onboarding') ?? '{}').status),
  ).toBe('completed')
})

test('拖放一个 .py（浏览器不给路径）：说清是哪个文件，退回「选择脚本所在的文件夹」', async ({ app, page }) => {
  await asReturningUser(page)
  const a = await app({ noProject: true })
  await page.goto(a.baseURL)
  const zone = page.locator('[data-home-dropzone]')
  await expect(zone).toBeVisible()
  const dt = await page.evaluateHandle(() => {
    const d = new DataTransfer()
    d.items.add(new File(['import matplotlib\n'], 'figure.py', { type: 'text/x-python' }))
    return d
  })
  await zone.dispatchEvent('dragenter', { dataTransfer: dt })
  await expect(zone).toHaveAttribute('data-dragging', 'true')
  await expect(zone).toContainText('松开即可导入')
  await zone.dispatchEvent('drop', { dataTransfer: dt })
  await expect(page.getByText('「figure.py」')).toBeVisible()
  await expect(page.locator('[data-dialog]')).toContainText('选择脚本所在的文件夹')
  await expect(zone).not.toHaveAttribute('data-dragging', 'true')
})

test('拖放的不是 .py：报错，不开任何窗口', async ({ app, page }) => {
  await asReturningUser(page)
  const a = await app({ noProject: true })
  await page.goto(a.baseURL)
  const dt = await page.evaluateHandle(() => {
    const d = new DataTransfer()
    d.items.add(new File(['%PDF'], 'fig.pdf', { type: 'application/pdf' }))
    return d
  })
  await page.locator('main').dispatchEvent('drop', { dataTransfer: dt })
  await expect(page.getByRole('alert')).toContainText('fig.pdf')
  await expect(page.locator('[data-dialog]')).toHaveCount(0)
})

test('「全部项目」与返回主页', async ({ app, page }) => {
  const a = await app({ noProject: true })
  await page.goto(a.baseURL)
  await showAllProjects(page)
  await expect(page.getByRole('button', { name: '新建项目' })).toBeVisible()
  await expect(page.locator('[data-onboarding-anchor="tutorial-entry"]')).toHaveCount(1)
  await page.locator('[data-home-back]').click()
  await expect(page.locator('main[data-home-variant="newcomer"]')).toBeVisible()
})
