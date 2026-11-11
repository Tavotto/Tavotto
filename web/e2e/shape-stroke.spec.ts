import { expect, test, waitForDocumentReady } from './fixtures'

/**
 * 形状 / 箭头描边属性补齐（ADR 0174）：真浏览器里画矩形、关掉描边、在「更多」里改线端，
 * 再画箭头换燕尾端型。判据是**画布上这个 SVG 图元此刻带不带描边、填充铺多大**，不是文档里的字段。
 */
test(
  '功能：矩形可关掉描边（填充铺满框）、「更多」里改线端；箭头可换燕尾端型',
  { tag: ['@feature:canvas.shape-stroke-options'] },
  async ({ app, page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    const a = await app()
    await page.goto(a.baseURL)
    await expect(page.locator('[data-page-sheet]')).toBeVisible({ timeout: 30_000 })
    await waitForDocumentReady(page)
    await page.waitForTimeout(500)

    const sheet = (await page.locator('[data-page-sheet]').boundingBox())!
    const drag = async (key: string, x0: number, y0: number, x1: number, y1: number) => {
      await page.keyboard.press(key)
      await page.mouse.move(sheet.x + x0, sheet.y + y0)
      await page.mouse.down()
      await page.mouse.move(sheet.x + x1, sheet.y + y1, { steps: 6 })
      await page.mouse.up()
      await page.waitForTimeout(250)
    }
    const panel = page.locator('[data-inspector-panel]')

    // ── 矩形：先加填充，描边默认开着 ─────────────────────────────────────
    await drag('r', 40, 40, 160, 120)
    await panel.locator('[data-effect-add]').first().click()
    const rect = page.locator('[data-object-id] svg rect').first()
    await expect(rect).toHaveAttribute('stroke', /.+/)
    const inset = Number(await rect.getAttribute('x'))
    expect(inset).toBeGreaterThan(0) // 有描边：内缩半个线宽

    // ── 关掉描边：画布上这个矩形不再带任何描边属性，填充铺满包围盒 ──────────
    await panel.locator('[role=switch][aria-label="描边"]').click()
    await expect(rect).not.toHaveAttribute('stroke', /.+/)
    await expect(rect).toHaveAttribute('x', '0')
    await expect(panel.locator('[data-fold="stroke-more"]')).toHaveCount(0) // 描边细节一并收起

    // ── 重新开着描边，在「更多」里选平头：画布上的线端真的变了 ────────────────
    await panel.locator('[data-effect-add]').first().click()
    await expect(rect).toHaveAttribute('stroke-linecap', 'round')
    await panel.locator('[data-fold="stroke-more"] button').click()
    await panel.locator('[role=radiogroup][aria-label="线端"] [role=radio]').first().click()
    await expect(rect).toHaveAttribute('stroke-linecap', 'butt')

    // ── 箭头：终点端型换成燕尾 ───────────────────────────────────────────
    await drag('a', 40, 180, 200, 180)
    await panel.locator('button[aria-label="终点"]').click()
    await page.getByRole('radio', { name: '燕尾' }).click()
    const heads = page.locator('[data-object-id] svg polygon')
    await expect(heads.last()).toHaveAttribute('points', /^(\S+ ){3}\S+$/) // 燕尾 = 4 个点
  },
)
