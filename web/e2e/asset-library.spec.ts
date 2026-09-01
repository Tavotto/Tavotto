import { existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, openElementsTab, test, type RunningApp } from './fixtures'
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

/** 展开元素树直到目标 treeitem 可见（树是异步填充的，固定次数的展开
 *  循环会在「树还没长全」时空转——这里以目标可见为准，带总限时）。 */
async function expandTreeUntil(page: Page, name: RegExp, timeoutMs = 60_000) {
  const target = page.getByRole('treeitem', { name }).first()
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (await target.count()) {
      if (await target.isVisible()) return target
    }
    const g = page.locator('[role="treeitem"][aria-expanded="false"]').first()
    if (await g.count()) await g.click()
    await page.waitForTimeout(150)
  }
  return target // 交给调用方的 expect 报出可读的失败
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

  // 打开（双击卡片 = 主动作）。Prompt 09 起它落在快速编辑工作区，
  // **当场就在图内编辑态**——不再需要先加入画布再点一次「编辑图内元素」。
  await card.dblclick()
  await expect(page.getByText('画布是空的')).toHaveCount(0)
  await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 120_000 })

  // 左侧元素树 → 标题 → 改字号
  await openElementsTab(page)
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
  // 13 而不是 12：标题初值就是 12.0pt（axes.titlesize=large），同值提交被
  // NumberField 的 no-op 拦截（#109）如实吞掉——编辑必须真的改值。
  await size.fill('13')
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

/**
 * Session 6 完整链（真实后端）：
 * 打开无 savefig 的旧项目 → 素材库运行发现 → 加画布 → 对象级编辑（标题
 * 字号 + 曲线线宽）→ undo/redo → 保存（磁盘自动保存）→ **关闭 App** →
 * 重开（同端口 + 同数据目录 = 同一台机器上的重启）→ lazy rehydrate +
 * 重新运行 → override 恢复 → 出版预检 → 导出 PDF + PNG（同一权威状态：
 * 两者由同一次合成产出，runtime 面板由当次 live worker 渲染）。
 */
test('完整链：保存 → 关闭 → 重开 → 重放 → 预检 → 导出 PDF/PNG', async ({ app, page }) => {
  test.setTimeout(900_000)
  const dir = path.join(os.tmpdir(), `tavotto-e2e-chain-${Date.now()}`)
  writeShowOnlyProject(dir)
  // 「关闭再重开」必须落在同一份用户数据上：共享 data/config/home。
  // 端口随机（钉同一个端口会撞上 TIME_WAIT——resolve_port 判占用后换端口，
  // 第二个实例就永远不在期望的地址上）；localStorage 的恢复索引按 origin
  // 存，跨实例用 addInitScript 原样带过去（真实浏览器重启时 origin 不变，
  // 索引本来就在）。
  const shared = mkdtempSync(path.join(os.tmpdir(), 'tavotto-e2e-user-'))
  const env = {
    TAVOTTO_DATA_DIR: path.join(shared, 'data'),
    TAVOTTO_CONFIG_DIR: path.join(shared, 'config'),
    HOME: path.join(shared, 'home'),
    USERPROFILE: path.join(shared, 'home'),
  }
  mkdirSync(env.HOME, { recursive: true })

  const a1 = await app({ figures: dir, env })
  await runAndDiscover(page, a1)
  const card = page.locator('[data-card="runtime:show_only.py#show_only"]')
  await expect(card).toBeVisible({ timeout: 30_000 })
  await card.dblclick()
  await expect(page.getByText('画布是空的')).toHaveCount(0)

  // 对象级编辑：标题字号 + 曲线线宽（与黄金路径同一套控件）。
  // Prompt 09：双击卡片已经进了图内编辑态
  await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 120_000 })
  await openElementsTab(page)
  const panel = page.getByLabel('右侧面板', { exact: true })
  await (await expandTreeUntil(page, /^标题/)).click()
  const size = panel.getByRole('textbox', { name: '字号' })
  // 13：初值 12.0，同值提交是 no-op（见上一条用例的注释）
  await size.fill('13')
  await size.press('Enter')
  await expect(panel.getByText('1 项已修改')).toBeVisible({ timeout: 30_000 })
  await (await expandTreeUntil(page, /^曲线/)).click()
  const width = page
    .getByRole('toolbar', { name: '快速编辑' })
    .getByRole('textbox', { name: '线宽' })
  await width.fill('3')
  await width.press('Enter')
  await expect(panel.getByText('1 项已修改')).toBeVisible({ timeout: 30_000 })
  // undo/redo 一轮（结束时两条编辑都在）
  await page.getByRole('button', { name: '撤销' }).click()
  await expect(panel.getByText(/项已修改/)).toHaveCount(0)
  await page.getByRole('button', { name: '重做' }).click()
  await expect(panel.getByText('1 项已修改')).toBeVisible()

  // reload = beforeunload 冲刷自动保存；顺带验证同实例内的恢复
  await page.reload()
  await expect(page.getByText('画布是空的')).toHaveCount(0, { timeout: 30_000 })
  // 恢复索引在 localStorage（按 origin）：重启后端口会变，索引要原样带走
  const stored: Record<string, string> = await page.evaluate(() =>
    Object.fromEntries(
      Object.entries(localStorage).filter(([k]) => k.startsWith('tavotto')),
    ),
  )
  await a1.stop()

  // 「保存文档」的证据在磁盘上：自动保存文件里 runtime 面板带着两条 override
  const autosaveDir = path.join(env.TAVOTTO_DATA_DIR, 'layouts', '_autosave')
  const saved = readdirSync(autosaveDir).filter((f) => f.endsWith('.json'))
  expect(saved.length).toBeGreaterThan(0)
  const pd = JSON.parse(readFileSync(path.join(autosaveDir, saved[0]), 'utf-8'))
  const objects = pd.canvases[0].objects as {
    type: string
    fileId?: string
    x: number; y: number; w: number; h: number
    overrides?: { gid: string; prop: string; value: unknown }[]
  }[]
  const rt = objects.find((o) => o.type === 'panel' && o.fileId?.startsWith('runtime:'))!
  expect(rt).toBeTruthy()
  const props = (rt.overrides ?? []).map((o) => `${o.gid}.${o.prop}`)
  expect(props).toContain('axes_0.title.fontsize')
  expect(props).toEqual(expect.arrayContaining([expect.stringMatching(/linewidth$/)]))

  // 重开：同数据目录（自动保存与 runtime cache 都在里面）。文档从磁盘
  // 自动保存恢复，runtime 面板先用 materialized cache 占位（零执行），
  // 进入编辑才重新运行（lazy 门）。
  const a2 = await app({ figures: dir, env })
  await page.addInitScript((entries: Record<string, string>) => {
    for (const [k, v] of Object.entries(entries)) localStorage.setItem(k, v)
  }, stored)
  await page.goto(a2.baseURL)
  await expect(page.getByText('画布是空的')).toHaveCount(0, { timeout: 30_000 })
  const obj = page.locator('[data-object-id]').first()
  await expect(obj).toBeVisible()

  // lazy rehydrate → 重新运行 → override 恢复（字号 13 还在）。
  // **这一条走的是画布上的对象**（重开项目后从版上点进去），不是素材卡——
  // 那条路径 Prompt 09 一个字没改，右栏入口照旧。
  await obj.click()
  await page.getByRole('button', { name: '编辑图内元素' }).first().click()
  await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 120_000 })
  await openElementsTab(page)
  await (await expandTreeUntil(page, /^标题/)).click()
  const panel2 = page.getByLabel('右侧面板', { exact: true })
  await expect(panel2.getByText('1 项已修改')).toBeVisible({ timeout: 30_000 })
  await expect(panel2.getByRole('textbox', { name: '字号' })).toHaveValue('13')

  // 出版预检（导出面板只给摘要，完整清单在左侧问题面板 —— ADR 0031 §四）
  await page.getByRole('button', { name: '导出' }).first().click()
  await expect(
    page.getByText(/导出前检查通过|\d+ (阻断|警告|建议|无法核验)/),
  ).toBeVisible({ timeout: 30_000 })
  await page.keyboard.press('Escape')

  // 导出 PDF + PNG：同一次合成（PNG 由同一份 PDF 渲染），runtime 面板由
  // 当次 live worker 按 override 渲染——绝不拿 cache 旧文件冒充
  const resp = await page.request.post(`${a2.baseURL}/api/export`, {
    data: {
      page_w_mm: 120,
      page_h_mm: 90,
      formats: ['pdf', 'png'],
      stem: 'chain_e2e',
      objects: [
        {
          type: 'panel',
          id: rt.fileId,
          x_mm: 5,
          y_mm: 5,
          w_mm: 100,
          h_mm: 75,
          overrides: rt.overrides ?? [],
        },
      ],
    },
    timeout: 300_000,
  })
  expect(resp.ok()).toBe(true)
  const out = await resp.json()
  expect(out.warnings ?? []).toEqual([])
  expect(out.files).toHaveLength(2)
  for (const f of out.files as { name: string }[]) {
    const p = path.join(out.export_dir, f.name)
    expect(existsSync(p)).toBe(true)
    expect(statSync(p).size).toBeGreaterThan(900)
  }
})

/** 多 Figure 交接：全部可见、选第二张、stem/asset id 不串。 */
test('多 Figure：?pick= 打开选择器，选第二张加的就是第二张', async ({ app, page }) => {
  const dir = path.join(os.tmpdir(), `tavotto-e2e-multi-${Date.now()}`)
  mkdirSync(dir, { recursive: true })
  writeFileSync(
    path.join(dir, 'show_two.py'),
    [
      'import matplotlib',
      'matplotlib.use("Agg")',
      'import matplotlib.pyplot as plt',
      '',
      'plt.figure()',
      'plt.plot([1, 2], [3, 4])',
      'plt.title("First")',
      'plt.figure()',
      'plt.plot([2, 1], [4, 3])',
      'plt.title("Second")',
      'plt.show()',
      '',
    ].join('\n'),
    'utf-8',
  )
  writeFileSync(path.join(dir, 'tavotto_registry.json'),
                JSON.stringify({ version: 1, scripts: {} }), 'utf-8')
  const a = await app({ figures: dir })

  // CLI 的 safe probe 与素材库按的是同一个端点：先经真实端点登记两张图
  const probe = await page.request.post(`${a.baseURL}/api/registry/probe`, {
    data: { script: 'show_two.py' },
    timeout: 300_000,
  })
  expect(probe.ok()).toBe(true)
  const stems = (await probe.json()).stems as string[]
  expect(stems).toHaveLength(2)

  // `tavotto open` 多图交接的落地形态：?pick=<脚本> → Figure 选择器
  await page.goto(`${a.baseURL}/?pick=show_two.py`)
  const dialog = page.getByRole('dialog', { name: /选择一张图/ })
  await expect(dialog).toBeVisible({ timeout: 30_000 })
  const rows = dialog.getByRole('listitem')
  await expect(rows).toHaveCount(2)

  // 选第二张：加的必须是第二张（stem/asset id 不串）
  const secondStem = (await rows.nth(1).textContent())!.match(/[\w.-]+/)![0]
  await rows.nth(1).getByRole('button', { name: '添加到画布' }).click()
  await expect(dialog).toHaveCount(0)
  await expect(page.getByText('画布是空的')).toHaveCount(0)

  // 冲刷自动保存后读磁盘：面板的 fileId 指向第二张的 asset id
  await page.reload()
  await expect(page.getByText('画布是空的')).toHaveCount(0, { timeout: 30_000 })
  const autosaveDir = path.join(a.dataDir, 'layouts', '_autosave')
  const saved = readdirSync(autosaveDir).filter((f) => f.endsWith('.json'))
  const pd = JSON.parse(readFileSync(path.join(autosaveDir, saved[0]), 'utf-8'))
  const panels = pd.canvases[0].objects.filter((o: { type: string }) => o.type === 'panel')
  expect(panels).toHaveLength(1)
  expect(panels[0].fileId).toBe(`runtime:show_two.py#${stems[1]}`)
  expect(panels[0].fileId).toContain(secondStem)
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
