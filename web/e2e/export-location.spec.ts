/**
 * 导出对话框「位置」（b3 反馈）：点「更改…」直接弹选择文件夹（浏览器版 = 服务器端目录浏览器），
 * 选完位置行更新、导出写到新目录；不再跳设置页。语言无关写法（chromium 与 chromium-en 各跑一遍）。
 */
import { existsSync, mkdtempSync, readdirSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, test } from './fixtures'

const CARD = '[data-card="Fig1_kinetics.pdf"]'

test(
  '导出位置：点「更改…」弹目录浏览器 → 选目录 → 位置更新 → 导出写到新目录',
  { tag: ['@feature:export.location-picker'] },
  async ({ app, page }) => {
    test.setTimeout(240_000)
    const a = await app()
    const target = mkdtempSync(path.join(os.tmpdir(), 'tavotto-exportloc-'))
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto(a.baseURL)
    await page.locator(CARD).dblclick({ timeout: 30_000 })
    await expect(page.locator('[data-element-svg] > svg')).toBeVisible({ timeout: 120_000 })
    await page.keyboard.press('ControlOrMeta+e')
    const dialog = page.locator('[data-dialog="export"]')
    await expect(dialog).toBeVisible()

    const location = dialog.locator('[data-export-location]')
    const before = await location.getAttribute('title')
    expect(before, '默认位置的 title 是完整绝对路径').toMatch(/^([/~]|[A-Za-z]:)/)
    // 非等宽、不再是只有末级名
    expect(await location.evaluate((el) => getComputedStyle(el).fontFamily)).not.toMatch(/mono/i)

    await dialog.locator('[data-export-location-change]').click()
    // 弹的是选择文件夹，不是设置页
    const browser = page.locator('[data-dir-browser-path]')
    await expect(browser).toBeVisible()
    await expect(page.locator('[data-dialog="settings"]')).toHaveCount(0)
    // 从当前导出目录的上一级打开，当前目录那一行高亮（一眼看到同级、也能点进去确认原目录）
    const current = page.locator('[data-dir-row][data-current]')
    await expect(current).toHaveCount(1)
    await expect(current).toHaveAttribute('data-dir-row', path.basename(before!))
    expect(path.dirname(before!)).toBe(await browser.inputValue())
    await browser.fill(target)
    await browser.press('Enter')
    const confirm = page.locator('[data-dir-browser-confirm]')
    await expect(confirm).toBeEnabled()
    await confirm.click()
    await expect(browser).toHaveCount(0)

    await expect(location).toHaveAttribute('title', new RegExp(`${path.basename(target)}$`))

    const started = page.waitForResponse(
      (r) => new URL(r.url()).pathname === '/api/export/start' && r.request().method() === 'POST',
    )
    await dialog.locator('[data-export-start]').click()
    const job = (await (await started).json()) as { export_dir?: string }
    expect(job.export_dir, '导出写到选的目录').toMatch(new RegExp(`${path.basename(target)}$`))
    await expect(dialog.locator('[data-export-result]')).toBeVisible({ timeout: 240_000 })
    await expect
      .poll(() => (existsSync(target) ? readdirSync(target).filter((f) => /\.(pdf|png)$/i.test(f)).length : 0), {
        timeout: 60_000,
      })
      .toBeGreaterThanOrEqual(1)
  },
)
