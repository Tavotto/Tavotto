import { expect, test } from './fixtures'
import type { Locator, Page } from '@playwright/test'

/**
 * 外观：跟随系统 / 浅色 / 深色（2026-10-07 暗色主题，宪法第二十八节；登记 `settings.appearance`）。
 *
 * 主语是**真浏览器算出来的颜色**（jsdom 不算 CSS）：
 *   - 设置 › 通用 › 外观点「深色」：桌面（body）与工作面板的计算底色真的换成暗色表里的值；
 *     画布上的纸（`data-page-sheet`）仍是白——纸是文档内容，不跟主题走；
 *   - 刷新之后还是深色（偏好写在本机，挂载之前就落到 `<html>` 上）；
 *   - 「跟随系统」：`<html>` 不挂 data-theme，颜色跟着 `prefers-color-scheme` 走（emulateMedia 切两次）。
 * 颜色值从 index.css 两张值表手抄（e2e 不 import src）：浅色桌面 #efefed、暗色桌面 #161615、暗色面板 #222220。
 */
const LIGHT_DESK = 'rgb(239, 239, 237)'
const DARK_DESK = 'rgb(22, 22, 21)'
const DARK_PANEL = 'rgb(34, 34, 32)'
const PAPER = 'rgb(255, 255, 255)'

const bg = (loc: Locator) => loc.evaluate((el) => getComputedStyle(el).backgroundColor)
const theme = (page: Page) => page.evaluate(() => document.documentElement.getAttribute('data-theme'))

async function openGeneralSettings(page: Page) {
  await page.locator('[data-rail="settings"]').click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible({ timeout: 30_000 })
  await dialog.locator('[data-section="general"]').click()
  const group = dialog.locator('[data-testid="settings-theme"]')
  await expect(group).toBeVisible()
  return { dialog, group }
}

test(
  '外观：设置里选「深色」界面当场变暗、纸仍是白、刷新后还是深色；「跟随系统」随 prefers-color-scheme 变',
  { tag: '@feature:settings.appearance' },
  async ({ app, page }) => {
    const a = await app()
    await page.emulateMedia({ colorScheme: 'light' })
    await page.goto(a.baseURL)
    const sheet = page.locator('[data-page-sheet]')
    await expect(sheet).toBeVisible({ timeout: 30_000 })
    const body = page.locator('body')
    const panel = page.locator('[data-work-panel]')
    expect(await bg(body)).toBe(LIGHT_DESK)
    expect(await bg(sheet)).toBe(PAPER)

    // 选「深色」：<html data-theme="dark">，桌面与工作面板的计算底色换成暗色表
    let { dialog, group } = await openGeneralSettings(page)
    const dark = group.locator('[data-value="dark"]')
    await dark.click()
    await expect(dark).toHaveAttribute('aria-checked', 'true')
    expect(await theme(page)).toBe('dark')
    await expect.poll(() => bg(body)).toBe(DARK_DESK)
    // 对话框本体也是暗色表里的面板色（surface），不是写死的白
    await expect.poll(() => bg(dialog)).toBe(DARK_PANEL)
    await page.keyboard.press('Escape')
    await expect(dialog).toBeHidden()
    await expect.poll(() => bg(panel)).toBe(DARK_PANEL)
    // 纸是文档内容：不跟主题走
    expect(await bg(sheet)).toBe(PAPER)

    // 刷新：偏好在本机，挂载之前就落到 <html> 上
    await page.reload()
    await expect(sheet).toBeVisible({ timeout: 30_000 })
    expect(await theme(page)).toBe('dark')
    expect(await bg(body)).toBe(DARK_DESK)

    // 「跟随系统」：不挂 data-theme，颜色跟着系统外观走
    ;({ dialog, group } = await openGeneralSettings(page))
    await group.locator('[data-value="system"]').click()
    expect(await theme(page)).toBeNull()
    await expect.poll(() => bg(body)).toBe(LIGHT_DESK)
    await page.emulateMedia({ colorScheme: 'dark' })
    await expect.poll(() => bg(body)).toBe(DARK_DESK)

    // 「浅色」挡住系统的暗色
    await group.locator('[data-value="light"]').click()
    expect(await theme(page)).toBe('light')
    await expect.poll(() => bg(body)).toBe(LIGHT_DESK)
    await page.keyboard.press('Escape')
    expect(await bg(sheet)).toBe(PAPER)
  },
)
