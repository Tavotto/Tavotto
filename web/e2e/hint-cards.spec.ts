import { addSecondFigure, expect, ONBOARDING_FLOW_VERSION, test } from './fixtures'
import type { Page } from '@playwright/test'

/**
 * 情景提示动画卡（ADR 0177）：看到笨办法才出、自己用过就收起并不再提。
 *
 * 只放 jsdom 量不到的：真手势（拖边 / 平移 / 挪对象）有没有把卡招出来、卡在窗口右下角且不挤任何东西、
 * 小动画真的挂上并在动（舞台 + 光标）、按钮与 × 的出口。判据逻辑在 `lib/onboarding/hintCards.test.ts`。
 * 定位认 `data-hint-card` / `data-hint-card-stage` / `data-hint-card-action` / `data-hint-card-close`。
 */

// 老手态、一句话提示都看过（它们与卡片共用一个槽位），卡片的账是空的
const FRESH_CARDS = JSON.stringify({
  // 用过好几天了：第二层（修饰键 / 微移）的卡也解锁
  activeDays: 5,
  lastActiveDay: 'e2e',
  schemaVersion: 1,
  flowVersion: ONBOARDING_FLOW_VERSION,
  status: 'completed',
  welcomeSeenAt: 1,
  hintSeen: {
    panel_editable: 1,
    panel_layout_only: 1,
    fast_edit_entered: 1,
    multi_select: 1,
    problem_found: 1,
    layout_page_bounds: 1,
  },
})

async function toLayout(page: Page, baseURL: string, learned: string[] = []) {
  // fixtures 的默认状态先写（把卡片都记成学会了），这里再盖掉；只在这一格还是默认值时盖，刷新后保留应用自己写的账
  await page.addInitScript((blob) => {
    const cur = window.localStorage.getItem('tavotto.onboarding')
    if (cur === null || cur.includes('"not_started"')) window.localStorage.setItem('tavotto.onboarding', blob)
  }, JSON.stringify({ ...JSON.parse(FRESH_CARDS), hintStats: Object.fromEntries(learned.map((k) => [k, { shows: 0, lastAt: 0, done: true }])) }))
  await page.goto(baseURL)
  const card = page.locator('[data-card="Fig1_kinetics.pdf"]')
  await expect(card).toHaveCount(1, { timeout: 30_000 })
  await card.dblclick()
  await expect(page.locator('[data-figure-edge]')).toHaveCount(4, { timeout: 90_000 })
  await addSecondFigure(page)
  await expect(page.locator('[data-view-tweening]')).toHaveCount(0)
  const first = page.locator('[data-object-id]').first()
  await first.click()
  await expect(page.locator('[data-handle="se"]')).toHaveCount(1)
  return first
}

async function dragHandle(page: Page, dir: string, dx: number, alt = false) {
  const b = (await page.locator(`[data-handle="${dir}"]`).boundingBox())!
  const x = b.x + b.width / 2
  const y = b.y + b.height / 2
  await page.mouse.move(x, y)
  await page.mouse.down()
  if (alt) await page.keyboard.down('Alt')
  await page.mouse.move(x + dx, y, { steps: 8 })
  await page.mouse.up()
  if (alt) await page.keyboard.up('Alt')
}

/** 卡片在窗口右下角、小动画挂上了（舞台与光标都在） */
async function expectCard(page: Page, kind: string) {
  const card = page.locator(`[data-hint-card="${kind}"]`)
  await expect(card).toBeVisible()
  await expect(card.locator('.ss-stage')).toHaveCount(1, { timeout: 15_000 })
  await expect(card.locator('.ss-cursor')).toHaveCount(1)
  const box = (await card.boundingBox())!
  const vp = page.viewportSize()!
  expect(box.width).toBeGreaterThan(280)
  expect(box.width).toBeLessThanOrEqual(321)
  // 窗口右下角（与反馈卡同一个角）：不盖用户正在摆弄的画布中部
  expect(vp.width - (box.x + box.width)).toBeLessThan(24)
  expect(vp.height - (box.y + box.height)).toBeLessThan(24)
  return card
}

test(
  '拖边改了图幅出「按住 ⌥ 裁剪」卡，按住 ⌥ 裁剪一次就收起；画布拖出窗口出「双击空白」卡，点「适应画布」回正',
  { tag: '@feature:onboarding.hint-cards' },
  async ({ app, page }, testInfo) => {
    const a = await app()
    await toLayout(page, a.baseURL)
    await expect(page.locator('[data-hint-card]')).toHaveCount(0)

    // 笨办法：拖边改图幅 → 教「按住 ⌥ 是裁剪」
    await dragHandle(page, 'e', -60)
    const edge = await expectCard(page, 'edge_crop')
    await expect(edge.locator('[data-hint-card-text]')).toContainText('裁剪')
    await expect(page.locator('[data-figure-pending]')).toHaveCount(0, { timeout: 60_000 })
    await page.screenshot({ path: testInfo.outputPath('hint-edge-crop.png') })
    // 自己按住 ⌥ 裁剪了一次 = 学会：卡当场收起
    await dragHandle(page, 'e', -30, true)
    await expect(page.locator('[data-hint-card]')).toHaveCount(0)

    // 笨办法：把画布拖出窗口（空格 + 拖动），停手两秒 → 教「双击空白处回正」
    const world = page.locator('[data-world-transform]')
    const before = await world.evaluate((n) => (n as HTMLElement).style.transform)
    const vp = page.viewportSize()!
    await page.mouse.move(vp.width / 2, vp.height / 2)
    await page.keyboard.down('Space')
    await page.mouse.down()
    await page.mouse.move(vp.width / 2 + 1400, vp.height / 2 + 900, { steps: 10 })
    await page.mouse.up()
    await page.keyboard.up('Space')
    const fit = await expectCard(page, 'fit_canvas')
    await page.screenshot({ path: testInfo.outputPath('hint-fit-canvas.png') })
    await fit.locator('[data-hint-card-action]').click()
    await expect(page.locator('[data-hint-card]')).toHaveCount(0)
    await expect(page.locator('[data-view-tweening]')).toHaveCount(0)
    await expect.poll(() => world.evaluate((n) => (n as HTMLElement).style.transform)).not.toContain('1400')
    expect(await world.evaluate((n) => (n as HTMLElement).style.transform)).not.toBe('')
    expect(before).not.toBe('')
  },
)

test(
  '连续挪三次对象出「从标尺拖出对齐线」卡；点 × 之后不再出',
  { tag: '@feature:onboarding.hint-cards' },
  async ({ app, page }, testInfo) => {
    const a = await app()
    const first = await toLayout(page, a.baseURL)
    const nudge = async (dx: number) => {
      const b = (await first.boundingBox())!
      await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2)
      await page.mouse.down()
      await page.mouse.move(b.x + b.width / 2 + dx, b.y + b.height / 2 + 6, { steps: 5 })
      await page.mouse.up()
    }
    await nudge(14)
    await nudge(-9)
    await expect(page.locator('[data-hint-card]')).toHaveCount(0)
    await nudge(7)
    const card = await expectCard(page, 'ruler_guides')
    await page.screenshot({ path: testInfo.outputPath('hint-ruler-guides.png') })
    await card.locator('[data-hint-card-close]').click()
    await expect(page.locator('[data-hint-card]')).toHaveCount(0)
    // × = 别再提：再挪三次也不出
    await nudge(12)
    await nudge(-8)
    await nudge(5)
    await page.waitForTimeout(600)
    await expect(page.locator('[data-hint-card]')).toHaveCount(0)
  },
)

test(
  '手拖了一条直线出「按住 ⇧」卡，按住 ⇧ 拖一次就收起；一点一点挪出「方向键微移」或「暂时不吸附」卡',
  { tag: '@feature:onboarding.hint-cards' },
  async ({ app, page }, testInfo) => {
    const a = await app()
    // 对齐线那张（第一层）记成学会了：这条只看第二层
    const first = await toLayout(page, a.baseURL, ['ruler_guides'])
    const drag = async (dx: number, dy: number, shift = false) => {
      const b = (await first.boundingBox())!
      const x = b.x + b.width / 2
      const y = b.y + b.height / 2
      await page.mouse.move(x, y)
      await page.mouse.down()
      if (shift) await page.keyboard.down('Shift')
      await page.mouse.move(x + dx, y + dy, { steps: 6 })
      await page.mouse.up()
      if (shift) await page.keyboard.up('Shift')
    }
    // 笨办法：小心翼翼地横着拖了一长段
    await drag(90, 0)
    const axis = await expectCard(page, 'shift_axis')
    await expect(axis.locator('[data-hint-card-text]')).toContainText('⇧')
    await page.screenshot({ path: testInfo.outputPath('hint-shift-axis.png') })
    // 自己按住 ⇧ 拖了一次 = 学会
    await drag(-60, 8, true)
    await expect(page.locator('[data-hint-card]')).toHaveCount(0)

    // 笨办法：一点一点挪（每次两三个像素）。吸上过就先教「暂时不吸附」，否则教「方向键微移」——两张都对
    await drag(3, 0)
    await drag(0, 3)
    await drag(-3, 0)
    const card = page.locator('[data-hint-card]')
    await expect(card).toBeVisible()
    expect(['arrow_nudge', 'no_snap']).toContain(await card.getAttribute('data-hint-card'))
    await expect(card.locator('.ss-stage')).toHaveCount(1, { timeout: 15_000 })
    await page.screenshot({ path: testInfo.outputPath('hint-tiny-moves.png') })
  },
)
