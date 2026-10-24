import type { Page } from '@playwright/test'
import { expect, test, waitForFigureEdit, type RunningApp } from './fixtures'

/**
 * 改图助手输入框（2026-10-16）：推理强度是底栏胶囊 + 带说明的菜单；输入框空态即完整高度。
 *
 * 能力探测（本机装没装 codex/claude）不能决定这条用例红绿：capabilities 在浏览器侧被换成一个固定的
 * codex（agents 与 connections 两份都换）（五档，默认 medium）。语言无关写法，chromium（zh-CN）与 chromium-en 各走一遍。
 */

const EFFORTS = ['minimal', 'low', 'medium', 'high', 'max']

async function openAssistant(page: Page, a: RunningApp) {
  await page.route('**/api/ai/capabilities*', async (route) => {
    const real = await (await route.fetch()).json()
    const base = real.agents?.[0] ?? {}
    const agent = {
      ...base,
      id: 'codex',
      display_name: 'Codex',
      icon_key: 'codex',
      state: 'ready',
      installed: true,
      enabled: true,
      usable: true,
      models: ['gpt-5.6-sol'],
      default_model: 'gpt-5.6-sol',
      efforts: EFFORTS,
      default_effort: 'medium',
      features: { ...(base.features ?? {}), model_selection: true, effort_selection: true },
    }
    // 真后端现在下发 vendors / connections：界面走「连接」视图，只换 agents 会在没装 CLI 的机器上看到「还没连接 AI」
    const connection = {
      id: 'chatgpt',
      vendor: 'chatgpt',
      label: 'ChatGPT',
      logo: 'chatgpt',
      state: 'ready',
      route: 'cli:codex',
      models: agent.models,
      default_model: agent.default_model,
      efforts: EFFORTS,
      default_effort: 'medium',
    }
    await route.fulfill({ json: { ...real, agents: [agent], connections: [connection] } })
  })
  await page.goto(a.baseURL)
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  await waitForFigureEdit(page)
  await page.getByRole('tab', { name: /改图助手|Assistant/i }).first().click()
  await expect(page.locator('[data-ai-composer]')).toBeVisible({ timeout: 30_000 })
  await expect(page.locator('[data-ai-pill="effort"]')).toBeVisible({ timeout: 30_000 })
}

const composerBox = async (page: Page) => (await page.locator('[data-ai-composer]').boundingBox())!

test('输入框：空态就是完整高度，打字前后高度与位置不变，控件行常驻', async ({ app, page }) => {
  await openAssistant(page, await app())
  const empty = await composerBox(page)
  expect(empty.height, '空态应是展开高度（原先是 42px 胶囊）').toBeGreaterThanOrEqual(90)
  await expect(page.locator('[data-ai-pill="model"]')).toBeVisible()
  await expect(page.locator('[data-ai-pill="effort"]')).toBeVisible()

  await page.locator('[data-ai-input]').fill('把图例移到左上角')
  await page.waitForTimeout(400) // 过了所有过渡
  const typed = await composerBox(page)
  expect(Math.abs(typed.height - empty.height)).toBeLessThan(0.5)
  expect(Math.abs(typed.y - empty.y)).toBeLessThan(0.5)
  await expect(page.locator('[data-ai-pill="effort"]')).toBeVisible()
})

test('推理强度：点胶囊 → 点一档，一步完成；弹层在视口内；当前档带 ✓，默认档带小标', async ({ app, page }) => {
  await openAssistant(page, await app())
  const pill = page.locator('[data-ai-pill="effort"]')
  const before = (await pill.textContent())!

  await pill.click()
  const menu = page.getByRole('menu')
  await expect(menu).toBeVisible()
  const rows = menu.getByRole('menuitemradio')
  await expect(rows).toHaveCount(EFFORTS.length)
  await expect(rows.nth(2)).toHaveAttribute('aria-checked', 'true') // 默认 medium
  await expect(menu.locator('[aria-checked="true"]')).toHaveCount(1)

  // 弹层不被右栏 / 视口裁切：量真实盒子
  await page.waitForTimeout(300)
  const box = (await menu.boundingBox())!
  const vp = page.viewportSize()!
  expect(box.x).toBeGreaterThanOrEqual(0)
  expect(box.y).toBeGreaterThanOrEqual(0)
  expect(box.x + box.width).toBeLessThanOrEqual(vp.width)
  expect(box.y + box.height).toBeLessThanOrEqual(vp.height)
  expect(Math.round(box.width)).toBe(248)
  // 每行有第二行说明，且没有文字被横向挤出
  for (let i = 0; i < EFFORTS.length; i++) {
    const r = rows.nth(i)
    expect(((await r.textContent()) ?? '').length).toBeGreaterThan(4)
    expect(await r.evaluate((el) => el.scrollWidth <= el.clientWidth + 1)).toBe(true)
  }

  await rows.nth(3).click()
  await expect(menu).toHaveCount(0)
  await expect(pill).not.toHaveText(before)
  // 再开一次：选中项跟着换了
  await pill.click()
  await expect(page.getByRole('menu').getByRole('menuitemradio').nth(3)).toHaveAttribute('aria-checked', 'true')
})

test('推理强度：键盘 Enter 开、↓ 移动、Enter 选、Esc 关', async ({ app, page }) => {
  await openAssistant(page, await app())
  const pill = page.locator('[data-ai-pill="effort"]')
  await pill.focus()
  await page.keyboard.press('Enter')
  const menu = page.getByRole('menu')
  await expect(menu).toBeVisible()
  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('Enter')
  await expect(menu).toHaveCount(0)
  await pill.click()
  const checked = page.getByRole('menu').locator('[aria-checked="true"]')
  await expect(checked).toHaveCount(1)
  await page.keyboard.press('Escape')
  await expect(page.getByRole('menu')).toHaveCount(0)
})

test('减少动效：菜单入场动画时长被压到 ~0', async ({ app, page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await openAssistant(page, await app())
  await page.locator('[data-ai-pill="effort"]').click()
  const menu = page.getByRole('menu')
  await expect(menu).toBeVisible()
  const dur = await menu.evaluate((el) => parseFloat(getComputedStyle(el).animationDuration))
  expect(dur).toBeLessThan(0.001) // 秒；0.01ms = 0.00001s
})

test('正常动效：向上展开的菜单走 pop-in-up 入场（位移朝上）', async ({ app, page }) => {
  await openAssistant(page, await app())
  await page.locator('[data-ai-pill="effort"]').click()
  const menu = page.getByRole('menu')
  await expect(menu).toBeVisible()
  expect(await menu.getAttribute('data-side')).toBe('top')
  const name = await menu.evaluate((el) => getComputedStyle(el).animationName)
  expect(name).toBe('pop-in-up')
})
