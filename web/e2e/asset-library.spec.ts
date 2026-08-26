import { mkdirSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, test, type RunningApp } from './fixtures'
import type { Page } from '@playwright/test'

/**
 * Compatibility Bridge Session 5：素材库普通入口的真实后端黄金路径。
 *
 * 负向反证 #1 的看护对象：show-only 项目（没有 PDF/PNG/SVG、没有 savefig）
 * 必须能从**素材库**（不是 RegistryDialog）走到编辑态——只展示静态
 * candidate 的话，这条当场红。
 *
 * 链路：打开 show-only 项目 → 脚本区看到 .py → 点「运行并发现图」→
 * Runtime Figure 出现在「图」区 → 加入画布 → 进入图内编辑 →
 * 改标题字号 → 改曲线线宽 → undo/redo。
 */

/** 造一个 show-only 项目：一个脚本、零磁盘图、零 savefig。 */
function writeShowOnlyProject(dir: string): void {
  mkdirSync(dir, { recursive: true })
  writeFileSync(
    path.join(dir, 'show_only.py'),
    [
      'import matplotlib',
      'matplotlib.use("Agg")',
      'import matplotlib.pyplot as plt',
      '',
      'plt.plot([1, 2, 3], [4, 5, 6], label="signal")',
      'plt.title("AI generated")',
      'plt.legend()',
      'plt.show()',
      '',
    ].join('\n'),
    'utf-8',
  )
  writeFileSync(path.join(dir, 'tavotto_registry.json'),
                JSON.stringify({ version: 1, scripts: {} }), 'utf-8')
}

async function runAndDiscover(page: Page, a: RunningApp) {
  await page.goto(a.baseURL)

  // 素材库分「图」「脚本」两个区；show-only 脚本在脚本区可见、可运行
  await expect(page.getByRole('heading', { name: '脚本' })).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('show_only.py').first()).toBeVisible()
  await expect(page.getByText('这个脚本尚未运行')).toBeVisible()

  // 显式用户动作才执行（总纲原则 5）：点「运行并发现图」
  await page.getByRole('button', { name: '运行 show_only.py 并发现图' }).click()
  // 运行中状态可见（starting → running 的具体切换取决于 SSE 时机，不硬卡）
  await expect(page.getByText(/正在启动渲染环境|正在运行脚本/)).toBeVisible()
  // 冷启动分钟级预算：真实 matplotlib worker
  await expect(page.getByText('已发现 1 张图')).toBeVisible({ timeout: 120_000 })
}

test('show-only 项目：素材库普通入口 → Runtime Figure → 画布 → 图内编辑 → undo/redo', async ({
  app,
  page,
}) => {
  const dir = path.join(os.tmpdir(), `tavotto-e2e-showonly-${Date.now()}`)
  writeShowOnlyProject(dir)
  const a = await app({ figures: dir })
  await runAndDiscover(page, a)

  // Runtime Figure 立即出现在「图」区：badge + 预览，身份是 runtime: 资产 id
  const card = page.locator('[data-card="runtime:show_only.py#show_only"]')
  await expect(card).toBeVisible({ timeout: 30_000 })
  await expect(card.getByText('运行时图')).toBeVisible()

  // 加入画布（双击卡片 = 主动作）
  await card.dblclick()
  await expect(page.getByText('画布是空的')).toHaveCount(0)

  // 进入图内编辑（面板刚被选中，右栏有入口）
  await page.getByRole('button', { name: '编辑图内元素' }).first().click()
  await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 120_000 })

  // 左侧元素树 → 标题 → 改字号
  await page.getByRole('navigation').getByRole('button', { name: '图内元素' }).click()
  await page.locator('[role="treeitem"]').first().waitFor({ timeout: 30_000 })
  for (let i = 0; i < 8; i++) {
    const g = page.locator('[role="treeitem"][aria-expanded="false"]').first()
    if (!(await g.count())) break
    await g.click()
    await page.waitForTimeout(120)
  }
  const panel = page.getByLabel('右侧面板', { exact: true })

  await page.getByRole('treeitem', { name: /^标题/ }).click()
  const size = panel.getByRole('textbox', { name: '字号' })
  await size.fill('12')
  await size.press('Enter')
  await expect(panel.getByText('1 项已修改')).toBeVisible({ timeout: 30_000 })

  // 曲线 → 改线宽（快速编辑工具条里的线宽输入框有可达名；属性栏里的
  // 数字框可见标签是同级文本，不入 accessible name）
  await page.getByRole('treeitem', { name: /^曲线/ }).first().click()
  const width = page
    .getByRole('toolbar', { name: '快速编辑' })
    .getByRole('textbox', { name: '线宽' })
  await width.fill('3')
  await width.press('Enter')
  await expect(panel.getByText('1 项已修改')).toBeVisible({ timeout: 30_000 })

  // undo 两次（线宽、字号各一条历史）→ redo 两次回来（当前面板显示的是
  // 曲线：第一次 redo 恢复的是标题字号，第二次才轮到曲线的线宽）
  await page.getByRole('button', { name: '撤销' }).click()
  await page.getByRole('button', { name: '撤销' }).click()
  await expect(panel.getByText(/项已修改/)).toHaveCount(0)
  await page.getByRole('button', { name: '重做' }).click()
  await page.getByRole('button', { name: '重做' }).click()
  await expect(panel.getByText('1 项已修改')).toBeVisible()
})

test('窄视口：脚本行的「运行并发现图」仍可见可点', async ({ app, page }) => {
  const dir = path.join(os.tmpdir(), `tavotto-e2e-narrow-${Date.now()}`)
  writeShowOnlyProject(dir)
  const a = await app({ figures: dir })
  await page.setViewportSize({ width: 960, height: 720 })
  await page.goto(a.baseURL)

  await expect(page.getByRole('heading', { name: '脚本' })).toBeVisible({ timeout: 30_000 })
  const run = page.getByRole('button', { name: '运行 show_only.py 并发现图' })
  await expect(run).toBeVisible()
  // 真的在可视区里（不是被挤出去后 Playwright 自动滚动救回来的）
  const box = await run.boundingBox()
  expect(box).not.toBeNull()
  expect(box!.x).toBeGreaterThanOrEqual(0)
  expect(box!.x + box!.width).toBeLessThanOrEqual(960)
})
