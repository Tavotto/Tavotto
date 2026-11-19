import { mkdtempSync, readdirSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, startApp, test } from './fixtures'

test.use({ actionTimeout: 20_000 })

const SOURCE = '<svg xmlns="http://www.w3.org/2000/svg" width="100pt" height="100pt" viewBox="0 0 100 100">' +
  '<rect width="100" height="100" fill="white"/>' +
  '<path id="old_axis" d="M10 10L10 90L90 90" fill="none" stroke="black"/>' +
  '<path id="curve" d="M10 90L90 10" fill="none" stroke="#888888"/>' +
  '<text id="keep" x="3" y="7" font-size="5">Source keep</text></svg>'

test('chart binding backs up the project and reopens the frozen source', async ({ app, page }) => {
  test.setTimeout(180_000)
  const project = mkdtempSync(path.join(os.tmpdir(), 'tavotto-chart-document-'))
  const source = path.join(project, 'chart.svg')
  writeFileSync(source, SOURCE)
  let reopened: Awaited<ReturnType<typeof startApp>> | undefined
  const openLayout = async () => {
    await page.getByRole('button', { name: /^(文档|排版)：/ }).click()
    await page.getByRole('menuitem', { name: /^打开(文档|排版)/ }).click()
    const dialog = page.getByRole('dialog')
    await dialog.getByRole('button', { name: /ChartLayout/ }).click()
    await expect(dialog).toBeHidden()
  }
  try {
    const first = await app({ figures: project })
    const layout = {
      schema: 3, project: { id: 'chart-test', name: 'ChartLayout' },
      canvases: [{
        id: 'c1', name: 'Canvas', schema: 2, page: { w: 120, h: 100 }, guides: [], updatedAt: 1,
        objects: [{
          id: 'chart-panel', type: 'panel', x: 20, y: 20, w: 50, h: 50,
          fileId: 'chart.svg', fileKind: 'svg', nativeW: 100 * 25.4 / 72, nativeH: 100 * 25.4 / 72,
          overrides: [],
        }],
      }],
      activeCanvasId: 'c1', createdAt: 1, updatedAt: 1,
    }
    const saved = await page.request.post(first.baseURL + '/api/layouts/ChartLayout?target=project', { data: layout })
    expect(saved.ok(), await saved.text()).toBe(true)
    await page.goto(first.baseURL)
    await openLayout()
    const panel = page.locator('[data-canvas-stage] [data-object-id="chart-panel"]')
    const dialog = page.locator('[data-chart-bind-dialog]')
    const enterBinding = async () => {
      await panel.click({ button: 'right' })
      await page.locator('[data-quick-item="bind-chart"]').click()
      for (const [index, value] of [10, 10, 80, 80].entries()) {
        await dialog.locator(`[data-chart-rect="${index}"]`).fill(String(value))
      }
      await dialog.locator('[data-chart-axes-nodes]').fill('old_axis')
      await dialog.locator('[data-chart-series-field="node"]').fill('curve')
      await dialog.locator('[data-chart-series-field="x"]').fill('x')
      await dialog.locator('[data-chart-series-field="y"]').fill('y')
      await dialog.locator('[data-chart-data]').fill('x,y\n0,25\n10,25\n')
    }

    // Deliver a real render into a session that has already opened a different binding draft.
    const renderRoute = '**/api/svg/chart/compute?*'
    let intercepted = false
    let release!: () => void
    const pending = new Promise<void>(resolve => { release = resolve })
    await page.route(renderRoute, async route => {
      intercepted = true
      await pending
      await route.continue()
    }, { times: 1 })
    await enterBinding()
    const bound = page.waitForResponse(res => new URL(res.url()).pathname === '/api/svg/chart/bind', { timeout: 60_000 })
    const late = page.waitForResponse(res => new URL(res.url()).pathname === '/api/svg/chart/compute', { timeout: 90_000 })
    try {
      await page.locator('[data-chart-bind-action="preview"]').click()
      const binding = await bound
      expect(binding.ok(), await binding.text()).toBe(true)
      await expect.poll(() => intercepted, { timeout: 60_000 }).toBe(true)
      await page.getByRole('dialog').getByRole('button', { name: '取消', exact: true }).click()
      await expect(dialog).toBeHidden()
      await enterBinding()
      await dialog.locator('[data-chart-data]').fill('x,y\n0,50\n10,50\n')
    } finally {
      release()
    }
    const discarded = await late
    expect(discarded.ok(), await discarded.text()).toBe(true)
    await page.evaluate(() => new Promise<void>(resolve =>
      requestAnimationFrame(() => requestAnimationFrame(() => resolve()))))
    await expect(dialog.locator('[data-chart-comparison]')).toHaveCount(0)
    await expect(dialog.locator('[data-chart-data]')).toHaveValue('x,y\n0,50\n10,50\n')
    await expect(panel.locator('[data-display="exact"]')).toHaveCount(0)

    await dialog.locator('[data-chart-data]').fill('x,y\n0,25\n10,25\n')
    await page.locator('[data-chart-bind-action="preview"]').click()
    await expect(dialog.locator('[data-chart-comparison] img')).toHaveCount(2, { timeout: 60_000 })
    const confirm = page.locator('[data-chart-bind-action="confirm"]')

    // A refused checkpoint must leave the layout unbound, even after a valid preview.
    await page.route('**/api/versions/*', async route => {
      if (route.request().method() === 'POST') await route.fulfill({ status: 503, json: { error: 'test failure' } })
      else await route.continue()
    })
    await confirm.click({ timeout: 60_000 })
    await expect(dialog.getByText('排版备份未能保存。请重新预览后重试。')).toBeVisible()
    await expect(panel.locator('[data-display="exact"]')).toHaveCount(0)
    await page.unroute('**/api/versions/*')

    await page.locator('[data-chart-bind-action="preview"]').click()
    const checkpoint = page.waitForResponse(r =>
      /\/api\/versions\/[^/]+$/.test(new URL(r.url()).pathname) && r.request().method() === 'POST')
    await confirm.click({ timeout: 60_000 })
    const backup = await checkpoint
    expect(backup.ok(), await backup.text()).toBe(true)
    const response = await backup.json()
    expect(response.version.schema).toBe(3)
    const docId = new URL(backup.url()).pathname.split('/').at(-1)
    await expect(dialog).toBeHidden()
    await expect(panel.locator('[data-display="exact"] svg text').getByText('Source keep')).toHaveCount(1, { timeout: 60_000 })
    const chart = panel.locator('[data-display="exact"] [data-element-svg] > svg')
    const firstFrame = await chart.getAttribute('viewBox')
    await panel.click({ button: 'right' })
    await page.locator('[data-quick-item="edit-elements"]').click()
    const elements = page.locator('[data-rail="elements"]')
    if ((await elements.getAttribute('aria-expanded')) !== 'true') await elements.click()
    await page.getByRole('tree').locator('[data-tree-label][title$=" · axes_0.yticks"]').click()
    const size = page.getByLabel('右侧面板', { exact: true }).locator('[data-prop="fontsize"] input')
    await size.fill('20')
    await size.press('Enter')
    await expect(chart).not.toHaveAttribute('viewBox', firstFrame!, { timeout: 60_000 })
    const largerFrame = await chart.getAttribute('viewBox')
    const undo = page.locator('[data-history-action="undo"]')
    const redo = page.locator('[data-history-action="redo"]')
    await undo.click()
    await expect(chart).toHaveAttribute('viewBox', firstFrame!, { timeout: 60_000 })
    await redo.click()
    await expect(chart).toHaveAttribute('viewBox', largerFrame!, { timeout: 60_000 })

    // Drag an axes handle in display coordinates and measure the source-space edit.
    const beforeDrag = await chart.innerHTML()
    await page.getByRole('tree').locator('[data-tree-label][title$=" · axes_0"]').click()
    const handle = page.locator('[data-element-handle="e"]')
    await expect(handle).toHaveCount(1)
    const { x, y, width, height } = (await handle.boundingBox())!
    const moved = page.waitForResponse(r =>
      new URL(r.url()).pathname === '/api/engine/render' && r.request().postDataJSON().patches?.some(
        (patch: { gid: string; prop: string }) => patch.gid === 'axes_0' && patch.prop === 'position',
      ))
    await page.mouse.move(x + width / 2, y + height / 2)
    await page.mouse.down()
    await page.mouse.move(x + width / 2 - 20, y + height / 2, { steps: 5 })
    await page.mouse.up()
    const moveResult = await moved
    expect(moveResult.ok(), await moveResult.text()).toBe(true)
    const position = moveResult.request().postDataJSON().patches.find((patch: { prop: string }) => patch.prop === 'position')
    expect(position.value[2]).toBeLessThan(0.8)
    expect(position.value[3]).toBeCloseTo(0.8, 3)
    await expect.poll(() => chart.innerHTML()).not.toBe(beforeDrag)
    await undo.click()
    await expect.poll(() => chart.innerHTML()).toBe(beforeDrag)
    await page.keyboard.press('Escape')

    const expected = await chart.innerHTML()
    const manualSave = page.waitForResponse(r =>
      new URL(r.url()).pathname === '/api/layouts/ChartLayout' && r.request().method() === 'POST')
    await page.keyboard.press('ControlOrMeta+s')
    const result = await manualSave
    expect(result.ok(), await result.text()).toBe(true)
    expect((await result.json()).chart_sources).toHaveLength(1)
    const layoutOnDisk = await (await page.request.get(first.baseURL + '/api/layouts/ChartLayout')).json()
    expect(layoutOnDisk.schema).toBe(4)
    const persisted = layoutOnDisk.canvases[0].objects[0]
    expect(persisted.chartSource.sourceId).toBe('chart.svg')
    expect(persisted.chartFrame.renderProfile).toBe('native-manifest/2')
    expect(persisted.chartFrame.originPt[0]).toBeLessThan(0)
    const tickSize = persisted.overrides.find((patch: { gid: string; prop: string }) =>
      patch.gid === 'axes_0.yticks' && patch.prop === 'fontsize')
    // The inspector takes on-page points; the saved patch uses source-scale points.
    expect(tickSize.value).toBeCloseTo(20 * (100 * 25.4 / 72) / 50, 2)
    const autosave = await (await page.request.get(first.baseURL + '/api/autosave/' + docId)).json()
    expect(autosave.schema).toBe(4)
    expect(autosave.canvases[0].objects[0]).toEqual(persisted)

    renameSync(source, path.join(project, 'moved.svg'))
    await first.stop()
    reopened = await startApp({ figures: project })
    await page.goto(reopened.baseURL)
    await openLayout()
    await expect(chart.getByText('Source keep')).toHaveCount(1, { timeout: 60_000 })
    expect(await chart.innerHTML()).toBe(expected)
    await expect(chart).toHaveAttribute('viewBox', largerFrame!)

    await page.keyboard.press('ControlOrMeta+e')
    const output = page.getByRole('dialog')
    await expect(output).toBeVisible()
    for (const box of await output.getByRole('checkbox').all()) {
      const label = await box.getAttribute('aria-label')
      if (['PNG', 'TIFF', 'EPS', 'SVG'].includes(label ?? '') && await box.isChecked()) await box.uncheck()
    }
    const pdf = output.getByRole('checkbox', { name: 'PDF', exact: true })
    if (!(await pdf.isChecked())) await pdf.check()
    const started = page.waitForResponse(r =>
      new URL(r.url()).pathname === '/api/export/start' && r.request().method() === 'POST')
    await output.getByRole('button', { name: /开始导出/ }).click()
    const job = await started
    expect(job.ok(), await job.text()).toBe(true)
    const request = job.request().postDataJSON()
    const sources = request.original ? [request.original] : request.canvas.objects
    expect(sources).toEqual(expect.arrayContaining([expect.objectContaining({
      chart_source: persisted.chartSource,
      chart_render_profile: 'native-manifest/2',
      overrides: persisted.overrides,
    })]))
    const { export_dir } = await job.json()
    await expect(output.locator('[data-export-result]')).toBeVisible({ timeout: 60_000 })
    const files = readdirSync(export_dir).filter(name => name.endsWith('.pdf'))
    expect(files).toHaveLength(1)
    expect(readFileSync(path.join(export_dir, files[0])).subarray(0, 5).toString()).toBe('%PDF-')
  } finally {
    await reopened?.stop()
    rmSync(project, { recursive: true, force: true })
  }
})
