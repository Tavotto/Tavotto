import type { Page } from '@playwright/test'
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import path from 'node:path'
import { expect, test, waitForDocumentReady } from './fixtures'

/**
 * 渐变填充（ADR 0186）：给矩形把填充类型改成「线性」，画布上这个矩形的填充真的变成
 * `url(#…)` 指向一个带多个色标的 `<linearGradient>`；导出 SVG 里同样是渐变，不是纯色。
 * 判据：画布 SVG 里的填充引用与色标、导出文件里的 `<linearGradient>`，不是文档字段。
 */

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    try {
      const key = 'tavotto.ui'
      const saved = JSON.parse(localStorage.getItem(key) || '{}')
      localStorage.setItem(key, JSON.stringify({ ...saved, prefsVersion: 2, snapEnabled: false }))
    } catch {
      /* 存储不可用时照常跑 */
    }
  })
})

async function openCanvas(page: Page, baseURL: string) {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(baseURL)
  await expect(page.locator('[data-page-sheet]')).toBeVisible({ timeout: 30_000 })
  await waitForDocumentReady(page)
  await page.waitForTimeout(500)
  return (await page.locator('[data-page-sheet]').boundingBox())!
}

function findFile(dir: string, name: string): string | null {
  if (!existsSync(dir)) return null
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry)
    if (entry === name) return full
    if (statSync(full).isDirectory()) {
      const hit = findFile(full, name)
      if (hit) return hit
    }
  }
  return null
}

test.describe('渐变填充', () => {
  test(
    '矩形填充改成线性渐变：画布上填充引用渐变、色标不止一种颜色；导出 SVG 里是 linearGradient',
    { tag: ['@feature:canvas.gradient-fill'] },
    async ({ app, page }) => {
      test.setTimeout(240_000)
      const a = await app()
      const sheet = await openCanvas(page, a.baseURL)

      await page.keyboard.press('r')
      await page.mouse.move(sheet.x + 120, sheet.y + 120)
      await page.mouse.down()
      await page.mouse.move(sheet.x + 360, sheet.y + 260, { steps: 6 })
      await page.mouse.up()
      await page.waitForTimeout(250)
      const panel = page.locator('[data-inspector-panel]')
      await panel.locator('[data-effect-add="fill"]').click()

      const rect = page.locator('[data-canvas-stage] [data-object-id]').last().locator('svg rect').first()
      // 纯色时填充是一个颜色值，不是引用
      await expect(rect).toHaveAttribute('fill', /^(?!url\()/)

      // 填充类型 → 线性
      await panel.locator('[data-select-trigger][aria-label="填充类型"]').click()
      await page.locator('[data-select-option="linear"]').click()

      // 画布：填充变成 url(#id)，id 对应的 <linearGradient> 在同一个 SVG 里、至少两个色标且颜色不同
      await expect(rect).toHaveAttribute('fill', /^url\(#.+\)$/)
      const info = await rect.evaluate((el) => {
        const id = /url\(#(.+)\)/.exec(el.getAttribute('fill') ?? '')![1]
        const g = (el as SVGGraphicsElement).ownerSVGElement!.querySelector(`linearGradient[id="${id}"]`)
        const colors = g ? [...g.querySelectorAll('stop')].map((s) => s.getAttribute('stop-color') ?? getComputedStyle(s).stopColor) : []
        return { found: !!g, colors }
      })
      expect(info.found, '填充引用的 <linearGradient> 应在画布 SVG 里').toBe(true)
      expect(info.colors.length).toBeGreaterThanOrEqual(2)
      expect(new Set(info.colors).size, '色标颜色应当不同（真有渐变）').toBeGreaterThanOrEqual(2)

      // 导出 SVG：文件里是渐变
      await page.keyboard.press('Escape')
      await page.keyboard.press('ControlOrMeta+e')
      const dialog = page.locator('[data-dialog="export"]')
      await expect(dialog).toBeVisible()
      const svg = dialog.locator('[data-export-format="svg"] input')
      await expect(svg).toBeEnabled({ timeout: 30_000 })
      await svg.check()
      await expect(svg).toBeChecked()
      await page.locator('#export-filename').fill('gradient-e2e')
      await page.locator('[data-export-start]').click()
      await expect(page.locator('[data-export-result]')).toBeVisible({ timeout: 120_000 })
      const found = findFile(a.figures, 'gradient-e2e.svg')
      expect(found, 'SVG 落盘').not.toBeNull()
      const text = readFileSync(found!, 'utf8')
      expect(text.trimStart().startsWith('<?xml') || text.trimStart().startsWith('<svg')).toBe(true)
      expect(text).toMatch(/<linearGradient\b/)
    },
  )
})
