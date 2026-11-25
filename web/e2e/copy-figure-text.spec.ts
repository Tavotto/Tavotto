/**
 * 图内文字「复制为标注」（ADR 0163，ADR 0207 修订）：编辑图内元素 → 选中图里的标题 → ⌘C → 退出图内编辑 → ⌘V →
 * 画布上多出一个文字标注，字一样，屏幕位置在原文字的右下（同画布粘贴错开 4 mm）。
 * （同一张图的图内编辑里 ⌘V 现在建的是图内副本，见 `figure-duplicate.spec.ts`；落在别处才是标注。）
 */
import { expect, test, waitForFigureEdit } from './fixtures'

test(
  '图内文字复制为标注：选中标题 ⌘C，退出图内编辑后 ⌘V → 多一个同字的文字标注，在原文字右下',
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
    // 退出图内编辑（它的唯一出口）：在图内编辑里 ⌘V 建的是图内副本，落在画布上才是标注
    await page.locator('[data-workspace-context-bar] [data-exit-element-edit]').click()
    await expect(page.locator('[data-exit-element-edit]')).toHaveCount(0)
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
  '图内编辑里右键「粘贴为标注」：画布上点选标题 ⌘C 后，弹层「粘贴为标注」落成同字的文字标注，标题本身不变',
  { tag: ['@feature:canvas.copy-figure-text-as-annotation'] },
  async ({ app, page, context }) => {
    test.setTimeout(240_000)
    await context.grantPermissions(['clipboard-read', 'clipboard-write'])
    const a = await app()
    await page.setViewportSize({ width: 1500, height: 950 })
    await page.goto(a.baseURL)
    await page.locator('[data-card="Fig1_kinetics.pdf"]').dblclick({ timeout: 30_000 })
    await waitForFigureEdit(page)
    // 在画布上点（不经元素树）：属性页文字框留下没有焦点的残留选区，⌘C 复制的仍是元素
    const title = (await page.locator('[data-element-svg] [id="axes_0.title"]').boundingBox())!
    const cx = title.x + title.width / 2
    const cy = title.y + title.height / 2
    await page.mouse.click(cx, cy)
    await expect(page.locator('[data-el="axes_0.title"]')).toContainText('Reaction kinetics')
    await page.keyboard.press('ControlOrMeta+c')
    await page.waitForTimeout(300)
    await page.mouse.click(cx, cy, { button: 'right' })
    const item = page.locator('[role="dialog"] [data-quick-item="paste-as-annotation"]')
    await expect(item).toBeEnabled()
    await item.click()
    const note = page.locator('[data-canvas-stage] [data-object-id] [data-text-body]')
    await expect(note).toHaveCount(1, { timeout: 15_000 })
    await expect(note).toContainText('Reaction kinetics')
    await expect(page.locator('[data-canvas-stage]')).not.toContainText('tavotto/objects')
  },
)
