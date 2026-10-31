import { expect } from './fixtures'
import type { Locator, Page } from '@playwright/test'

/**
 * 外观切换的共用写法（theme.spec / a11y.spec 共用，不抄第二份）：
 * 设置 › 通用 › 外观 `[data-testid="settings-theme"]` 里按 `data-value` 选 dark / light / system。
 */

/** 打开设置 › 通用，返回对话框与外观分段控件 */
export async function openGeneralSettings(page: Page) {
  await page.locator('[data-rail="settings"]').click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible({ timeout: 30_000 })
  await dialog.locator('[data-section="general"]').click()
  const group = dialog.locator('[data-testid="settings-theme"]')
  await expect(group).toBeVisible()
  return { dialog, group }
}

/** 在已打开的设置里选外观，并等 `<html>` 的 data-theme 落上（跟随系统 = 不挂） */
export async function chooseTheme(group: Locator, page: Page, value: 'dark' | 'light' | 'system') {
  const item = group.locator(`[data-value="${value}"]`)
  await item.click()
  await expect(item).toHaveAttribute('aria-checked', 'true')
  await expect
    .poll(() => page.evaluate(() => document.documentElement.getAttribute('data-theme')))
    .toBe(value === 'system' ? null : value)
}
