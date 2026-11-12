/**
 * N5 第 6 步：/2 计算图表的完整用户生命周期（拆成两条串行用例，缩短单测暴露面：
 * 本机存在 Playwright Node test-worker 原生退出 0xC0000409 的已知机器问题，
 * 长链用例更容易撞上；断言与链路一步不少）。
 *
 * 种子：100pt 方幅源 + 真实 bind API，再经 v4 排版接口写一份带 `chartFrame`
 * （/2、诞生外框 = 源 viewport）的排版——/2 的入口是持久化的坐标合同。
 * 之后全部走真实界面：打开排版 → 成功的 /2 渲染在文档事务里自动采用 F
 * （native 保比例、originPt 跟上）→ 进图内编辑改标题 → 撤销/重做 → 存进项目；
 * 第二条用例停实例、同 data/config 冷开 → 重开排版 → 帧与标题都在 → 导出 PDF
 * （页面 = F）。
 */
import { expect, test } from './fixtures'
import { startApp, type RunningApp } from './fixtures'
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const PT_MM = 25.4 / 72
const LAYOUT = 'F2Layout'
const NEW_TITLE = 'T11 计算外框'

/** 100pt 方幅的源（与 tests/test_chart_native_api.py 同一份）：轴标签会越出源框，
 *  F ≠ S 是 N5 要处理的真实情形。 */
const SOURCE = Buffer.from(
  '<svg xmlns="http://www.w3.org/2000/svg" width="100pt" height="100pt" ' +
  'viewBox="0 0 100 100"><rect width="100" height="100" fill="white"/>' +
  '<g id="old_axis"><path d="M10 10L10 90L90 90" fill="none" stroke="black"/></g>' +
  '<path id="curve" d="M10 90L90 10" fill="none" stroke="#888888"/>' +
  '<text id="keep" x="3" y="7" font-size="5" ' +
  'font-family="Liberation Sans">Source keep</text></svg>',
  'utf-8',
)

/** 绑定请求（与 tests/test_chart_native_api.py 的 _bind 同一组事实） */
const BIND = {
  file: 'chart.svg',
  axes_id: 'ax1',
  rect: [10, 10, 80, 80],
  x: { p1_user: 0.0, p1_value: 0.0, p2_user: 1.0, p2_value: 1.0, scale: 'linear' },
  y: { p1_user: 0.0, p1_value: 0.0, p2_user: 1.0, p2_value: 1.0, scale: 'linear' },
  axes_nodes: ['old_axis'],
  series: [{ node: 'curve', x_col: 'x', y_col: 'y', kind: 'line', style: { color: '#0000ff', linewidth: 3.0 } }],
  dataset_text: 'x,y\n0,0.25\n1,0.25\n',
  completeness: 'self-contained',
}

const layoutDoc = (chart: unknown) => ({
  // v4 文档库：计算图表项目必须 schema 4（与 tests/test_chart_documents_v4.py 同形）
  schema: 4,
  project: { id: 'proj-f2', name: 'F2' },
  canvases: [
    {
      id: 'c1',
      name: 'Canvas',
      schema: 4,
      page: { w: 210, h: 297 },
      objects: [
        {
          id: 'p1',
          type: 'panel',
          x: 20,
          y: 20,
          w: 80,
          h: 66,
          fileId: 'chart.svg',
          fileKind: 'svg',
          // 源 viewport 100×100pt；诞生形状的外框与它重合
          nativeW: 100 * PT_MM,
          nativeH: 100 * PT_MM,
          overrides: [],
          chart,
          chartFrame: { version: 1, renderProfile: 'native-manifest/2', originPt: [0, 0] },
        },
      ],
      guides: [],
      updatedAt: 1,
    },
  ],
  activeCanvasId: 'c1',
  createdAt: 1,
  updatedAt: 1,
})

async function openLayout(page: import('@playwright/test').Page) {
  await page.getByRole('button', { name: /^(文档|排版)：/ }).click()
  await page.getByRole('menuitem', { name: /^打开(文档|排版)/ }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByRole('button', { name: new RegExp(LAYOUT) }).click()
  await expect(dialog).toBeHidden()
  await expect(page.locator('[data-canvas-stage] [data-object-id]')).toHaveCount(1, { timeout: 30_000 })
}

test.describe.serial('计算图表 /2 生命周期', () => {
  const root = realpathSync.native(mkdtempSync(path.join(os.tmpdir(), 'tavotto-f2-')))
  const keepRoot = realpathSync.native(mkdtempSync(path.join(os.tmpdir(), 'tavotto-f2-keep-')))
  const project = path.join(root, 'F2')
  const layoutFile = path.join(project, 'tavottofile', 'documents', 'v4', `${LAYOUT}.json`)
  let a: RunningApp | undefined
  let second: RunningApp | undefined
  let port = 0

  test.beforeAll(() => {
    mkdirSync(project, { recursive: true })
    writeFileSync(path.join(project, 'chart.svg'), SOURCE)
  })
  test.afterAll(async () => {
    await second?.stop().catch(() => undefined)
    await a?.stop().catch(() => undefined)
    for (const d of [root, keepRoot]) {
      try {
        rmSync(d, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 })
      } catch {
        /* 清理失败不盖住主失败 */
      }
    }
  })

  test('绑定 → 打开 → 自动采用 F → 编辑 → undo/redo → 存进项目', async ({ app, page }) => {
    test.setTimeout(600_000)
    a = await app({ figures: project })
    await page.goto(a.baseURL)
    // ---- 真实绑定 + 产品排版保存：写入 /2 的持久合同
    const bound = await page.request.post(`${a.baseURL}/api/svg/chart/bind`, { data: BIND })
    expect(bound.ok(), await bound.text()).toBe(true)
    const chart = await bound.json()
    // 轴标签让墨迹越出源 viewport：F ≠ S（与 complete_frame_http 同一组标签事实）
    chart.model.axes[0].xlabel = 'Sample time (seconds)'
    chart.model.axes[0].ylabel = 'Response (a.u.)'
    const saved = await page.request.post(`${a.baseURL}/api/documents/v4/layouts/${LAYOUT}?target=project`, {
      data: layoutDoc(chart),
    })
    expect(saved.ok(), await saved.text()).toBe(true)
    const onDisk = JSON.parse(readFileSync(layoutFile, 'utf-8')) as {
      canvases: { objects: { chartFrame?: { renderProfile?: string } }[] }[]
    }
    expect(onDisk.canvases[0].objects[0].chartFrame?.renderProfile).toBe('native-manifest/2')

    // ---- 打开排版：成功的 /2 渲染在文档事务里自动采用 F
    const bodies: string[] = []
    page.on('request', (req) => {
      if (req.url().includes('/api/engine/render')) bodies.push(req.postData() ?? '')
    })
    await openLayout(page)
    await expect
      .poll(() => {
        for (const b of bodies) {
          try {
            if ((JSON.parse(b) as { render_profile?: string }).render_profile === 'native-manifest/2') return true
          } catch { /* ignore */ }
        }
        return false
      }, { timeout: 120_000, message: '/2 渲染请求必须带 render_profile' })
      .toBe(true)
    await page.locator('[data-canvas-stage] [data-object-id]').first().click()
    const inspector = page.getByLabel('右侧面板', { exact: true })
    // 采用后的原生尺寸 = F（144×124pt = 50.8×43.7mm）
    await expect
      .poll(async () => inspector.getByText(/原始 50\.8 × 43\.7/).count(), { timeout: 120_000 })
      .toBeGreaterThan(0)

    // ---- 进图内编辑改标题（真实权威渲染），撤销/重做
    await page.locator('[data-canvas-stage] [data-object-id]').first().dblclick()
    await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 180_000 })
    await page.getByRole('button', { name: '图内元素' }).click()
    await page.getByRole('tree').getByText(/^标题/).first().click()
    const titleField = inspector.locator('[data-prop="text"] [data-rich-text]')
    await expect(titleField).toBeVisible({ timeout: 30_000 })
    await titleField.fill(NEW_TITLE)
    await titleField.press('Enter')
    await expect(titleField).toHaveText(NEW_TITLE)
    await page.keyboard.press('ControlOrMeta+z')
    await expect(titleField).not.toHaveText(NEW_TITLE)
    await page.keyboard.press('ControlOrMeta+Shift+z')
    await expect(titleField).toHaveText(NEW_TITLE)

    // ---- 存进项目（未绑定时弹「存进项目」；已绑定直接保存）
    const saveDialog = page.getByRole('dialog', { name: '存进项目' })
    const saveToProject = async () => {
      await page.keyboard.press('ControlOrMeta+s')
      const appeared = await saveDialog
        .waitFor({ state: 'visible', timeout: 5_000 })
        .then(() => true)
        .catch(() => false)
      if (appeared) {
        await saveDialog.locator('#layout-save-name').fill(LAYOUT)
        await saveDialog.getByRole('button', { name: '存进项目' }).click()
        await expect(saveDialog).toBeHidden()
      }
    }
    await saveToProject()
    // 磁盘排版：originPt 跟上渲染外框（负原点）、native 是 F 尺寸（mm）
    await expect
      .poll(() => {
        const doc = JSON.parse(readFileSync(layoutFile, 'utf-8')) as {
          canvases: { objects: { chartFrame?: { originPt?: number[] }; nativeW?: number }[] }[]
        }
        const panel = doc.canvases[0].objects[0]
        return [panel.chartFrame?.originPt?.[0], panel.nativeW === undefined ? null : Math.round(panel.nativeW * 10) / 10]
      }, { timeout: 30_000 })
      .toEqual([-35, 50.8])

    // ---- 停实例，保存 data/config 供冷开
    port = a.port
    const keptData = path.join(keepRoot, 'data')
    const keptConfig = path.join(keepRoot, 'config')
    cpSync(a.dataDir, keptData, { recursive: true })
    cpSync(path.join(path.dirname(a.dataDir), 'config'), keptConfig, { recursive: true })
    await a.stop()
    a = undefined
  })

  test('同 data/config 冷开 → 重开排版 → 帧与标题都在 → 导出 PDF（页面 = F）', async ({ page }) => {
    test.setTimeout(600_000)
    second = await startApp({
      figures: project,
      port,
      env: { TAVOTTO_DATA_DIR: path.join(keepRoot, 'data'), TAVOTTO_CONFIG_DIR: path.join(keepRoot, 'config') },
    })
    await page.goto(second.baseURL)
    await openLayout(page)
    await page.locator('[data-canvas-stage] [data-object-id]').first().click()
    const inspector = page.getByLabel('右侧面板', { exact: true })
    // 冷开重放：chartFrame（F 尺寸）与标题修改都在
    await expect
      .poll(async () => inspector.getByText(/原始 50\.8 × 43\.7/).count(), { timeout: 120_000 })
      .toBeGreaterThan(0)
    await page.locator('[data-canvas-stage] [data-object-id]').first().dblclick()
    await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 180_000 })
    await page.getByRole('button', { name: '图内元素' }).click()
    await page.getByRole('tree').getByText(/^标题/).first().click()
    await expect(inspector.locator('[data-prop="text"] [data-rich-text]')).toHaveText(NEW_TITLE, { timeout: 30_000 })

    // ---- 导出 PDF：真实落盘，页面 = F（宽 > 100.5pt）
    await page.keyboard.press('Escape')
    await page.keyboard.press('ControlOrMeta+e')
    const exportDialog = page.getByRole('dialog')
    await expect(exportDialog).toBeVisible()
    for (const fmt of ['PNG', 'TIFF', 'EPS']) {
      const box = exportDialog.getByRole('checkbox', { name: fmt, exact: true })
      if ((await box.count()) && (await box.isChecked())) await box.uncheck()
    }
    const pdfBox = exportDialog.getByRole('checkbox', { name: 'PDF', exact: true })
    if (!(await pdfBox.isChecked())) await pdfBox.check()
    const started = page.waitForResponse(
      (r) => new URL(r.url()).pathname === '/api/export/start' && r.request().method() === 'POST',
    )
    await exportDialog.getByRole('button', { name: /开始导出/ }).click()
    const job = (await (await started).json()) as { export_dir?: string }
    expect(job.export_dir, 'start 回包里应当有导出目录').toBeTruthy()
    await expect(exportDialog.locator('[data-export-result]')).toBeVisible({ timeout: 240_000 })
    const exported = readFileSync(path.join(job.export_dir!, 'F2.pdf'))
    expect(exported.subarray(0, 5).toString('latin1')).toBe('%PDF-')
    const media = exported.toString('latin1').match(/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]/)
    expect(media, 'PDF 应当带 MediaBox').not.toBeNull()
    expect(Number(media![1])).toBeGreaterThan(100.5)
    expect(existsSync(path.join(job.export_dir!, 'F2.pdf'))).toBe(true)
  })
})
