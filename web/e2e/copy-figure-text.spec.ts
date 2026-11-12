/**
 * 图内文字「复制为标注」（ADR 0163）：编辑图内元素 → 选中图里的标题 → ⌘C → ⌘V →
 * 画布上多出一个文字标注，字一样，屏幕位置在原文字的右下（同画布粘贴错开 4 mm）。
 */
import { expect, test, waitForFigureEdit } from './fixtures'

test(
  '图内文字复制为标注：选中标题 ⌘C ⌘V → 多一个同字的文字标注，在原文字右下',
  { tag: ['@feature:canvas.copy-figure-text-as-annotation'] },
  async ({ app, page, context }) => {
    test.setTimeout(240_000)
    await context.grantPermissions(['clipboard-read', 'clipboard-write'])
    const a = await app()
    await page.setViewportSize({ width: 1500, height: 950 })
    await page.goto(a.baseURL)
    await page.locator('[data-card="Fig1_kinetics.pdf"]').dblclick({ timeout: 30_000 })
    await waitForFigureEdit(page)
    // 在元素树里选中标题（选中框就是原文字在屏幕上的位置）
    await page.locator('[data-el="axes_0.title"]').click()
    const box = page.locator('[data-element-box="selected"], [data-canvas-stage] svg [data-element-box]').first()
    await expect(box).toBeVisible()
    const orig = (await box.boundingBox())!
    await page.keyboard.press('ControlOrMeta+c')
    await page.waitForTimeout(300)
    await page.keyboard.press('ControlOrMeta+v')
    const note = page.locator('[data-canvas-stage] [data-object-id] [data-text-body]')
    await expect(note).toHaveCount(1, { timeout: 15_000 })
    await expect(note).toContainText('Reaction kinetics')
    const got = (await note.boundingBox())!
    // 右下：4 mm 在这个缩放下是几十个屏幕像素；左上角都比原文字靠右靠下
    expect(got.x - orig.x).toBeGreaterThan(8)
    expect(got.y - orig.y).toBeGreaterThan(8)
    expect(got.x - orig.x).toBeLessThan(200)
    expect(got.y - orig.y).toBeLessThan(200)
  },
)
