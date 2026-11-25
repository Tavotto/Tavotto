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

test(
  '画布上点选标题后 ⌘C ⌘V：属性页文字框的残留选区不接粘贴，标题文字不变、落成标注',
  { tag: ['@feature:canvas.copy-figure-text-as-annotation'] },
  async ({ app, page, context }) => {
    test.setTimeout(240_000)
    await context.grantPermissions(['clipboard-read', 'clipboard-write'])
    const a = await app()
    await page.setViewportSize({ width: 1500, height: 950 })
    await page.goto(a.baseURL)
    await page.locator('[data-card="Fig1_kinetics.pdf"]').dblclick({ timeout: 30_000 })
    await waitForFigureEdit(page)
    // 在画布上点（不经元素树）：属性页的文字框抢焦点、全选、再把焦点还给页面，留下残留选区
    const title = (await page.locator('[data-element-svg] [id="axes_0.title"]').boundingBox())!
    await page.mouse.click(title.x + title.width / 2, title.y + title.height / 2)
    await expect(page.locator('[data-el="axes_0.title"]')).toContainText('Reaction kinetics')
    await page.keyboard.press('ControlOrMeta+c')
    await page.waitForTimeout(300)
    await page.keyboard.press('ControlOrMeta+v')
    const note = page.locator('[data-canvas-stage] [data-object-id] [data-text-body]')
    await expect(note).toHaveCount(1, { timeout: 15_000 })
    await expect(note).toContainText('Reaction kinetics')
    // 标题本身没被改成剪贴板里的那串负载
    await expect(page.locator('[data-canvas-stage]')).not.toContainText('tavotto/objects')
  },
)
