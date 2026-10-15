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
  test(`新手版 ${width}px：拖放区与两个主动作都在、没有三步说明卡，不横向溢出`, async ({ app, page }) => {
    await page.setViewportSize({ width, height: 800 })
    const a = await app({ noProject: true })
    await page.goto(a.baseURL)
    const main = page.locator('main[data-home-variant="newcomer"]')
    await expect(main).toBeVisible()
    await expect(main.locator('ol > li')).toHaveCount(0)
    await expect(main.locator('[data-home-dropzone]')).toBeVisible()
    await expect(page.getByRole('button', { name: '用示例学一遍（带引导）' })).toBeVisible()
    await expect(page.getByRole('button', { name: '导入我的脚本' })).toBeVisible()
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

/**
 * 首页最近项目卡的克制浮起（`<Card lift>`，宪法第二十五节登记豁免）：真浏览器里量——
 * hover / 键盘聚焦 = translateY 恰为 -1px（整数像素）+ 阴影升档，按下回落到 0，
 * 兄弟卡与自己的布局盒一动不动（transform 不影响布局，这里量出来而不是信），
 * reduced-motion 下不位移只换阴影，切换项目中（置灰）不浮起。
 */
test('最近项目卡 lift：hover/聚焦 -1px + 阴影升档、按下回落、兄弟零位移、reduced-motion 只换阴影、置灰不浮起', async ({
  app,
  page,
  browserName,
}) => {
  const now = Date.now()
  const recent = ['kinetics', 'poster', 'thesis-ch3'].map((n, i) => ({
    path: `/Users/me/Documents/${n}`,
    name: n,
    last_opened: now - 3600_000 * (i + 1),
    exists: true,
    current: false,
  }))
  await page.route('**/api/projects/recent', (r) => r.fulfill({ json: { recent, pinned: [] } }))
  let hold = false
  await page.route('**/api/projects/open', async (r) => {
    if (!hold) await r.continue() // hold 之后永不应答：switching 一直亮着
  })
  await page.setViewportSize({ width: 1200, height: 800 })
  const a = await app({ noProject: true })
  await page.goto(a.baseURL)
  const cards = page.locator('[data-home-recent] [data-card]')
  await expect(cards).toHaveCount(3)
  await page.mouse.move(2, 2)

  const state = (i: number) =>
    cards.nth(i).evaluate((el) => {
      const cs = getComputedStyle(el)
      return { translate: cs.translate, shadow: cs.boxShadow }
    })
  /** 每张卡自己所在 li 的布局盒（transform 不影响）与卡本身的 boundingBox（含 transform） */
  const layout = () =>
    page.evaluate(() =>
      [...document.querySelectorAll<HTMLElement>('[data-home-recent]')].map((li) => {
        const r = li.getBoundingClientRect()
        const c = li.querySelector<HTMLElement>('[data-card]')!.getBoundingClientRect()
        return { li: [r.x, r.y, r.width, r.height], card: [c.x, c.y, c.width, c.height] }
      }),
    )
  const rest = await layout()
  const restState = await state(1)
  expect(restState.translate).toBe('none')

  // ---- hover：恰好 -1px，阴影升档，其余卡与自己的 li 不动
  await cards.nth(1).locator('button').first().hover()
  await expect.poll(async () => (await state(1)).translate).toBe('0px -1px')
  const hov = await state(1)
  expect(hov.shadow, '阴影应当升一档').not.toBe(restState.shadow)
  const lifted = await layout()
  rest.forEach((r, i) => {
    expect(lifted[i].li, `li ${i} 的布局盒`).toEqual(r.li)
    if (i === 1) expect(lifted[i].card[1]).toBe(r.card[1] - 1) // 整数像素：恰好上移 1
    else expect(lifted[i].card, `兄弟卡 ${i}`).toEqual(r.card)
  })

  // ---- 按下回落到 0
  await page.mouse.down()
  await expect.poll(async () => (await state(1)).translate).toMatch(/^(0px|0px 0px|none)$/)
  await page.mouse.move(2, 2)
  await page.mouse.up()
  await expect.poll(async () => (await state(1)).translate).toBe('none')

  // ---- 键盘聚焦同样浮起（WebKit：程序化聚焦不算 :focus-visible、Tab 又不停在按钮上，这一步只在 chromium 量）
  if (browserName !== 'webkit') {
    // 键盘模态：先按一下键盘，再聚焦——WebKit 的 Tab 默认不停在按钮上（需 Option+Tab），这里不赌 Tab 的落点
    await page.keyboard.press('Shift')
    await cards.nth(2).locator('button').first().focus()
    await expect(cards.nth(2).locator('button').first()).toBeFocused()
    await expect.poll(async () => (await state(2)).translate).toBe('0px -1px')
    await cards.nth(2).locator('button').first().blur()
  }

  // ---- reduced-motion：不位移，阴影照样升档
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await cards.nth(0).locator('button').first().hover()
  await expect.poll(async () => (await state(0)).shadow).not.toBe(restState.shadow)
  expect((await state(0)).translate).toBe('none')
  await page.emulateMedia({ reducedMotion: 'no-preference' })

  // ---- 切换中（置灰）：hover 不浮起
  hold = true
  await cards.nth(0).locator('button').first().click()
  await expect(cards.nth(0)).toHaveAttribute('data-disabled', 'true')
  await cards.nth(1).locator('button').first().hover({ force: true })
  await page.waitForTimeout(400)
  expect((await state(1)).translate).toBe('none')
  expect((await state(1)).shadow).toBe(restState.shadow)
})
