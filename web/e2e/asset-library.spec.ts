import { copyFileSync, cpSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, openElementsTab, test, writeRuntimeNamedProject, type RunningApp } from './fixtures'
import type { Page } from '@playwright/test'
import { horizontalOffenders } from './overflow'

import { autosavePanelEvidence } from './autosaveEvidence'

// 这一组钉的是**旧**的同步试运行路径（T09 起它只在本地开关关闭时走；默认的准备面板路径见
// `preparation-card.spec.ts`）。开关是本机 localStorage 偏好，在页面脚本之前写好
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('tavotto.preparationPanel', 'off'))
})

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
  await expect(page.getByText('脚本尚未运行')).toBeVisible()

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

  // 编辑原图（双击卡片 = 主动作；T06 起与「添加到画布」分开）。Prompt 09 起它落在快速编辑工作区，
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
  // 这份夹具只有一个面板。对象身份来自实际画布；首次防抖前恢复索引可能尚不存在，
  // 也可能仍指向另一份排版，不能拿它猜当前文档的写盘槽位。
  const currentObject = page.locator('[data-canvas-stage] [data-object-id]')
  await expect(currentObject).toHaveCount(1)
  await expect(currentObject).toBeVisible()
  const panelId = await currentObject.getAttribute('data-object-id')
  expect(panelId).toBeTruthy()
  const saveTarget = {
    panelId: panelId!, fileId: 'runtime:show_only.py#show_only',
    requiredOverrides: [{ gid: 'axes_0.title', prop: 'fontsize' }, { prop: 'linewidth' }],
  }
  // 只认当前对象的两条最终编辑；文档 id 取这条真实 PUT 的路径，不读恢复索引。
  const savedEdits = page.waitForResponse((response) => autosavePanelEvidence(response, saveTarget) !== null)
  await page.getByRole('button', { name: '重做' }).click()
  await expect(panel.getByText('1 项已修改')).toBeVisible()
  // 未绑定项目文件的排版：正式保存先等本机写队列排空，才打开命名框。
  // 单独一个同内容的迟到 200 仍可能早于队列里更旧的正文，不能据此关页。
  await page.keyboard.press('ControlOrMeta+s')
  await expect(page.locator('[data-layout-save]')).toBeVisible()
  const written = await savedEdits
  expect(await written.finished()).toBeNull()
  await page.keyboard.press('Escape')
  await expect(page.locator('[data-layout-save]')).toBeHidden()
  const evidence = autosavePanelEvidence(written, saveTarget)
  expect(evidence).not.toBeNull()
  const { docId, panel: writtenPanel } = evidence!

  // 同实例恢复也必须等真实面板装好；空 root 同样没有「画布是空的」文案。
  await page.reload()
  await expect(page.locator(`[data-object-id="${writtenPanel.id}"]`)).toBeVisible({ timeout: 30_000 })
  // 恢复索引在 localStorage（按 origin）：重启后端口会变，索引要原样带走
  const stored: Record<string, string> = await page.evaluate(() =>
    Object.fromEntries(
      Object.entries(localStorage).filter(([k]) => k.startsWith('tavotto')),
    ),
  )
  await a1.stop()

  // 「保存文档」的证据在磁盘上：自动保存文件里 runtime 面板带着两条 override
  const autosaveDir = path.join(env.TAVOTTO_DATA_DIR, 'layouts', '_autosave')
  const pd = JSON.parse(readFileSync(path.join(autosaveDir, `${docId}.json`), 'utf-8'))
  const objects = pd.canvases[0].objects as {
    type: string
    fileId?: string
    x: number; y: number; w: number; h: number
    overrides?: { gid: string; prop: string; value: unknown }[]
  }[]
  const rt = objects.find((o) => o.type === 'panel' && o.fileId?.startsWith('runtime:'))!
  expect(rt).toBeTruthy()
  expect(rt.overrides).toEqual(writtenPanel.overrides)
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
  const obj = page.locator(`[data-object-id="${writtenPanel.id}"]`)
  await expect(obj).toBeVisible({ timeout: 30_000 })

  // lazy rehydrate → 重新运行 → override 恢复（字号 13 还在）。
  // **这一条走的是画布上的对象**（重开项目后从版上点进去），不是素材卡——
  // 那条路径 Prompt 09 一个字没改，右栏入口照旧。
  await obj.click()
  await page.getByRole('button', { name: '改图里的内容' }).first().click()
  await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 120_000 })
  await openElementsTab(page)
  await (await expandTreeUntil(page, /^标题/)).click()
  const panel2 = page.getByLabel('右侧面板', { exact: true })
  await expect(panel2.getByText('1 项已修改')).toBeVisible({ timeout: 30_000 })
  await expect(panel2.getByRole('textbox', { name: '字号' })).toHaveValue('13')

  // 出版预检（导出面板只给摘要，完整清单在左侧问题面板 —— ADR 0031 §四）
  await page.getByRole('button', { name: '导出' }).first().click()
  await expect(
    page.getByText(/按「[^」]+」未发现问题|\d+ (项要改|项留意|条建议|项无法核验)/),
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
  const dialog = page.locator('[data-dialog="figure-picker"]')
  await expect(dialog).toBeVisible({ timeout: 30_000 })
  const rows = dialog.locator('[data-figure-picker-row]')
  await expect(rows).toHaveCount(2)

  // 选第二张：加的必须是第二张（stem/asset id 不串）。加一张不关框（2026-10-07 设计审计 §10.2），「完成」才关
  const secondStem = (await rows.nth(1).getAttribute('data-figure-picker-row'))!
  await rows.nth(1).locator('[data-figure-picker-add]').click()
  await expect(rows.nth(1).locator('[data-figure-picker-placed]')).toBeVisible()
  await dialog.locator('[data-figure-picker-done]').click()
  await expect(dialog).toHaveCount(0)
  await expect(page.getByText('画布是空的')).toHaveCount(0)

  // 冲刷自动保存后读磁盘：面板的 fileId 指向第二张的 asset id。
  // 画布不空只证明客户端恢复了文档，服务端的自动保存是异步落盘的——
  // 目录可能还没建出来（2026-09-21 merge_group 的 windows-exe-smoke 两次 ENOENT），
  // 所以等到磁盘上出现 json 再读，而不是读到什么算什么。
  await page.reload()
  await expect(page.getByText('画布是空的')).toHaveCount(0, { timeout: 30_000 })
  const autosaveDir = path.join(a.dataDir, 'layouts', '_autosave')
  const savedJson = () =>
    existsSync(autosaveDir) ? readdirSync(autosaveDir).filter((f) => f.endsWith('.json')) : []
  await expect.poll(() => savedJson().length, { timeout: 30_000 }).toBeGreaterThan(0)
  const saved = savedJson()
  const pd = JSON.parse(readFileSync(path.join(autosaveDir, saved[0]), 'utf-8'))
  const panels = pd.canvases[0].objects.filter((o: { type: string }) => o.type === 'panel')
  expect(panels).toHaveLength(1)
  expect(panels[0].fileId).toBe(`runtime:show_two.py#${stems[1]}`)
  expect(panels[0].fileId).toContain(secondStem)
})

/**
 * 起会话之前的依赖门（Windows 真机验收，main 493a1310）：项目里只有脚本、还没有图，试运行 200 回来、
 * `error.code == dependency_preparation_required` 带整份载荷。以前这一行掉进「可能需要原环境」，出口只有
 * 「选择渲染环境 / 复制诊断」，新用户走不到安装。
 *
 * 门的那一次响应用 `page.route` 造（真后端要触发它得先让受管环境缺一个可装的包、授权后还要联网装），其余全走
 * 真后端：依赖门直接弹一键修复框（用户 2026-10-03 的决定，#793）；「稍后」后从脚本行重开，
 * 框内「详情」里的「不准备，直接运行」（真 `POST /api/engine/dependencies/skip`）
 * 放行后自动重跑 → 真 worker → 发现图、运行时图卡片出现。授权准备成功后的重跑由 `ScriptLibrary.test.tsx` 与
 * `dependency-one-click.spec.ts` 看护（SSE 在这里造不了）。
 */
test('试运行撞上依赖门：直接弹一键修复框，稍后可重开；明确跳过后自动跑出图', async ({
  app,
  page,
}) => {
  const dir = path.join(os.tmpdir(), `tavotto-e2e-depgate-${Date.now()}`)
  writeShowOnlyProject(dir)
  const a = await app({ figures: dir })
  let gated = 0
  let probes = 0
  // 请求带 `?pj=` 查询串：用正则认路径（不认 `/probe/cancel`）
  await page.route(/\/api\/registry\/probe(\?|$)/, async (route) => {
    probes += 1
    if (gated > 0) return route.continue()
    gated += 1
    const plan = {
      plan_version: 1, status: 'ready', target_kind: 'tavotto_managed', script: 'show_only.py', needed: [],
      missing: [{ import_name: 'adjustText', distribution: 'adjusttext', resolution_source: 'curated', declared: false, specifiers: [], via: [] }],
      satisfied: [], unknown: [], possible: [], requirements: ['adjusttext'], constraints: [], require_hashes: false,
      adapter: [], blocked: [],
      selection: { selected_groups: [], available_groups: [], unselected_groups: [], skipped_marker: [] },
      identity: 'e2e',
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        script: 'show_only.py', entry: null, stems: [], descriptors: [], tried: [], registered: false,
        error: {
          code: 'dependency_preparation_required',
          message: '脚本开跑就需要的包渲染环境里没有，要先准备依赖',
          dependency_preparation: {
            code: 'dependency_preparation_required', script: 'show_only.py', plan, target_kind: 'tavotto_managed',
            targets: [{ kind: 'tavotto_managed', venv: '', python: '', modifies_user_environment: false, creates_environment: true, available: true, reason: '' }],
            rounds_remaining: 3, skipped: false,
          },
        },
      }),
    })
  })

  await page.goto(a.baseURL)
  const row = page.locator('[data-script-row="show_only.py"]')
  await expect(row).toBeVisible({ timeout: 30_000 })
  await row.locator('[data-script-run]').click()

  // 跑前缺依赖直接弹框；行只保留「稍后」后的再打开入口，不再执行另一套行内准备。
  const dialog = page.locator('[data-dialog="dependency-prepare"]')
  await expect(dialog).toBeVisible()
  await expect(dialog).toBeInViewport()
  await expect(dialog).toContainText('这个脚本还缺 adjusttext，点一下自动装好。')
  await expect(dialog.locator('[data-dependency-prepare-start]')).toHaveText('一键修复')
  await expect(page.getByText('可能需要原环境')).toHaveCount(0)
  expect(probes).toBe(1)
  await dialog.locator('[data-dependency-prepare-later]').click()
  await expect(dialog).toHaveCount(0)
  await expect(row.locator('..')).toHaveAttribute('aria-label', '需要修复')
  const reopen = row.locator('[data-script-preparation-fix]')
  await expect(reopen).toBeVisible()
  await reopen.click()
  await expect(dialog).toBeVisible()
  await expect(dialog).toContainText('这个脚本还缺 adjusttext，点一下自动装好。')
  expect(probes).toBe(1)

  // 页脚 start 槽的「不准备，直接运行」（2026-10-07 设计审计 §10.2，不再埋在「详情」里）：真 `POST /api/engine/dependencies/skip`，
  // 然后这一行自动再试运行（真 worker）→ 发现图（授权准备成功后的重跑由 `ScriptLibrary.test.tsx` 与
  // `dependency-one-click.spec.ts` 看护：SSE 在这里造不了）
  const skipping = page.waitForResponse(response =>
    /\/api\/engine\/dependencies\/skip(\?|$)/.test(response.url()) && response.request().method() === 'POST')
  await dialog.locator('[data-dependency-skip]').click()
  expect((await skipping).ok()).toBe(true)
  await expect(page.getByText('已发现 1 张图')).toBeVisible({ timeout: 120_000 })
  await expect(page.locator('[data-card="runtime:show_only.py#show_only"]')).toBeVisible({ timeout: 30_000 })
  await expect(dialog).toHaveCount(0)
  await expect(page.locator('[data-script-preparation]')).toHaveCount(0)
  expect(gated).toBe(1)
  expect(probes).toBe(2)
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

/**
 * 「能不能编辑」胶囊筛选（`data-testid="asset-editability"`）：可编辑与仅版面的图混在网格里时，
 * 一点就只剩那一类。项目 = 示例图（3 张有脚本的 PDF）+ 一张没有脚本的 PNG（后端判 `layout_only`）。
 * 判的是真界面上卡片集合真的变了，不只是胶囊在 DOM 里。
 */
test(
  '能不能编辑的胶囊：可编辑 / 仅版面一点就分开，回到全部恢复',
  { tag: '@feature:assets.editability-filter' },
  async ({ app, page }) => {
    const repo = path.resolve(import.meta.dirname, '..', '..')
    const dir = mkdtempSync(path.join(os.tmpdir(), 'tavotto-e2e-editability-'))
    cpSync(path.join(repo, 'examples', 'figures'), dir, { recursive: true })
    copyFileSync(path.join(repo, 'tests', 'compat', 'assets', 'sample.png'), path.join(dir, 'Photo_plain.png'))
    const a = await app({ figures: dir })
    await page.goto(a.baseURL)

    const group = page.locator('[data-testid="asset-editability"]')
    await expect(group).toBeVisible({ timeout: 30_000 })
    const viewport = page.viewportSize()!
    const box = await group.boundingBox()
    expect(box).not.toBeNull()
    expect(box!.x).toBeGreaterThanOrEqual(0)
    expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width)

    const cards = page.locator('li[role="option"][data-card]')
    const cardIds = async () => (await cards.evaluateAll((els) => els.map((el) => el.getAttribute('data-card')))).sort()
    const plain = page.locator('li[role="option"][data-card="Photo_plain.png"]')
    const kinetics = page.locator('li[role="option"][data-card="Fig1_kinetics.pdf"]')
    await expect(plain).toBeVisible()
    await expect(kinetics).toBeVisible()
    const all = await cardIds()
    expect(all.length).toBeGreaterThanOrEqual(4)

    // 可编辑：没有脚本的那张消失，有脚本的还在
    await group.locator('[role="radio"][data-value="editable"]').click()
    await expect(group.locator('[data-value="editable"]')).toHaveAttribute('aria-checked', 'true')
    await expect(plain).toHaveCount(0)
    await expect(kinetics).toBeVisible()

    // 仅版面：只剩没有脚本的那张
    await group.locator('[role="radio"][data-value="layout"]').click()
    await expect(kinetics).toHaveCount(0)
    await expect(plain).toBeVisible()
    expect(await cardIds()).toEqual(['Photo_plain.png'])

    // 回到全部：清单与开始时一样
    await group.locator('[role="radio"][data-value="all"]').click()
    await expect(cards).toHaveCount(all.length)
    expect(await cardIds()).toEqual(all)
  },
)

/**
 * 英文四档在 280px 最窄抽屉里放得下（Codex #1027 r4267757501）：抽屉扣掉 `px-3` 只剩 256px，
 * "All / Editable / Pending / Layout" 四档的固有宽度不能超出它，否则右边被 LeftPanel 的 overflow-hidden 裁掉。
 * 语言用 localStorage 偏好切（不依赖 project locale，chromium 下同样执行），宽度用抽屉自己的键盘把手收到下限；
 * 量的是**抽屉**不是视口。「待连接」那一档靠 `writeRuntimeNamedProject`：图名来自运行期、静态解不出，那张图的状态是 needs_probe。
 */
test(
  '能不能编辑的胶囊：英文四档在 280px 最窄抽屉里不溢出',
  { tag: '@feature:assets.editability-filter' },
  async ({ app, page }) => {
    await page.addInitScript(() => localStorage.setItem('tavotto.locale', 'en-US'))
    const repo = path.resolve(import.meta.dirname, '..', '..')
    const dir = mkdtempSync(path.join(os.tmpdir(), 'tavotto-e2e-editability-en-'))
    cpSync(path.join(repo, 'examples', 'figures'), dir, { recursive: true })
    copyFileSync(path.join(repo, 'tests', 'compat', 'assets', 'sample.png'), path.join(dir, 'Photo_plain.png'))
    writeRuntimeNamedProject(dir) // Runtime_map.pdf 的图名静态解不出 → needs_probe（待连接）
    const a = await app({ figures: dir })
    await page.goto(a.baseURL)

    const group = page.locator('[data-testid="asset-editability"]')
    await expect(group).toBeVisible({ timeout: 30_000 })
    // 四档都出现：All / Editable / Pending / Layout
    await expect(group.locator('[role="radio"]')).toHaveCount(4, { timeout: 30_000 })
    await expect(group.locator('[role="radio"]')).toHaveText(['All', 'Editable', 'Pending', 'Layout'])

    // 把抽屉收到下限 280（键盘把手每次 16px）
    const handle = page.locator('[role="separator"][aria-valuemin="280"]')
    await handle.focus()
    for (let i = 0; i < 20 && Number(await handle.getAttribute('aria-valuenow')) > 280; i++) {
      await page.keyboard.press('ArrowLeft')
    }
    await expect(handle).toHaveAttribute('aria-valuenow', '280')

    // 整个胶囊与每一档都落在抽屉的右缘之内
    const drawerOf = () =>
      handle.evaluate((el) => {
        const r = (el.parentElement as HTMLElement).getBoundingClientRect()
        return { left: r.left, right: r.right }
      })
    // 宽度有过渡动画：等量到的宽度真的是 280 再量胶囊，不然是量在半路上
    await expect.poll(async () => Math.round((await drawerOf()).right - (await drawerOf()).left)).toBe(280)
    const drawer = await drawerOf()
    expect(drawer.right - drawer.left).toBeLessThanOrEqual(281)
    // 抽屉内容区的右缘 = 抽屉右缘 - px-3(12)；胶囊是 min-w-fit，字太长时会比内容区宽几个像素，所以一像素都不给
    const contentRight = drawer.right - 12
    await expect
      .poll(async () => {
        const b = (await group.boundingBox())!
        return b.x + b.width
      })
      .toBeLessThanOrEqual(contentRight + 0.01)
    const radios = group.locator('[role="radio"]')
    for (let i = 0; i < 4; i++) {
      const b = (await radios.nth(i).boundingBox())!
      expect(b.x, `第 ${i} 档左缘`).toBeGreaterThanOrEqual(drawer.left)
      expect(b.x + b.width, `第 ${i} 档右缘`).toBeLessThanOrEqual(contentRight + 0.01)
    }
    expect(await horizontalOffenders(page, '[data-testid="asset-editability"]')).toEqual([])
  },
)
