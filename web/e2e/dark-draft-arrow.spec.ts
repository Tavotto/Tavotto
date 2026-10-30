import { expect, test } from './fixtures'

test('深色工作区拖画箭头：临时轮廓可见，落笔保留文档颜色', async ({ app, page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const a = await app()
  await page.goto(a.baseURL)
  await expect(page.locator('[data-page-sheet]')).toBeVisible({ timeout: 30_000 })
  await page.evaluate(() => { document.documentElement.dataset.theme = 'dark' })
  await page.keyboard.press('a')

  const stage = (await page.locator('[data-canvas-stage]').boundingBox())!
  const sheet = (await page.locator('[data-page-sheet]').boundingBox())!
  // 在白纸上起笔、拖到纸外的深色工作区；鼠标保持按下时量草稿。
  const x = sheet.x + 30
  const y = sheet.y + 30
  const endX = Math.max(stage.x + 10, sheet.x - 30)
  await page.mouse.move(x, y)
  await page.mouse.down()
  await page.mouse.move(endX, y + 45, { steps: 5 })
  const draft = page.locator('[data-draft-line-preview]')
  await expect(draft).toBeVisible()
  const outline = draft.locator('[data-draft-outline="line"]')
  const head = draft.locator('[data-draft-outline="head"]')
  const content = draft.locator('[data-draft-content="line"]')
  await expect(head).toBeVisible()
  const colors = await outline.evaluate((el) => {
    const rgb = (value: string) => value.match(/\d+/g)!.slice(0, 3).map(Number)
    const luminance = (c: number[]) => c.reduce((sum, n, i) => {
      const v = n / 255
      return sum + [0.2126, 0.7152, 0.0722][i] * (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4)
    }, 0)
    const stroke = getComputedStyle(el).stroke
    const canvas = getComputedStyle(el).getPropertyValue('--color-canvas').trim()
    const probe = document.createElement('span')
    probe.style.color = 'var(--color-sel)'
    probe.style.backgroundColor = canvas
    el.parentElement!.appendChild(probe)
    const style = getComputedStyle(probe)
    const token = style.color
    const [a, b] = [luminance(rgb(stroke)), luminance(rgb(style.backgroundColor))].sort((x, y) => y - x)
    probe.remove()
    return { stroke, token, contrast: (a + 0.05) / (b + 0.05) }
  })
  expect(colors.stroke).toBe(colors.token)
  expect(colors.contrast).toBeGreaterThan(3)
  expect(await head.evaluate((el) => getComputedStyle(el).fill)).toBe(colors.stroke)
  expect(await content.evaluate((el) => getComputedStyle(el).stroke)).toBe('rgb(27, 27, 24)')
  await page.mouse.up()
  await expect(draft).toHaveCount(0)
  const arrow = page.locator('[data-object-id] svg polygon').last()
  await expect(arrow).toHaveAttribute('fill', '#1B1B18')
})
