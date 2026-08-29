import { expect, openElementsTab, test, type RunningApp } from './fixtures'
import type { Page } from '@playwright/test'

/**
 * Inspector 重构的黄金路径（docs/ux/INSPECTOR_REDESIGN.md 的量化验收）：
 *
 *   A. 标题：字体/字号可见标签 → 改字号 → undo/redo → 单项恢复到脚本；
 *   B. 曲线：颜色/线宽/线型首屏可见，线型用视觉选择器（不读 "--" 编码）；
 *   C. 图例：3×3 位置网格；
 *   E. 来源状态：头部计数随修改/恢复增减；
 *   布局：1366×768 左树 + 画布 + 属性栏共存。
 *
 * 全部走真实引擎渲染（examples/figures 的拷贝），不是手写 manifest。
 */

async function openFigure(page: Page, a: RunningApp) {
  await page.goto(a.baseURL)
  // Prompt 09 起，双击素材卡 = 打开这张图（快速编辑工作区），**当场就在图内
  // 编辑态**——不再需要先「加入画布」再点一次「编辑图内元素」。
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 60_000 })
}

/** 打开左侧元素树并展开全部分组 */
async function openTree(page: Page) {
  await openElementsTab(page)
  await page.locator('[role="treeitem"]').first().waitFor({ timeout: 30_000 })
  for (let i = 0; i < 8; i++) {
    const g = page.locator('[role="treeitem"][aria-expanded="false"]').first()
    if (!(await g.count())) break
    await g.click()
    await page.waitForTimeout(120)
  }
}

const inspector = (page: Page) => page.getByLabel('右侧面板', { exact: true })

test('流程 A+E：标题的字体/字号可见标签、改字号、undo/redo、来源计数与单项恢复', async ({
  app,
  page,
}) => {
  const a = await app()
  await openFigure(page, a)
  await openTree(page)
  await page.getByRole('treeitem', { name: /^标题/ }).click()

  const panel = inspector(page)
  // 「字体」「字号」是可见文字（不是只有 aria-label）
  await expect(panel.getByText('字体', { exact: true })).toBeVisible()
  await expect(panel.getByText('字号', { exact: true })).toBeVisible()
  await expect(panel.getByText('颜色', { exact: true })).toBeVisible()

  // 改字号：字号输入框里敲 12 回车
  const size = panel.getByRole('textbox', { name: '字号' })
  await size.fill('12')
  await size.press('Enter')
  // 来源状态：头部出现「1 项已修改」，行上出现恢复按钮
  await expect(panel.getByText('1 项已修改')).toBeVisible({ timeout: 15_000 })

  // 再改颜色（第二项）：头部计数 → 2
  const color = panel.locator('input[type="color"]').first()
  await color.evaluate((el, v) => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    setter.call(el, v)
    el.dispatchEvent(new Event('input', { bubbles: true }))
    el.dispatchEvent(new Event('change', { bubbles: true }))
  }, '#aa2233')
  await expect(panel.getByText('2 项已修改')).toBeVisible({ timeout: 15_000 })
  // 取色手势按「安静计时」收尾（450ms）：等它把事务关掉再撤销
  await page.waitForTimeout(800)

  // undo 两次（字号那轮是一条历史、颜色一条）→ 计数归零；redo 回到 2
  await page.getByRole('button', { name: '撤销' }).click()
  await page.getByRole('button', { name: '撤销' }).click()
  await expect(panel.getByText(/项已修改/)).toHaveCount(0)
  await page.getByRole('button', { name: '重做' }).click()
  await page.getByRole('button', { name: '重做' }).click()
  await expect(panel.getByText('2 项已修改')).toBeVisible()

  // 单项恢复：恢复字号 → 剩 1 项；恢复颜色 → 归零
  await panel.getByRole('button', { name: '恢复字号' }).click()
  await expect(panel.getByText('1 项已修改')).toBeVisible()
  await panel.getByRole('button', { name: '恢复颜色' }).click()
  await expect(panel.getByText(/项已修改/)).toHaveCount(0)
})

test('流程 B+C：曲线首屏（视觉线型选择器）与图例 3×3 位置网格', async ({ app, page }) => {
  const a = await app()
  await openFigure(page, a)
  await openTree(page)

  // --- 曲线 ---
  await page.getByRole('treeitem', { name: /^曲线/ }).first().click()
  const panel = inspector(page)
  await expect(panel.getByText('颜色', { exact: true })).toBeVisible()
  await expect(panel.getByText('线宽', { exact: true })).toBeVisible()
  await expect(panel.getByText('线型', { exact: true })).toBeVisible()
  // 线型是视觉选择器：radio 上是「实线/虚线」的名字 + SVG 预览，不是 "--" 下拉
  const solid = panel.getByRole('radio', { name: '实线' })
  await expect(solid).toHaveAttribute('aria-checked', 'true')
  await panel.getByRole('radio', { name: '虚线' }).click()
  await expect(panel.getByRole('radio', { name: '虚线' })).toHaveAttribute(
    'aria-checked',
    'true',
    { timeout: 15_000 },
  )
  // marker 选择器在首屏（不需要展开折叠组）
  await expect(panel.getByText('标记', { exact: true })).toBeVisible()

  // --- 图例 ---
  await page.getByRole('treeitem', { name: /^图例（图例）/ }).click()
  await expect(panel.getByRole('radio', { name: '右下' })).toHaveAttribute(
    'aria-checked',
    'true',
  )
  await panel.getByRole('radio', { name: '左上' }).click()
  await expect(panel.getByRole('radio', { name: '左上' })).toHaveAttribute(
    'aria-checked',
    'true',
    { timeout: 15_000 },
  )
  // 字号在首屏
  await expect(panel.getByText('字号', { exact: true })).toBeVisible()
})

test('1366×768：左树、画布与属性栏三者共存', async ({ app, page }) => {
  await page.setViewportSize({ width: 1366, height: 768 })
  const a = await app()
  await openFigure(page, a)
  await openTree(page)
  await page.getByRole('treeitem', { name: /^标题/ }).click()

  // 左树还在
  await expect(page.getByRole('tree')).toBeVisible()
  // 右栏属性也在，且显示的是刚选中的标题
  const panel = inspector(page)
  await expect(panel.getByText('字号', { exact: true })).toBeVisible()
  // 画布仍有可操作区域（世界层可见且宽度可观）
  const stage = page.locator('[data-element-svg] svg').first()
  await expect(stage).toBeVisible()
  // 三者横向互不遮挡：树右缘 < 画布 svg 左缘不必成立（画布可平移），
  // 但右栏左缘必须 > 树右缘，且中间至少留出 500px
  const tree = await page.getByRole('tree').boundingBox()
  const aside = await panel.boundingBox()
  expect(tree && aside && aside.x - (tree.x + tree.width) > 500).toBe(true)
})
