/**
 * 图内副本（ADR 0207）：在图内编辑里选中图里的标题 → ⌘C ⌘V / ⌘D → 仍在图内编辑、元素树里多一只副本并被选中 →
 * 改字号 → ⌘Z 两次副本消失。选择器只用稳定的 `data-*`（元素树行 `data-el`、工作区条的退出图内编辑钮 `data-exit-element-edit`、检查器 `data-inspector-prop`）。
 */
import type { Page } from '@playwright/test'
import { expect, test, waitForFigureEdit } from './fixtures'

const TITLE = '[data-tree="elements"] [data-el="axes_0.title"]'
const COPIES = '[data-tree="elements"] [data-el^="axes_0.copy_"]'
const OBJECTS = '[data-canvas-stage] [data-object-id]'

async function openFigure(page: Page, baseURL: string) {
  await page.setViewportSize({ width: 1500, height: 950 })
  await page.goto(baseURL)
  await page.locator('[data-card="Fig1_kinetics.pdf"]').dblclick({ timeout: 30_000 })
  await waitForFigureEdit(page)
  await page.locator(TITLE).click()
  // 选中元素 = 在图内编辑：工作区条上有「退出图内编辑」的钮（它是图内编辑态的唯一出口，e2e 拿它判进没进）
  await expect(page.locator('[data-workspace-context-bar] [data-exit-element-edit]')).toHaveCount(1)
}

/** 副本出现、被选中、仍在图内编辑；画布上没有新增任何对象（不是落成标注，也不是复制了整张图） */
async function expectFreshCopy(page: Page, objectsBefore: number) {
  const copies = page.locator(COPIES)
  await expect(copies).toHaveCount(1, { timeout: 30_000 })
  await expect(copies).toHaveAttribute('aria-selected', 'true', { timeout: 30_000 })
  await expect(page.locator('[data-workspace-context-bar]')).toHaveAttribute('data-workspace-mode', 'fast_edit')
  await expect(page.locator('[data-workspace-context-bar] [data-exit-element-edit]')).toHaveCount(1)
  await expect(page.locator(OBJECTS)).toHaveCount(objectsBefore)
}

async function changeFontSizeAndUndoTwice(page: Page) {
  const size = page.locator('[data-inspector-prop="fontsize"]')
  await expect(size).toHaveCount(1)
  const value = Number(await size.inputValue())
  await size.fill(String(value + 4))
  await size.press('Enter')
  await expect(size).toHaveValue(String(value + 4), { timeout: 30_000 })
  // 焦点还给元素树（输入框会吃掉 ⌘Z），再撤销：第一下撤掉字号，第二下撤掉副本
  await page.locator(COPIES).click()
  await page.keyboard.press('ControlOrMeta+z')
  await expect(page.locator(COPIES)).toHaveCount(1)
  await expect(page.locator('[data-inspector-prop="fontsize"]')).toHaveValue(String(value), { timeout: 30_000 })
  await page.keyboard.press('ControlOrMeta+z')
  await expect(page.locator(COPIES)).toHaveCount(0, { timeout: 30_000 })
  await expect(page.locator('[data-workspace-context-bar] [data-exit-element-edit]')).toHaveCount(1)
}

test(
  '图内副本：选中标题 ⌘C ⌘V → 仍在图内编辑、多一只副本并被选中；改字号后 ⌘Z 两次副本消失',
  { tag: ['@feature:canvas.in-figure-duplicate'] },
  async ({ app, page, context }) => {
    test.setTimeout(240_000)
    await context.grantPermissions(['clipboard-read', 'clipboard-write'])
    const a = await app()
    await openFigure(page, a.baseURL)
    const objectsBefore = await page.locator(OBJECTS).count()
    await page.keyboard.press('ControlOrMeta+c')
    await page.waitForTimeout(300)
    await page.keyboard.press('ControlOrMeta+v')
    await expectFreshCopy(page, objectsBefore)
    await changeFontSizeAndUndoTwice(page)
  },
)

test(
  '图内副本：⌘D 在图内编辑里 = 原地建副本（不复制整张图），同样可撤销',
  { tag: ['@feature:canvas.in-figure-duplicate'] },
  async ({ app, page }) => {
    test.setTimeout(240_000)
    const a = await app()
    await openFigure(page, a.baseURL)
    const objectsBefore = await page.locator(OBJECTS).count()
    await page.keyboard.press('ControlOrMeta+d')
    await expectFreshCopy(page, objectsBefore)
    await changeFontSizeAndUndoTwice(page)
  },
)

test(
  '画布上点选标题后 ⌘C ⌘V：属性页文字框的残留选区不接粘贴，标题文字不变、建出图内副本',
  { tag: ['@feature:canvas.in-figure-duplicate'] },
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
    await expect(page.locator(TITLE)).toContainText('Reaction kinetics')
    await page.keyboard.press('ControlOrMeta+c')
    await page.waitForTimeout(300)
    await page.keyboard.press('ControlOrMeta+v')
    await expect(page.locator(COPIES)).toHaveCount(1, { timeout: 60_000 })
    await expect(page.locator('[data-workspace-context-bar] [data-exit-element-edit]')).toHaveCount(1)
    // 标题本身没被改成剪贴板里的那串负载
    await expect(page.locator(TITLE)).toContainText('Reaction kinetics')
    await expect(page.locator('[data-canvas-stage]')).not.toContainText('tavotto/objects')
  },
)
