import { expect, test, type RunningApp } from './fixtures'
import type { Page } from '@playwright/test'

/**
 * 桌面端 UX 一致性修复的黄金路径（真浏览器 + 真引擎渲染，
 * `examples/figures` 的 Fig1_kinetics，不是手写 manifest）。
 *
 *   A. 统一改图中文字：多选图标题 + X/Y 轴标题 → 改字体、字号、加粗 →
 *      三个目标一起变 → 撤销一次全回来 → 重做一次全变回去；
 *   B. 刻度方向与次刻度：选中子图 → 开上/右刻度 → X 朝内、Y 内外 → 开 X 次刻度
 *      → 示意图状态跟着变 → 等真实渲染 → 撤销重做；
 *   C. AI 配置：模型 / 键盘调强度 / 关掉重开偏好还在 / 无横向溢出；
 *   D. 设置渐进披露：无文字墙 / 键盘聚焦问号 / Esc / 展开环境诊断才见完整路径。
 *
 * 断言全部打在「结构与行为」上，不打在某台机器上装没装 codex/claude。
 */

async function openFigure(page: Page, a: RunningApp) {
  await page.goto(a.baseURL)
  // Prompt 09 起，双击素材卡 = 打开这张图（快速编辑工作区），**当场就在图内
  // 编辑态**——不再需要先「加入画布」再点一次「编辑图内元素」。
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 60_000 })
}

/** 打开左侧元素树并展开全部分组（分组头点行即展开，元素行点行首小三角） */
async function openTree(page: Page) {
  await page.getByRole('navigation').getByRole('button', { name: '图内元素' }).click()
  await page.locator('[role="treeitem"]').first().waitFor({ timeout: 30_000 })
  for (let i = 0; i < 20; i++) {
    const rows = page.locator('[role="treeitem"][aria-expanded="false"]')
    if (!(await rows.count())) break
    const row = rows.first()
    const chevron = row.getByRole('button', { name: '展开', exact: true })
    try {
      if (await chevron.count()) await chevron.first().click({ timeout: 4000 })
      else await row.click({ timeout: 4000 })
    } catch {
      break
    }
    await page.waitForTimeout(120)
  }
}

const inspector = (page: Page) => page.getByLabel('右侧面板', { exact: true })

/**
 * 把布局撑破的元素（真布局才量得出来）。只认 `overflow-x: visible` 的那些：
 * 裁切（truncate 是 hidden + 省略号）与有意可滚的容器本来就不该算撑破。
 */
async function horizontalOffenders(page: Page, rootSel: string): Promise<string[]> {
  return page.evaluate((sel) => {
    const rootEl = document.querySelector(sel)
    if (!rootEl) return ['NO ROOT: ' + sel]
    const out: string[] = []
    for (const el of [rootEl, ...Array.from(rootEl.querySelectorAll('*'))]) {
      const e = el as HTMLElement
      if (getComputedStyle(e).overflowX !== 'visible') continue
      if (e.scrollWidth > e.clientWidth + 1) {
        out.push(
          `${e.tagName}.${String(e.className).slice(0, 60)} sw=${e.scrollWidth} cw=${e.clientWidth}`,
        )
      }
    }
    return out
  }, rootSel)
}

/* ============================ 流程 A：统一图中文字 ========================== */

test('流程 A：图标题 + X/Y 轴标题一起改字体 / 字号 / 加粗，撤销重做全组一致', async ({
  app,
  page,
}) => {
  const a = await app()
  await openFigure(page, a)
  await openTree(page)

  await page.getByRole('treeitem', { name: /^标题/ }).first().click()
  await page.getByRole('treeitem', { name: /^X 轴/ }).first().click({ modifiers: ['Shift'] })
  await page.getByRole('treeitem', { name: /^Y 轴/ }).first().click({ modifiers: ['Shift'] })

  const panel = inspector(page)
  // 公共样式区出现（角色不同也能一起改——这正是修改前做不到的）
  await expect(panel.getByText(/个文字元素的公共样式/)).toBeVisible({ timeout: 15_000 })
  // 对齐工具同时在：样式与几何互不排斥
  await expect(panel.getByText('已选 3 个元素')).toBeVisible()

  const size = panel.getByRole('textbox', { name: '字号' })
  // Fig1_kinetics 里三条文字的字号本来就一样，先把标题单独改掉造出 mixed
  await page.getByRole('treeitem', { name: /^标题/ }).first().click()
  await panel.getByRole('textbox', { name: '字号' }).fill('12')
  await panel.getByRole('textbox', { name: '字号' }).press('Enter')
  await expect(panel.getByRole('textbox', { name: '字号' })).toHaveValue('12', { timeout: 15_000 })

  await page.getByRole('treeitem', { name: /^X 轴/ }).first().click({ modifiers: ['Shift'] })
  await page.getByRole('treeitem', { name: /^Y 轴/ }).first().click({ modifiers: ['Shift'] })
  // --- mixed：输入框留空 + 「多个值」占位，绝不谎报某一个的字号 ---
  await expect(size).toHaveValue('', { timeout: 15_000 })
  await expect(size).toHaveAttribute('placeholder', '多个值')

  // --- 字号：三个目标一起变 ---
  await size.fill('13')
  await size.press('Enter')
  await expect(size).toHaveValue('13', { timeout: 15_000 })

  // --- 加粗：B 图标（不是「常规 / 加粗」下拉） ---
  const bold = panel.getByRole('button', { name: /^加粗/ })
  await expect(bold).toBeVisible()
  await bold.click()
  await expect(bold).toHaveAttribute('aria-pressed', 'true', { timeout: 15_000 })

  // --- 字体：选一个新字体 ---
  await panel.getByRole('combobox', { name: '字体' }).click()
  await page.getByRole('option', { name: /无衬线|sans/i }).first().click()
  await page.waitForTimeout(600)

  // 三个目标都被改过：头部计数说 3 项（每个元素各自算）
  // 逐个选中确认，比数字更硬
  for (const name of [/^标题/, /^X 轴/, /^Y 轴/]) {
    await page.getByRole('treeitem', { name }).first().click()
    await expect(panel.getByRole('textbox', { name: '字号' })).toHaveValue('13', {
      timeout: 15_000,
    })
    await expect(panel.getByRole('button', { name: /^加粗/ })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
  }

  // --- 撤销：一次点击 = 一条历史 ---
  const undo = page.getByRole('button', { name: '撤销' })
  const redo = page.getByRole('button', { name: '重做' })
  await undo.click() // 字体
  await undo.click() // 加粗
  await page.getByRole('treeitem', { name: /^标题/ }).first().click()
  await expect(panel.getByRole('button', { name: /^加粗/ })).toHaveAttribute(
    'aria-pressed',
    'false',
    { timeout: 15_000 },
  )
  await undo.click() // 批量字号 → 回到「标题 12、轴标题各自原值」
  await expect(panel.getByRole('textbox', { name: '字号' })).toHaveValue('12', { timeout: 15_000 })

  // --- 重做：全部回到新值 ---
  await redo.click()
  await redo.click()
  await redo.click()
  for (const name of [/^标题/, /^X 轴/, /^Y 轴/]) {
    await page.getByRole('treeitem', { name }).first().click()
    await expect(panel.getByRole('textbox', { name: '字号' })).toHaveValue('13', {
      timeout: 15_000,
    })
  }
})

/* ========================= 流程 B：刻度方向与次刻度 ========================= */

test('流程 B：选中子图即可设四边刻度、方向与次刻度，示意图跟着真实状态变', async ({
  app,
  page,
}) => {
  const a = await app()
  await openFigure(page, a)
  await openTree(page)
  await page.getByRole('treeitem', { name: /^子图 1/ }).first().click()

  const panel = inspector(page)
  await expect(panel.getByText('次刻度', { exact: true })).toBeVisible({ timeout: 15_000 })
  await expect(panel.getByText('方向', { exact: true })).toBeVisible()

  // --- 开上边与右边刻度（四边点按这条既有交互必须还在）---
  await panel.getByRole('switch', { name: '上边刻度线' }).click()
  await expect(panel.getByRole('switch', { name: '上边刻度线' })).toHaveAttribute(
    'aria-checked',
    'true',
    { timeout: 15_000 },
  )
  await panel.getByRole('switch', { name: '右边刻度线' }).click()
  await expect(panel.getByRole('switch', { name: '右边刻度线' })).toHaveAttribute(
    'aria-checked',
    'true',
    { timeout: 15_000 },
  )

  const majorBottom = panel.locator('[data-tick-major="bottom"]')
  const majorLeft = panel.locator('[data-tick-major="left"]')

  // --- 示意图读的是真实状态 ---
  // paper_style.py 里 xtick.direction = "in"：**这张图本来就是朝内的**，
  // 而修改前的示意图把刻度画死在框外（before/zh-1440-axes-ticks.png）。
  // 起手就该是 in——这一条本身就是那个缺陷的真数据反例。
  await expect(majorBottom).toHaveAttribute('data-tick-direction', 'in', { timeout: 15_000 })
  // 三档都要能带动示意图
  await panel.getByRole('radio', { name: '朝外' }).click()
  await expect(majorBottom).toHaveAttribute('data-tick-direction', 'out', { timeout: 15_000 })
  await panel.getByRole('radio', { name: '朝内' }).click()
  await expect(majorBottom).toHaveAttribute('data-tick-direction', 'in', { timeout: 15_000 })

  // --- 切到 Y 刻度，设成内外 ---
  await panel.getByRole('radio', { name: 'Y 刻度' }).click()
  await panel.getByRole('radio', { name: '内外' }).click()
  await expect(majorLeft).toHaveAttribute('data-tick-direction', 'inout', { timeout: 15_000 })
  // X 不受影响（两个轴各写各的 ticks 元素）
  await expect(majorBottom).toHaveAttribute('data-tick-direction', 'in')

  // --- 开 X 次刻度：示意图上出现更短的次刻度 ---
  await panel.getByRole('radio', { name: 'X 刻度' }).click()
  await expect(panel.locator('[data-tick-minor="bottom"]')).toHaveCount(0)
  await panel.getByRole('switch', { name: 'X 轴的次刻度' }).click()
  await expect(panel.locator('[data-tick-minor="bottom"]')).toHaveCount(1, { timeout: 15_000 })

  // --- 等真实渲染定稿：画布上的图确实被重画过 ---
  await expect(page.locator('[data-element-svg] svg').first()).toBeVisible()
  await page.waitForTimeout(2500)

  // --- 撤销 / 重做 ---
  const undo = page.getByRole('button', { name: '撤销' })
  await undo.click()
  await expect(panel.locator('[data-tick-minor="bottom"]')).toHaveCount(0, { timeout: 15_000 })
  await page.getByRole('button', { name: '重做' }).click()
  await expect(panel.locator('[data-tick-minor="bottom"]')).toHaveCount(1, { timeout: 15_000 })
})

/* ============================== 流程 C：AI 配置 ============================= */

test('流程 C：AI 模型与推理强度——键盘可调、偏好保持、无横向溢出', async ({ app, page }) => {
  const a = await app()
  await openFigure(page, a)

  // 右栏切到改图助手
  await page.getByRole('button', { name: /改图助手/ }).first().click()
  const openPopover = async () => {
    await page.getByRole('button', { name: '作用范围与执行器' }).click()
    await expect(page.getByText('作用范围')).toBeVisible({ timeout: 15_000 })
  }
  await openPopover()

  const popover = page.locator('[data-radix-popper-content-wrapper]').first()

  // 正常状态不常驻实现说明（技术详情里才有）
  await expect(popover.getByText(/自动快照/)).toHaveCount(0)
  await expect(popover.getByRole('button', { name: '技术详情' })).toBeVisible()

  const slider = popover.getByRole('slider', { name: '推理强度' })
  const providerGroup = popover.getByRole('radiogroup', { name: '执行改动的命令行工具' })
  const recovery = popover.getByRole('button', { name: '打开编码 Agent 设置' })

  const hasSlider = (await slider.count()) > 0
  const providers = await providerGroup.count()

  if (!hasSlider && !providers && (await recovery.count())) {
    // 这台机器上一个可用 Agent 都没有：恢复入口必须在，且不摆一个死掉的双选
    await expect(recovery).toBeVisible()
  }
  // 只有一个 Provider 时不摆只有一项的「双选」
  if (providers) {
    const items = await providerGroup.getByRole('radio').count()
    expect(items).toBeGreaterThan(1)
  }

  if (hasSlider) {
    const before = await slider.getAttribute('aria-valuetext')
    // 键盘调节：方向键是原生 range 免费拿到的
    await slider.focus()
    await page.keyboard.press('ArrowLeft')
    await expect(slider).not.toHaveAttribute('aria-valuetext', before ?? '', { timeout: 10_000 })
    const after = await slider.getAttribute('aria-valuetext')

    // 关掉再打开：偏好还在
    await page.keyboard.press('Escape')
    await openPopover()
    await expect(
      page.locator('[data-radix-popper-content-wrapper]').first().getByRole('slider', { name: '推理强度' }),
    ).toHaveAttribute('aria-valuetext', after ?? '', { timeout: 10_000 })
  }

  // 弹层无横向溢出（真布局才量得出来；修改前六档按钮在这里两头被切掉）
  const offenders = await horizontalOffenders(page, '[data-radix-popper-content-wrapper]')
  expect(offenders).toEqual([])
})

/* ========================== 流程 D：设置渐进披露 =========================== */

test('流程 D：设置页没有文字墙，问号键盘可达、Esc 可关，完整路径只在诊断里', async ({
  app,
  page,
}) => {
  const a = await app()
  await page.goto(a.baseURL)
  await page.getByRole('button', { name: '设置', exact: true }).first().click()
  // 帮助气泡也是 role=dialog，按名字消歧
  const dialog = page.getByRole('dialog', { name: '设置' })
  await expect(dialog).toBeVisible({ timeout: 30_000 })

  /** 对话框正文里独立成段的长解释有几段 */
  const proseCount = async () =>
    dialog.evaluate(
      (d) =>
        [...d.querySelectorAll('p')].filter((p) => (p.textContent ?? '').trim().length >= 30).length,
    )

  for (const section of ['常规', '项目与路径', '画布与编辑', '侧栏行为', '导出默认值']) {
    await dialog.getByRole('navigation').getByRole('button', { name: section }).click()
    await page.waitForTimeout(250)
    expect(await proseCount(), `${section} 分区仍是文字墙`).toBeLessThanOrEqual(1)
    expect(await horizontalOffenders(page, '[role="dialog"][aria-labelledby]')).toEqual([])
  }

  // --- 问号：Tab 到它 → 展开 → Esc 收回 ---
  await dialog.getByRole('navigation').getByRole('button', { name: '常规' }).click()
  const help = dialog.getByRole('button', { name: '关于界面语言' })
  await expect(help).toHaveAttribute('aria-expanded', 'false')
  await help.focus()
  await expect(help).toHaveAttribute('aria-expanded', 'true', { timeout: 10_000 })
  await expect(page.getByText(/只影响界面文字/)).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(help).toHaveAttribute('aria-expanded', 'false', { timeout: 10_000 })
  // Esc 关的是气泡，不是整个设置对话框
  await expect(dialog).toBeVisible()

  // --- About：完整解释器路径只在「环境诊断」里 ---
  await dialog.getByRole('navigation').getByRole('button', { name: /隐私、诊断与 About/ }).click()
  await expect(dialog.getByText(/仅在你明确开启后发送匿名功能使用情况/)).toBeVisible()

  const absolutePath = /(^|[^\w])[/\\](?:usr|opt|home|Users|private|tmp)[/\\][^\s]{8,}/
  const firstScreen = (await dialog.textContent()) ?? ''
  expect(firstScreen).not.toMatch(absolutePath)

  const diag = dialog.getByRole('button', { name: '环境诊断' })
  await expect(diag).toHaveAttribute('aria-expanded', 'false')
  await diag.click()
  await expect(diag).toHaveAttribute('aria-expanded', 'true')
  await page.waitForTimeout(500)
  const expanded = (await dialog.textContent()) ?? ''
  expect(expanded).toMatch(absolutePath)
})
