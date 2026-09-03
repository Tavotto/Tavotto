import AxeBuilder from '@axe-core/playwright'
import { lowContrastNodes } from './contrast'
import { expect, test } from './fixtures'
import type { Page } from '@playwright/test'

/**
 * 新手教程（Prompt 21，ADR 0040）——只有真浏览器 + 真后端才能回答的那几件：
 *
 *   * 从项目选择器的「用示例了解 Tavotto」开始，**每一步都由真实动作完成**：
 *     双击素材卡进快速编辑、选标题、改字号、从「问题」定位那条 7 pt、开导出面板
 *     确认原图、加入画布、Shift 多选 + 顶对齐、确认画布导出；
 *   * coachmark 贴着真实锚点、没有遮罩、导出面板开着时它在面板里（模态对话框外面
 *     的东西点不到）；
 *   * 刷新页面回到同一步；Esc 暂停后刷新不再出现；「更多」菜单里继续；
 *   * 「重新开始教程」把画布恢复原样、onboarding 从头；
 *   * axe 无 critical / serious。
 *
 * 全程不联网（后端本地、教程资源在包内）。
 */

const coachmark = (page: Page) => page.locator('[data-onboarding-coachmark]')

async function openTutorialFromPicker(page: Page, baseURL: string) {
  await page.goto(baseURL)
  await page.getByRole('button', { name: '用示例了解 Tavotto' }).click()
  // 工作台起来 + 欢迎页
  await expect(coachmark(page)).toBeVisible({ timeout: 60_000 })
  await expect(coachmark(page)).toContainText('用示例了解 Tavotto')
}

test('完整走完教程：每一步都由真实动作完成', async ({ app, page }) => {
  test.setTimeout(300_000)
  const a = await app({ noProject: true })
  await page.setViewportSize({ width: 1400, height: 900 })
  await openTutorialFromPicker(page, a.baseURL)

  // 没有全屏遮罩：coachmark 后面的界面照常可点
  expect(await page.locator('[data-onboarding-mask]').count()).toBe(0)
  await coachmark(page).getByRole('button', { name: '开始' }).click()

  // ---- Step 1：打开一张图（左侧素材抽屉默认开着 → 锚点是那张卡片） ----
  await expect(coachmark(page)).toContainText('打开一张图')
  await expect(page.locator('[data-onboarding-ring]')).toBeVisible()
  await page.locator('[data-card="Fig2_correlation.pdf"]').dblclick()
  // 两条分开判，超时报文才说得出**停在哪一步**（issue #267）：
  //   第一条红 = 双击根本没进图内编辑（素材→脚本关联迟到时那次进入被丢掉）；
  //   第二条红 = 进去了，但那一版矢量渲染没回来。
  // 合成一条时两种都只报 "element(s) not found"，得下载 trace 才分得开——
  // 而它恰恰是在合并队列里踢 PR 的那一条。
  //
  // **两条共用同一个 90 s 截止期。** 顺序执行的两个 timeout 会相加：
  // 30 s + 90 s 意味着实际截止期变成最多 120 s——数字一个没改，判据却松了
  // （25 s 进编辑态 + 80 s 渲染在旧断言下是红的，在"两个独立超时"下是绿的）。
  // 诊断性拆分不该顺带买来 30 秒预算。
  const deadline = Date.now() + 90_000
  // **下限 1 而不是 0**：Playwright 把 `timeout: 0` 读成"不设超时"，
  // 预算耗尽反而变成永不超时——那是本次修复要挡的事情的反面
  const left = () => Math.max(1, deadline - Date.now())
  await expect(
    page.locator('[data-exit-element-edit]'),
    '双击素材卡之后没有进入图内编辑',
  ).toBeVisible({ timeout: Math.min(30_000, left()) })
  await expect(
    page.locator('[data-element-svg]').first().locator('svg'),
    '进了图内编辑，但这一版的矢量渲染没回来',
  ).toBeVisible({ timeout: left() })

  // ---- Step 2：选一个文字（高亮环套着图里的标题；点它的中心 = 真实选中） ----
  await expect(coachmark(page)).toContainText('选一个文字')
  const ring = page.locator('[data-onboarding-ring]')
  await expect(ring).toBeVisible()
  const rb = (await ring.boundingBox())!
  await page.mouse.click(rb.x + rb.width / 2, rb.y + rb.height / 2)

  // ---- Step 3：改字号（右侧属性里的真实输入框；锚点就是那一行） ----
  await expect(coachmark(page)).toContainText('改字号或字体')
  const inspector = page.getByLabel('右侧面板', { exact: true })
  const size = inspector.getByRole('textbox', { name: '字号' })
  await size.fill('12')
  await size.press('Enter')

  // ---- Step 4：从「问题」定位（那条故意的 7 pt） ----
  await expect(coachmark(page)).toContainText('从「问题」定位')
  await page.locator('[data-rail="problems"]').click()
  // 那条故意的 7 pt 在第二张图（p2）上；两张图都渲染过，问题面板里两张图的问题都在
  const row = page
    .locator('[data-issue-row][data-issue-rule="font-below-absolute-floor"][data-issue-object="p2"]')
    .first()
  await expect(row).toBeVisible({ timeout: 30_000 })
  await expect(coachmark(page)).toContainText('点这一条问题')
  await row.click()

  // ---- Step 5：原图导出（面板开着时 coachmark 在面板里） ----
  await expect(coachmark(page)).toContainText('看看原图导出')
  await page.getByRole('button', { name: '导出', exact: true }).click()
  // coachmark 自己也是 role=dialog（非模态）：导出面板要按「不是 coachmark 的那个」找
  const dialog = page.locator('[role="dialog"]:not([data-onboarding-coachmark])').first()
  await expect(dialog).toBeVisible()
  await expect(dialog.locator('[data-onboarding-coachmark]')).toBeVisible()
  await dialog.getByRole('radio', { name: '原图尺寸' }).click()
  await expect(dialog.getByRole('radio', { name: '原图尺寸' })).toHaveAttribute('aria-checked', 'true')
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)

  // ---- Step 6：加入画布（回到版面） ----
  await expect(coachmark(page)).toContainText('加入画布')
  await page.locator('[data-onboarding-anchor="add-to-layout"]').click()

  // ---- Step 7：Shift 多选 + 顶对齐 ----
  await expect(coachmark(page)).toContainText('Shift 多选')
  // 引导层会把被平移到工作区外的那张图挪回视野（只动视口）；等它真的进了画布区再点
  const stage = page.locator('[data-canvas-stage]')
  await expect(async () => {
    const s = (await stage.boundingBox())!
    const b = (await page.locator('[data-object-id="p1"]').boundingBox())!
    expect(b.x).toBeGreaterThanOrEqual(s.x - 1)
    expect(b.x + b.width).toBeLessThanOrEqual(s.x + s.width + 1)
  }).toPass({ timeout: 10_000 })
  await page.locator('[data-object-id="p1"]').click({ modifiers: ['Shift'] })
  await expect(coachmark(page)).toContainText('对齐')
  const bar = page.locator('[data-multi-selection-context-bar]')
  await expect(bar).toBeVisible()
  await bar.locator('[data-align-mode="top"]').click()

  // ---- Step 8：画布导出 ----
  await expect(coachmark(page)).toContainText('看看画布导出')
  await page.getByRole('button', { name: '导出', exact: true }).click()
  await expect(dialog).toBeVisible()
  await dialog.getByRole('radio', { name: '当前画布' }).click()
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)

  // ---- 完成 ----
  await expect(coachmark(page)).toContainText('教程完成')
  await coachmark(page).getByRole('button', { name: '继续探索' }).click()
  await expect(coachmark(page)).toHaveCount(0)
  // 状态落在本机：完成
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('tavotto.onboarding') ?? '{}').status)).toBe(
    'completed',
  )
})

test('刷新回到同一步；Esc 暂停后刷新不出现；「更多」菜单里继续；axe 无 critical/serious', async ({
  app,
  page,
}) => {
  test.setTimeout(240_000)
  const a = await app({ noProject: true })
  await page.setViewportSize({ width: 1400, height: 900 })
  await openTutorialFromPicker(page, a.baseURL)
  // 读屏：进度 / 标题在 aria-live 区里；coachmark 是非模态 dialog
  await expect(coachmark(page)).toHaveAttribute('aria-modal', 'false')
  await coachmark(page).getByRole('button', { name: '开始' }).click()
  await expect(coachmark(page)).toContainText('打开一张图')

  // 键盘：Tab 顺序能到返回 / 跳过 / 暂停
  await coachmark(page).focus()
  await page.keyboard.press('Tab')
  expect(await page.evaluate(() => 'onboardingBack' in (document.activeElement as HTMLElement).dataset)).toBe(true)
  await page.keyboard.press('Tab')
  expect(await page.evaluate(() => 'onboardingSkip' in (document.activeElement as HTMLElement).dataset)).toBe(true)
  await page.keyboard.press('Tab')
  expect(await page.evaluate(() => (document.activeElement as HTMLElement)?.getAttribute('aria-label'))).toBe('暂停教程')

  // axe：无 critical / serious。`color-contrast` 交给仓库自己的尺子（`e2e/contrast.ts`）：
  // 高亮环盖在锚点上时 axe 算不出被覆盖文字的背景色（issue #130 同一形状），
  // 它给的结论不是「不达标」而是「没测到」
  const axe = await new AxeBuilder({ page }).analyze()
  const bad = axe.violations.filter(
    (v) => (v.impact === 'critical' || v.impact === 'serious') && v.id !== 'color-contrast',
  )
  expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([])
  expect(await lowContrastNodes(page, '[data-onboarding-coachmark]')).toEqual([])
  // 高亮环套着的那张素材卡：环是透明底的边框，不该改变卡片文字的对比度结论
  expect(await lowContrastNodes(page, '[data-card="Fig2_correlation.pdf"]')).toEqual([])

  // 重启（刷新）：仍在第一步
  await page.reload()
  await expect(coachmark(page)).toBeVisible({ timeout: 60_000 })
  await expect(coachmark(page)).toContainText('打开一张图')

  // Esc（焦点在 coachmark 里）= 暂停；刷新后不再出现
  await coachmark(page).focus()
  await page.keyboard.press('Escape')
  await expect(coachmark(page)).toHaveCount(0)
  await page.reload()
  await expect(page.getByRole('button', { name: '导出', exact: true })).toBeVisible({ timeout: 60_000 })
  await page.waitForTimeout(800)
  expect(await coachmark(page).count()).toBe(0)

  // 「更多」→ 继续教程
  await page.getByRole('button', { name: '更多', exact: true }).click()
  await page.getByRole('menuitem', { name: '继续教程' }).click()
  await expect(coachmark(page)).toContainText('打开一张图')
})

test('重新开始教程：画布恢复原样、onboarding 从头；最近列表里带「教程项目」标记', async ({
  app,
  page,
}) => {
  test.setTimeout(240_000)
  const a = await app({ noProject: true })
  await page.setViewportSize({ width: 1400, height: 900 })
  await openTutorialFromPicker(page, a.baseURL)
  await coachmark(page).getByRole('button', { name: '开始' }).click()
  // 先动一下画布：把第一张图拖走。判据是**图在页面上的相对位置**（与缩放 / 平移无关），
  // 不是屏幕像素：重置前后视口会重新适配
  const fracX = async () => {
    const sheet = (await page.locator('[data-page-sheet]').boundingBox())!
    const box = (await page.locator('[data-object-id="p1"]').boundingBox())!
    return (box.x - sheet.x) / sheet.width
  }
  // 先「适应画布」：两张图都完整进工作区，拖动才落在图上而不是抽屉的拖拽把手上
  await page.getByRole('button', { name: '适应画布' }).click()
  await page.waitForTimeout(400)
  const p1 = page.locator('[data-object-id="p1"]')
  const before = (await p1.boundingBox())!
  const frac0 = await fracX()
  await page.mouse.move(before.x + before.width / 2, before.y + before.height / 2)
  await page.mouse.down()
  await page.mouse.move(before.x + before.width / 2 + 120, before.y + before.height / 2 + 40, { steps: 10 })
  await page.mouse.up()
  await page.waitForTimeout(1500) // 自动保存防抖：让改动真的落盘，重置才有东西可恢复
  expect((await fracX()) - frac0).toBeGreaterThan(0.05)

  // ⌘K → 重新开始教程 → 确认
  await page.keyboard.press('ControlOrMeta+k')
  await page.getByRole('option', { name: '重新开始教程' }).click()
  const confirm = page.getByRole('dialog').filter({ hasText: '重新开始教程？' })
  await expect(confirm).toBeVisible()
  await confirm.getByRole('button', { name: '重新开始' }).click()
  await expect(coachmark(page)).toContainText('用示例了解 Tavotto', { timeout: 60_000 })
  await page.waitForTimeout(500)
  expect(Math.abs((await fracX()) - frac0)).toBeLessThan(0.01)

  // 项目选择器里教程副本显示「教程项目」而不是数据目录路径
  await page.getByRole('button', { name: /当前项目 Tutorial/ }).click()
  await page.getByRole('menuitem', { name: '全部项目…' }).click()
  const row = page.getByRole('button', { name: '打开项目 Tutorial' })
  await expect(row).toBeVisible()
  await expect(row).toContainText('教程项目')
  await expect(row).not.toContainText(a.dataDir)
})

test('切到别的项目自动暂停，切回来自动继续', async ({ app, page }) => {
  test.setTimeout(240_000)
  const a = await app()
  await page.setViewportSize({ width: 1400, height: 900 })
  await page.goto(a.baseURL)
  await expect(page.getByText('Fig1_kinetics.pdf')).toBeVisible({ timeout: 30_000 })
  // 从「更多」开始教程（在别的项目里也能进）
  await page.getByRole('button', { name: '更多', exact: true }).click()
  await page.getByRole('menuitem', { name: '开始教程' }).click()
  await expect(coachmark(page)).toBeVisible({ timeout: 60_000 })
  await coachmark(page).getByRole('button', { name: '开始' }).click()
  await expect(coachmark(page)).toContainText('打开一张图')

  // 切回原来的项目 → coachmark 消失（系统暂停）
  await page.getByRole('button', { name: /当前项目 Tutorial/ }).click()
  await page.getByRole('menuitem', { name: 'figures' }).click()
  await expect(page.getByRole('button', { name: /当前项目 figures/ })).toBeVisible({ timeout: 30_000 })
  await expect(coachmark(page)).toHaveCount(0)
  // 再切回教程 → 自动继续
  await page.getByRole('button', { name: /当前项目 figures/ }).click()
  await page.getByRole('menuitem', { name: 'Tutorial' }).click()
  await expect(coachmark(page)).toContainText('打开一张图', { timeout: 60_000 })
})
