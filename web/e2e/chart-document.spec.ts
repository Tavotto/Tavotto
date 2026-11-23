import { existsSync, mkdtempSync, readdirSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, startApp, test, waitForDocumentReady } from './fixtures'

test.use({ actionTimeout: 20_000 })

const SOURCE = '<svg xmlns="http://www.w3.org/2000/svg" width="100pt" height="100pt" viewBox="0 0 100 100">' +
  '<rect width="100" height="100" fill="white"/>' +
  '<path id="old_axis" d="M10 10L10 90L90 90" fill="none" stroke="black"/>' +
  '<path id="curve" d="M10 90L90 10" fill="none" stroke="#888888"/>' +
  '<text id="keep" x="3" y="7" font-size="5">Source keep</text></svg>'

test('chart binding backs up the project and reopens the frozen source', {
  tag: '@feature:canvas.bind-computed-svg',
}, async ({ app, page }) => {
  test.setTimeout(240_000)
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
        }, {
          id: 'curve-note', type: 'path', x: 80, y: 24, w: 24, h: 18,
          color: '#224466', strokePt: 1.5, fill: '#aaccee', fillRule: 'evenodd',
          subpaths: [{
            closed: true,
            nodes: [{ p: [0, 0.5], out: [0, 0] }, { p: [1, 0.5], in: [1, 0], out: [1, 1] }, { p: [0.5, 1] }],
          }],
        }],
      }],
      activeCanvasId: 'c1', createdAt: 1, updatedAt: 1,
    }
    const saved = await page.request.post(first.baseURL + '/api/layouts/ChartLayout?target=project', { data: layout })
    expect(saved.ok(), await saved.text()).toBe(true)
    await page.goto(first.baseURL)
    await openLayout()
    const panel = page.locator('[data-canvas-stage] [data-object-id="chart-panel"]')
    const annotation = page.locator('[data-canvas-stage] [data-object-id="curve-note"] svg > path:not([data-hit-path])')
    await expect(annotation).toBeVisible()
    await expect(annotation).toHaveAttribute('d', /C/)
    const annotationGeometry = (await annotation.getAttribute('d'))!
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
    await expect(annotation).toHaveAttribute('d', annotationGeometry)

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

    // The recipient has a fresh user directory and receives only the package.
    const packed = await page.request.post(reopened.baseURL + '/api/package', { data: { doc: layoutOnDisk } })
    expect(packed.ok(), await packed.text()).toBe(true)
    const { name, url } = await packed.json()
    const archive = await page.request.get(reopened.baseURL + url)
    expect(archive.ok()).toBe(true)
    const receiver = await app({ noProject: true })
    const destination = path.join(receiver.home, '接收 端项目')
    await page.goto(receiver.baseURL)
    const fileChooser = page.waitForEvent('filechooser')
    await page.locator('[data-install-project-package]').click()
    await (await fileChooser).setFiles({ name, mimeType: 'application/zip', buffer: await archive.body() })
    const destinationField = page.locator('[data-project-package-destination]')
    await expect(destinationField).toBeVisible({ timeout: 60_000 })
    expect(existsSync(destination)).toBe(false)
    await destinationField.fill(destination)
    const installation = page.waitForResponse(r => new URL(r.url()).pathname === '/api/package/install', { timeout: 90_000 })
    await page.locator('[data-project-package-install]').click()
    const installed = await installation
    expect(installed.status(), await installed.text()).toBe(201)
    expect((await installed.json()).document_saved).toBe(false)
    expect(existsSync(path.join(destination, 'chart.svg'))).toBe(false)
    await page.locator('[data-project-package-open]').click()
    const continueEditing = page.locator('[data-project-package-continue]')
    await expect(continueEditing).toBeVisible({ timeout: 80_000 })
    await continueEditing.click()
    await waitForDocumentReady(page)
    await expect(chart.getByText('Source keep')).toHaveCount(1, { timeout: 60_000 })
    expect(await chart.innerHTML()).toBe(expected)
    await expect(annotation).toHaveAttribute('d', annotationGeometry)
    await page.reload()
    await waitForDocumentReady(page)
    await expect(chart.getByText('Source keep')).toHaveCount(1, { timeout: 60_000 })
    expect(await chart.innerHTML()).toBe(expected)
    await expect(annotation).toHaveAttribute('d', annotationGeometry)
    await expect(annotation).toHaveAttribute('fill-rule', 'evenodd')
  } finally {
    await reopened?.stop()
    rmSync(project, { recursive: true, force: true })
  }
})

test('packages only the active canvas and reopens it in a fresh project', {
  tag: '@feature:canvas.package-current',
}, async ({ app, page }) => {
  test.setTimeout(240_000)
  const project = mkdtempSync(path.join(os.tmpdir(), 'tavotto-canvas-package-'))
  const layoutName = 'SharingLayout'
  const c1 = 'private'
  const c2 = 'shared'
  let sender: Awaited<ReturnType<typeof startApp>> | undefined
  try {
    writeFileSync(path.join(project, 'chart.svg'), SOURCE)
    writeFileSync(path.join(project, 'private.svg'), SOURCE.replace('Source keep', 'Private note'))
    writeFileSync(path.join(project, 'inset.pdf'), readFileSync(path.join(
      import.meta.dirname, '..', '..', 'examples', 'figures', 'Fig1_kinetics.pdf',
    )))
    sender = await startApp({ figures: project })
    const canvas = (id: string, name: string, fileId: string) => ({
      id, name, schema: 2, page: { w: 120, h: 100 }, guides: [], updatedAt: 1,
      objects: [{
        id: id + '-panel', type: 'panel', x: 20, y: 20, w: 50, h: 50,
        fileId, fileKind: 'svg', nativeW: 100 * 25.4 / 72, nativeH: 100 * 25.4 / 72,
        overrides: [],
      }],
    })
    const shared = canvas(c2, 'Shared canvas', 'chart.svg')
    shared.objects.push({
      ...shared.objects[0], id: 'shared-inset', fileId: 'inset.pdf', fileKind: 'pdf',
      x: 80, w: 25, h: 20, nativeW: 100, nativeH: 80,
    })
    const layout = {
      schema: 3, project: { id: 'sharing-test', name: layoutName },
      canvases: [
        canvas(c1, 'Private canvas', 'private.svg'),
        shared,
      ],
      activeCanvasId: c1, createdAt: 1, updatedAt: 1,
    }
    const layoutURL = sender.baseURL + '/api/layouts/' + layoutName
    const seeded = await page.request.post(layoutURL + '?target=project', { data: layout })
    expect(seeded.ok(), await seeded.text()).toBe(true)
    await page.goto(sender.baseURL)
    const documentMenu = page.getByRole('button', { name: /^(文档|排版)：/ })
    await documentMenu.click()
    await page.getByRole('menuitem', { name: /^打开(文档|排版)/ }).click()
    await page.getByRole('dialog').getByRole('button', { name: new RegExp(layoutName) }).click()
    await waitForDocumentReady(page)
    await page.locator('[data-all-canvases]').click()
    await page.locator('[data-all-canvases-item="shared"]').click()
    await expect(page.locator('[data-canvas-tab="shared"]')).toHaveAttribute('aria-selected', 'true')
    const panel = page.locator('[data-canvas-stage] [data-object-id="shared-panel"]')
    await panel.click({ button: 'right' })
    await page.locator('[data-quick-item="bind-chart"]').click()
    const binding = page.locator('[data-chart-bind-dialog]')
    for (const [index, value] of [10, 10, 80, 80].entries()) {
      await binding.locator(`[data-chart-rect="${index}"]`).fill(String(value))
    }
    await binding.locator('[data-chart-axes-nodes]').fill('old_axis')
    await binding.locator('[data-chart-series-field="node"]').fill('curve')
    await binding.locator('[data-chart-series-field="x"]').fill('x')
    await binding.locator('[data-chart-series-field="y"]').fill('y')
    await binding.locator('[data-chart-data]').fill('x,y\n0,25\n10,25\n')
    await page.locator('[data-chart-bind-action="preview"]').click()
    await expect(binding.locator('[data-chart-comparison] img')).toHaveCount(2, { timeout: 60_000 })
    await page.locator('[data-chart-bind-action="confirm"]').click()
    const chart = panel.locator('[data-display="exact"] [data-element-svg] > svg')
    await expect(chart.getByText('Source keep')).toHaveCount(1, { timeout: 60_000 })
    const expectedSvg = await chart.innerHTML()
    const saved = page.waitForResponse(response =>
      new URL(response.url()).pathname === '/api/layouts/' + layoutName && response.request().method() === 'POST')
    await page.keyboard.press('ControlOrMeta+s')
    const save = await saved
    expect(save.ok(), await save.text()).toBe(true)
    const before = await (await page.request.get(layoutURL)).text()

    await documentMenu.click()
    const packaging = page.waitForResponse(response => new URL(response.url()).pathname === '/api/package')
    await page.getByRole('menuitem', { name: '仅打包当前画布', exact: true }).click()
    const packed = await packaging
    expect(packed.ok(), await packed.text()).toBe(true)
    const { name, url, chart_sources } = await packed.json()
    expect(chart_sources).toBe(1)
    expect(name).toMatch(/^Shared_canvas_/)
    await expect(page.locator('[data-canvas-tab="private"]')).toBeVisible()
    expect(await (await page.request.get(layoutURL)).text()).toBe(before)
    const archive = await page.request.get(sender.baseURL + url)
    expect(archive.ok(), await archive.text()).toBe(true)
    const buffer = await archive.body()
    await sender.stop()
    sender = undefined

    const receiver = await app({ noProject: true })
    const destination = path.join(receiver.home, 'received canvas')
    await page.goto(receiver.baseURL)
    const chooser = page.waitForEvent('filechooser')
    await page.locator('[data-install-project-package]').click()
    await (await chooser).setFiles({ name, mimeType: 'application/zip', buffer })
    await page.locator('[data-project-package-destination]').fill(destination, { timeout: 60_000 })
    const installation = page.waitForResponse(response =>
      new URL(response.url()).pathname === '/api/package/install', { timeout: 90_000 })
    await page.locator('[data-project-package-install]').click()
    const installed = await installation
    expect(installed.status(), await installed.text()).toBe(201)
    const received = (await installed.json()).doc
    expect(received.activeCanvasId).toBe(c2)
    expect(received.canvases.map((item: { id: string }) => item.id)).toEqual([c2])
    expect(existsSync(path.join(destination, 'private.svg'))).toBe(false)
    expect(existsSync(path.join(destination, 'inset.pdf'))).toBe(true)
    await page.locator('[data-project-package-open]').click()
    const continueEditing = page.locator('[data-project-package-continue]')
    await continueEditing.click({ timeout: 80_000 })
    await expect(continueEditing).toBeHidden()
    await waitForDocumentReady(page)
    await expect(chart.getByText('Source keep')).toHaveCount(1, { timeout: 60_000 })
    expect(await chart.innerHTML()).toBe(expectedSvg)
    await expect(page.locator('[data-canvas-tab="private"]')).toHaveCount(0)

    await page.locator('[data-onboarding-anchor="export"]').click()
    const pdf = page.locator('[data-export-format="pdf"] input')
    await expect(pdf).toBeVisible()
    if (!(await pdf.isChecked())) await pdf.check()
    const png = page.locator('[data-export-format="png"] input')
    if (await png.isChecked()) await png.uncheck()
    const exportStarted = page.waitForResponse(response =>
      new URL(response.url()).pathname === '/api/export/start' && response.request().method() === 'POST')
    await page.locator('[data-export-start]').click()
    const job = await exportStarted
    expect(job.ok(), await job.text()).toBe(true)
    expect(job.request().postDataJSON().scope).toBe('canvas')
    const { export_dir } = await job.json()
    await expect(page.locator('[data-export-result]')).toBeVisible({ timeout: 60_000 })
    const files = readdirSync(export_dir).filter(file => file.endsWith('.pdf'))
    expect(files).toHaveLength(1)
    expect(readFileSync(path.join(export_dir, files[0])).subarray(0, 5).toString()).toBe('%PDF-')
  } finally {
    await sender?.stop()
    rmSync(project, { recursive: true, force: true })
  }
})

test('packages selected canvases and installs only their resources', {
  tag: '@feature:canvas.package-selection',
}, async ({ app, page }) => {
  const project = mkdtempSync(path.join(os.tmpdir(), 'tavotto-selected-canvases-'))
  const layoutName = 'SelectedSharing'
  let sender: Awaited<ReturnType<typeof startApp>> | undefined
  try {
    const pdf = readFileSync(path.join(import.meta.dirname, '..', '..', 'examples', 'figures', 'Fig1_kinetics.pdf'))
    for (const id of ['private', 'shared', 'detail']) writeFileSync(path.join(project, id + '.pdf'), pdf)
    sender = await startApp({ figures: project })
    const canvases = ['private', 'shared', 'detail'].map(id => ({
      id, name: id, schema: 2, page: { w: 120, h: 100 }, guides: [], updatedAt: 1,
      objects: [{
        id: id + '-panel', type: 'panel', x: 20, y: 20, w: 50, h: 40,
        fileId: id + '.pdf', fileKind: 'pdf', nativeW: 73, nativeH: 58, overrides: [],
      }],
    }))
    const layoutURL = sender.baseURL + '/api/layouts/' + layoutName
    const seeded = await page.request.post(layoutURL + '?target=project', {
      data: {
        schema: 3, project: { id: 'sharing-selection', name: layoutName },
        canvases, activeCanvasId: 'private', createdAt: 1, updatedAt: 1,
      },
    })
    expect(seeded.ok(), await seeded.text()).toBe(true)
    const before = await (await page.request.get(layoutURL)).text()
    await page.goto(sender.baseURL)
    const documentMenu = page.getByRole('button', { name: /^(文档|排版)：/ })
    await documentMenu.click()
    await page.getByRole('menuitem', { name: /^打开(文档|排版)/ }).click()
    await page.getByRole('dialog').getByRole('button', { name: new RegExp(layoutName) }).click()
    await waitForDocumentReady(page)
    await expect(page.locator('[data-canvas-tab="private"]')).toHaveAttribute('aria-selected', 'true')

    await documentMenu.click()
    const fullExport = page.waitForResponse(response => new URL(response.url()).pathname === '/api/package')
    await page.getByRole('menuitem', { name: '导出项目包', exact: true }).click()
    const fullPackage = await fullExport
    expect(fullPackage.ok(), await fullPackage.text()).toBe(true)
    expect(fullPackage.request().postDataJSON().canvas_ids).toEqual(['private', 'shared', 'detail'])
    expect(await fullPackage.json()).toMatchObject({ document_format: 'v4', assets: 3 })

    await expect(documentMenu).toHaveAttribute('aria-expanded', 'false')
    await documentMenu.press('ArrowDown')
    await page.getByRole('menuitem', { name: '打包所选画布', exact: true }).click()
    const selection = page.getByRole('dialog', { name: '打包所选画布' })
    const submit = selection.getByRole('button', { name: '打包所选画布' })
    const privateCanvas = selection.locator('[data-package-canvas="private"]')
    await expect(privateCanvas).toBeChecked()
    await privateCanvas.uncheck()
    await expect(submit).toBeDisabled()
    await selection.locator('[data-package-canvas="detail"]').check()
    await selection.locator('[data-package-canvas="shared"]').check()
    const exporting = page.waitForResponse(response => new URL(response.url()).pathname === '/api/package')
    await submit.click()
    const packed = await exporting
    expect(packed.ok(), await packed.text()).toBe(true)
    expect(packed.request().postDataJSON().canvas_ids).toEqual(['shared', 'detail'])
    const { name, url, assets } = await packed.json()
    expect(assets).toBe(2)
    await expect(selection).toBeHidden()
    await expect(page.locator('[data-canvas-tab="private"]')).toHaveAttribute('aria-selected', 'true')
    expect(await (await page.request.get(layoutURL)).text()).toBe(before)
    const archive = await page.request.get(sender.baseURL + url)
    expect(archive.ok()).toBe(true)
    const buffer = await archive.body()
    await sender.stop()
    sender = undefined

    const receiver = await app({ noProject: true })
    const destination = path.join(receiver.home, 'received canvases')
    await page.goto(receiver.baseURL)
    const chooser = page.waitForEvent('filechooser')
    await page.locator('[data-install-project-package]').click()
    await (await chooser).setFiles({ name, mimeType: 'application/zip', buffer })
    await page.locator('[data-project-package-destination]').fill(destination, { timeout: 60_000 })
    const installation = page.waitForResponse(response =>
      new URL(response.url()).pathname === '/api/package/install', { timeout: 90_000 })
    await page.locator('[data-project-package-install]').click()
    const installed = await installation
    expect(installed.status(), await installed.text()).toBe(201)
    const received = (await installed.json()).doc
    expect(received.activeCanvasId).toBe('shared')
    expect(received.canvases.map((item: { id: string }) => item.id)).toEqual(['shared', 'detail'])
    expect(existsSync(path.join(destination, 'private.pdf'))).toBe(false)
    for (const id of ['shared', 'detail']) expect(readFileSync(path.join(destination, id + '.pdf'))).toEqual(pdf)

    await page.locator('[data-project-package-open]').click()
    await page.locator('[data-project-package-continue]').click({ timeout: 80_000 })
    await waitForDocumentReady(page)
    await expect(page.locator('[data-canvas-stage] [data-object-id="shared-panel"]')).toBeVisible()
    await page.locator('[data-all-canvases]').click()
    await expect(page.locator('[data-all-canvases-item="private"]')).toHaveCount(0)
    await page.locator('[data-all-canvases-item="detail"]').click()
    await expect(page.locator('[data-canvas-stage] [data-object-id="detail-panel"]')).toBeVisible()
  } finally {
    await sender?.stop()
    rmSync(project, { recursive: true, force: true })
  }
})

test('canvas PDF export adds bookmarks to two instances of the same figure', {
  tag: '@feature:export.pdf-panel-bookmarks',
}, async ({ app, page }) => {
  const project = mkdtempSync(path.join(os.tmpdir(), 'tavotto-pdf-bookmarks-'))
  const layoutName = 'BookmarkLayout'
  const fileId = 'panel.pdf'
  let instance: Awaited<ReturnType<typeof startApp>> | undefined
  try {
    writeFileSync(path.join(project, fileId), readFileSync(path.join(
      import.meta.dirname, '..', '..', 'examples', 'figures', 'Fig1_kinetics.pdf',
    )))
    instance = await app({ figures: project })
    const panel = {
      type: 'panel', y: 20, w: 50, h: 40, fileId, fileKind: 'pdf', nativeW: 100, nativeH: 80,
      overrides: [],
    }
    const saved = await page.request.post(instance.baseURL + '/api/layouts/' + layoutName + '?target=project', {
      data: {
        schema: 3, project: { id: 'bookmark-test', name: layoutName },
        canvases: [{
          id: 'c1', name: layoutName, schema: 2, page: { w: 140, h: 100 }, guides: [], updatedAt: 1,
          objects: [
            { ...panel, id: 'bookmark-left', name: 'Left panel', x: 15 },
            { ...panel, id: 'bookmark-right', name: 'Right panel', x: 75 },
          ],
        }],
        activeCanvasId: 'c1', createdAt: 1, updatedAt: 1,
      },
    })
    expect(saved.ok(), await saved.text()).toBe(true)
    await page.goto(instance.baseURL)
    await waitForDocumentReady(page)
    await page.keyboard.press('ControlOrMeta+k')
    await page.locator('[data-palette-input]').fill('打开排版')
    await page.locator('[data-cmd-id="load-layout"]').click()
    await page.locator('[data-layout-row="BookmarkLayout"]').click()
    await expect(page.locator('[data-canvas-stage] [data-object-id="bookmark-right"]')).toBeVisible()
    await page.locator('[data-onboarding-anchor="export"]').click()
    const dialog = page.locator('[data-export-group="format"]')
    const pdf = dialog.locator('[data-export-format="pdf"] input')
    await expect(pdf).toBeVisible()
    if (!(await pdf.isChecked())) await pdf.check()
    const bookmarks = dialog.locator('[data-export-pdf-bookmarks]')
    await expect(bookmarks).toBeVisible()
    await expect(bookmarks).not.toBeChecked()
    await bookmarks.check()
    const started = page.waitForResponse(r =>
      new URL(r.url()).pathname === '/api/export/start' && r.request().method() === 'POST')
    await page.locator('[data-export-start]').click()
    const job = await started
    expect(job.ok(), await job.text()).toBe(true)
    const request = job.request().postDataJSON()
    expect(request.pdf_panel_bookmarks).toBe(true)
    expect(request.canvas.objects.map((o: { bookmark_title: string }) => o.bookmark_title))
      .toEqual(['Left panel', 'Right panel'])
    const { export_dir } = await job.json()
    await expect(page.locator('[data-export-result]')).toBeVisible({ timeout: 60_000 })
    const files = readdirSync(export_dir).filter(name => name.endsWith('.pdf'))
    expect(files).toHaveLength(1)
    const published = readFileSync(path.join(export_dir, files[0])).toString('latin1')
    expect(published.match(/\/FitR\b/g)).toHaveLength(2)
  } finally {
    await instance?.stop()
    rmSync(project, { recursive: true, force: true })
  }
})

test('canvas PDF export adds an annotation layer and a text attachment', {
  tag: ['@feature:export.pdf-annotation-layer', '@feature:export.pdf-text-attachment', '@feature:export.pdf-panel-layers'],
}, async ({ app, page }) => {
  const project = mkdtempSync(path.join(os.tmpdir(), 'tavotto-pdf-annotation-layer-'))
  const name = 'LayeredCanvas'
  const fileId = 'panel.pdf'
  let instance: Awaited<ReturnType<typeof startApp>> | undefined
  try {
    writeFileSync(path.join(project, fileId), readFileSync(path.join(
      import.meta.dirname, '..', '..', 'examples', 'figures', 'Fig1_kinetics.pdf',
    )))
    instance = await app({ figures: project })
    const saved = await page.request.post(instance.baseURL + '/api/layouts/' + name + '?target=project', {
      data: {
        schema: 3, project: { id: 'pdf-layer-test', name },
        canvases: [{
          id: 'c1', name, schema: 2, page: { w: 140, h: 100 }, guides: [], updatedAt: 1,
          objects: [
            { type: 'panel', id: 'figure', name: 'Main figure', x: 15, y: 10, w: 70, h: 55,
              fileId, fileKind: 'pdf', nativeW: 100, nativeH: 80, overrides: [] },
            { type: 'panel', id: 'detail', name: 'Detail', x: 90, y: 10, w: 35, h: 27.5,
              fileId, fileKind: 'pdf', nativeW: 100, nativeH: 80, overrides: [] },
            { type: 'text', id: 'label', x: 15, y: 70, w: 55, h: 10,
              text: 'Canvas annotation', sizePt: 12, color: '#224466', bold: false, align: 'left' },
          ],
        }],
        activeCanvasId: 'c1', createdAt: 1, updatedAt: 1,
      },
    })
    expect(saved.ok(), await saved.text()).toBe(true)
    await page.goto(instance.baseURL)
    await waitForDocumentReady(page)
    await page.keyboard.press('ControlOrMeta+k')
    await page.locator('[data-palette-input]').fill('打开排版')
    await page.locator('[data-cmd-id="load-layout"]').click()
    await page.locator('[data-layout-row="LayeredCanvas"]').click()
    await expect(page.locator('[data-canvas-stage] [data-object-id="label"]')).toBeVisible()
    await page.locator('[data-onboarding-anchor="export"]').click()
    const dialog = page.locator('[data-export-group="format"]')
    const pdf = dialog.locator('[data-export-format="pdf"] input')
    if (!(await pdf.isChecked())) await pdf.check()
    const panelLayers = dialog.locator('[data-export-pdf-panel-layers]')
    const layer = dialog.locator('[data-export-pdf-annotation-layer]')
    const attachment = dialog.locator('[data-export-pdf-text-attachment]')
    await expect(panelLayers).toBeVisible()
    await expect(panelLayers).not.toBeChecked()
    await expect(layer).toBeVisible()
    await expect(layer).not.toBeChecked()
    await expect(attachment).toBeVisible()
    await expect(attachment).not.toBeChecked()
    await panelLayers.check()
    await layer.check()
    await attachment.check()
    await page.keyboard.press('Escape')
    await expect(layer).toBeHidden()
    await page.locator('[data-onboarding-anchor="export"]').click()
    if (!(await pdf.isChecked())) await pdf.check()
    await expect(panelLayers).not.toBeChecked()
    await expect(layer).not.toBeChecked()
    await expect(attachment).not.toBeChecked()
    await panelLayers.check()
    await layer.check()
    await attachment.check()
    const started = page.waitForResponse(r =>
      new URL(r.url()).pathname === '/api/export/start' && r.request().method() === 'POST')
    await page.locator('[data-export-start]').click()
    const job = await started
    expect(job.ok(), await job.text()).toBe(true)
    expect(job.request().postDataJSON().pdf_panel_layers).toBe(true)
    expect(job.request().postDataJSON().canvas.objects
      .filter((object: { type: string }) => object.type === 'panel')
      .map((object: { bookmark_title?: string }) => object.bookmark_title))
      .toEqual(['Main figure', 'Detail'])
    expect(job.request().postDataJSON().pdf_annotation_layer).toBe(true)
    expect(job.request().postDataJSON().pdf_text_attachment).toBe(true)
    const { export_dir } = await job.json()
    await expect(page.locator('[data-export-result]')).toBeVisible({ timeout: 60_000 })
    const files = readdirSync(export_dir).filter(file => file.endsWith('.pdf'))
    expect(files).toHaveLength(1)
    const published = readFileSync(path.join(export_dir, files[0])).toString('latin1')
    expect(published).toContain('/OCProperties')
    expect(published.match(/\/Type \/OCG\b/g)).toHaveLength(3)
    expect(published).toContain('/Name (1. Main figure)')
    expect(published).toContain('/Name (2. Detail)')
    expect(published).toContain('/Name (Canvas annotations)')
    expect(published).toContain('/EmbeddedFiles')
    expect(published).toContain('/Type /EmbeddedFile')
    expect(published).toContain('(tavotto-canvas-text.json)')
  } finally {
    await instance?.stop()
    rmSync(project, { recursive: true, force: true })
  }
})
