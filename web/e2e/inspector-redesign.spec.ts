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
  // 线型是视觉选择器：触发钮上是当前项的名字 + SVG 预览（不是 "--" 这种码），
  // 展开后每一项是带预览的 radio（Visual Consolidation 把整块网格收成了下拉）
  // exact：旁边还有一颗「恢复线型」，子串匹配会把两颗都捞进来
  const lineStyle = panel.getByRole('button', { name: '线型', exact: true })
  await expect(lineStyle).toContainText('实线')
  await lineStyle.click()
  await expect(panel.getByRole('radio', { name: '实线' })).toHaveAttribute('aria-checked', 'true')
  await panel.getByRole('radio', { name: '虚线' }).click()
  await expect(lineStyle).toContainText('虚线', { timeout: 15_000 })
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

test('流程 C2：图例放到子图外面（外侧锚点），检查随即报「超出图幅」', async ({ app, page }) => {
  const a = await app()
  await openFigure(page, a)
  await openTree(page)
  await page.getByRole('treeitem', { name: /^图例（图例）/ }).click()
  const panel = inspector(page)

  // 外侧带在首屏（不用展开任何折叠组），六个位各是一个 radio
  const rightTop = panel.getByRole('radio', { name: '右侧上' })
  await expect(rightTop).toBeVisible({ timeout: 15_000 })
  // 图内摆着的时候不说那句「可能超出图幅」——那时它是句噪音
  await expect(panel.getByText(/可能超出图幅/)).toHaveCount(0)

  await rightTop.click()
  await expect(rightTop).toHaveAttribute('aria-checked', 'true', { timeout: 15_000 })
  // 锚点那两个数字是父容器分数坐标：1.02 = 子图右边缘往外 2%。
  // 它们默认收在「锚点」折叠里（Visual Consolidation）：先展开再读值——
  // `getByRole` 只认可访问树，hidden 的输入框对它等于不存在。
  const refine = panel.getByRole('button', { name: '锚点', exact: true })
  await expect(refine).toHaveAttribute('aria-expanded', 'false')
  await refine.click()
  await expect(panel.getByRole('textbox', { name: /锚点 x/ })).toHaveValue('1.02')
  await expect(panel.getByText(/可能超出图幅/)).toBeVisible()

  // **真的跑到图幅外面了**：预检的 element-outside-figure 报出来（审计 T14）。
  // 这一条是 jsdom 量不到的那半——它要真的渲染一遍、真的量元素的框。
  // 报在**问题面板**里：Visual Consolidation 撤掉了检查器里就地的 ElementIssueNote，
  // 问题面板是全产品唯一的清单（ADR 0030）。从常驻轨道进，与检查器同屏。
  const rail = page.locator('[data-rail="problems"]')
  await rail.focus()
  await page.keyboard.press('Enter')
  const problems = page.getByRole('complementary', { name: /问题|Problems/ })
  await expect(problems).toBeVisible()
  // **只认图例那一行**：清单是全文档的，别的元素（这台机器的 matplotlib 排出来的
  // x 轴标题就差 1.87 mm 出界）也可能在同一组里，按组名判会把它们的问题算成图例的。
  // 行的锚点是 `data-issue-row` + `data-issue-rule`；主语那个 span 恰好只写「图例」。
  const legendOutside = problems
    .locator('[data-issue-row][data-issue-rule="element-outside-figure"]')
    .filter({ has: page.locator('span', { hasText: /^图例$/ }) })
  await expect(legendOutside).toHaveCount(1, { timeout: 30_000 })

  // 点回九宫格 = 回到子图内侧：那句提示与图例超出图幅的问题一起消失
  await panel.getByRole('radio', { name: '左上' }).click()
  await expect(panel.getByText(/可能超出图幅/)).toHaveCount(0)
  await expect(legendOutside).toHaveCount(0, { timeout: 30_000 })
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
