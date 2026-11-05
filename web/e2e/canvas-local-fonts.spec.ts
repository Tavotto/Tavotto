/**
 * 画布文字选本机字体（ADR 0169）：字体下拉里除内置三类外列出这台机器能嵌入的字体，选中后导出请求带上
 * 这张脸的身份；按加粗换成同族的粗体文件。DejaVu 随 matplotlib 而来，每台机器都有。
 */
import { expect, test } from './fixtures'
import path from 'node:path'

test(
  '标注选本机字体：下拉里有 DejaVu Serif，选中后导出带身份，加粗换成同族粗体文件',
  { tag: ['@feature:canvas.text-local-fonts'] },
  async ({ app, page }) => {
    test.setTimeout(240_000)
    const a = await app()
    await page.setViewportSize({ width: 1500, height: 950 })
    await page.goto(a.baseURL)
    await page.locator('[data-card="Fig1_kinetics.pdf"]').dblclick({ timeout: 30_000 })
    await expect(page.locator('[data-canvas-toolbar]')).toHaveCount(1)
    await page.keyboard.press('ControlOrMeta+k')
    const addText = page.locator('[data-cmd-id="add-text"]')
    await expect(addText).toBeVisible()
    await addText.click()
    await expect(addText).toHaveCount(0)
    await page.keyboard.press('Escape')
    await page.locator('[data-canvas-stage] [data-object-id] [data-text-body]').click()
    // 机器上有中文字体时字体行是两格（英文 + 中文），否则一格（Linux CI）
    const latin = page.getByRole('combobox', { name: /^(英文字体|字体)$/ })
    await expect(latin).toBeVisible({ timeout: 30_000 })
    const shot = async (name: string) => {
      const dir = process.env.TAVOTTO_SHOT_DIR
      if (!dir) return
      await page.waitForTimeout(350)
      await page.screenshot({ path: path.join(dir, `${name}.png`) })
    }
    // 首次启动清单在后台扫，下拉先只有内置三项，扫完才长出来
    const dejavu = page.getByRole('option', { name: 'DejaVu Serif', exact: true })
    await expect(async () => {
      await latin.click()
      await expect(dejavu).toBeVisible({ timeout: 1000 })
    }).toPass({ timeout: 60_000 })
    await shot('menu')
    await dejavu.click()
    await expect(latin).toContainText('DejaVu Serif')

    // 中文格：有中文字体的机器才验
    const cjk = page.getByRole('combobox', { name: '中文字体' })
    const hasCjk = (await cjk.count()) > 0
    if (hasCjk) {
      await cjk.click()
      await page.getByRole('option').nth(1).click()
    }
    await shot('picked')

    const bold = page.getByRole('button', { name: '加粗', exact: true }).first()
    await bold.click()
    await expect(bold).toHaveAttribute('aria-pressed', 'true')

    await page.keyboard.press('ControlOrMeta+e')
    const start = page.locator('[data-export-start]')
    await expect(start).toBeEnabled()
    const request = page.waitForRequest((r) => new URL(r.url()).pathname === '/api/export/start' && r.method() === 'POST')
    await start.click()
    const faces: { family: string; bold?: boolean; cjk?: { family: string } }[] = []
    JSON.stringify((await request).postDataJSON(), (key, value) => {
      if (key === 'source_font') faces.push(value)
      return value
    })
    expect(faces.length).toBeGreaterThan(0)
    expect(faces[0]).toMatchObject({ family: 'DejaVu Serif', bold: true })
    if (hasCjk) expect(faces[0].cjk?.family).toBeTruthy()
  },
)
