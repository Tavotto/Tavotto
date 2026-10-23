/**
 * 导出对话框的缩略图说真话（PR3）：对象头缩略图旁有「示意 · 非最终文件」标注；
 * 勾上透明背景后画布范围的缩略图页面画成棋盘格。语言无关写法（chromium 与 chromium-en 各跑一遍）。
 */
import type { Page } from '@playwright/test'
import { addSecondFigure, expect, test } from './fixtures'

const CARD = '[data-card="Fig1_kinetics.pdf"]'

async function openExport(page: Page, baseURL: string) {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(baseURL)
  await page.locator(CARD).dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-element-svg] > svg')).toBeVisible({ timeout: 120_000 })
  // 画布范围的缩略图 / 透明背景只在多图排版里有：第二张图进来
  await addSecondFigure(page)
  await expect(page.locator('[data-canvas-stage] [data-object-id]')).toHaveCount(2)
  await page.keyboard.press('ControlOrMeta+e')
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  return dialog
}

test('导出预览角标：有「示意」标注，勾透明背景后页面是棋盘格', async ({ app, page }) => {
  test.setTimeout(180_000)
  const a = await app()
  const dialog = await openExport(page, a.baseURL)
  // 顶部只有一行「名字 · 尺寸」，「示意」标注挂在左栏预览的角上
  const stage = dialog.locator('[data-export-preview-stage]')
  const note = stage.locator('[data-export-thumb-note]')
  await expect(note).toBeVisible()
  await expect(note).not.toHaveText('')
  await expect(dialog.locator('[data-export-target] [data-export-thumb-note]')).toHaveCount(0)
  // 标注在对话框内、没有撑破它
  const nb = (await note.boundingBox())!
  const db = (await dialog.boundingBox())!
  expect(nb.x).toBeGreaterThanOrEqual(db.x)
  expect(nb.x + nb.width).toBeLessThanOrEqual(db.x + db.width + 0.5)

  // 默认（含 PNG、不透明）：页面是纸白，没有棋盘格
  const png = dialog.getByRole('checkbox', { name: 'PNG', exact: true })
  if (!(await png.isChecked())) await png.check()
  await expect(stage.locator('[data-thumb-page]')).toHaveCount(1)
  await expect(stage.locator('[data-thumb-transparent]')).toHaveCount(0)

  await dialog.getByRole('switch', { name: /透明背景|transparent/i }).click()
  await expect(stage.locator('[data-thumb-transparent]')).toHaveCount(1)
  await expect(stage.locator('pattern')).toHaveCount(1)
  // 标注仍在（棋盘格不替代「示意」）
  await expect(note).toBeVisible()
})

test(
  '导出预览：图在预览区里不留边距，滚轮以指针为中心缩放、可拖动、双击回到适合大小',
  { tag: ['@feature:export.preview-zoom'] },
  async ({ app, page }) => {
    test.setTimeout(180_000)
    const a = await app()
    const dialog = await openExport(page, a.baseURL)
    const stage = dialog.locator('[data-export-preview-stage]')
    const zoom = stage.locator('[data-export-preview-zoom]')
    const content = stage.locator('[data-export-preview-content]')
    const scale = async () => Number(await zoom.getAttribute('data-export-preview-zoom'))
    await expect.poll(scale).toBe(1)

    // 不留边距：内容盒的某一对边（上下或左右）与舞台贴齐
    const sb = (await stage.boundingBox())!
    const cb = (await content.boundingBox())!
    const flushX = Math.abs(cb.width - sb.width) < 1.5
    const flushY = Math.abs(cb.height - sb.height) < 1.5
    expect(flushX || flushY, `内容 ${cb.width}×${cb.height} 在舞台 ${sb.width}×${sb.height} 里留了边距`).toBe(true)

    // 滚轮放大（以指针为中心）；放大后出现「适合大小」小按钮
    await page.mouse.move(sb.x + sb.width * 0.25, sb.y + sb.height * 0.25)
    await page.mouse.wheel(0, -600)
    await expect.poll(scale).toBeGreaterThan(1.3)
    await expect(stage.locator('[data-export-preview-reset]')).toBeVisible()

    // 拖动：内容跟着手走
    const before = (await content.boundingBox())!
    await page.mouse.down()
    await page.mouse.move(sb.x + sb.width * 0.25 + 30, sb.y + sb.height * 0.25 + 20, { steps: 4 })
    await page.mouse.up()
    const after = (await content.boundingBox())!
    expect(Math.abs(after.x - before.x) + Math.abs(after.y - before.y)).toBeGreaterThan(5)

    // 双击回到适合大小；按钮随之消失
    await stage.dblclick({ position: { x: sb.width / 2, y: sb.height / 2 } })
    await expect.poll(scale).toBe(1)
    await expect(stage.locator('[data-export-preview-reset]')).toHaveCount(0)

    // 再放大，用小按钮回去
    await page.mouse.move(sb.x + sb.width / 2, sb.y + sb.height / 2)
    await page.mouse.wheel(0, -600)
    await expect.poll(scale).toBeGreaterThan(1.3)
    await stage.locator('[data-export-preview-reset]').click()
    await expect.poll(scale).toBe(1)
  },
)
