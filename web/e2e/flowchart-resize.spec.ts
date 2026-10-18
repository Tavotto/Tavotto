import { execFileSync } from 'node:child_process'
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

const REPO = path.resolve(import.meta.dirname, '..', '..')
const BOX = 'axes_0.patches_5'
const INNER = 'axes_0.patches_10'
const TEXT = 'axes_0.texts_5'
const ARROW = 'axes_0.texts_13.arrow'
const EPS_PX = 1.5

type Bounds = [number, number, number, number]
interface Element {
  gid: string
  bbox: Bounds
  shape_bounds?: Bounds
  arrow_endpoints?: [number, number][]
}
interface Render {
  manifest: { elements: Element[] }
  warnings?: string[]
}
interface SavedPanel {
  type: string
  fileId?: string
  overrides?: { gid: string; prop: string; value: unknown }[]
}

function flowchartLibrary(runtime = false): string {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'tavotto-resize-'))
  copyFileSync(path.join(REPO, 'tests', 'fixtures', 'flowchart_resize.py'), path.join(dir, 'fig_flow.py'))
  if (!runtime) {
    writeFileSync(path.join(dir, 'tavotto_registry.json'), JSON.stringify({
      scripts: { 'fig_flow.py': { entry: 'main', cost: 'light', stems: ['Flowchart'] } },
    }))
    execFileSync(process.env.TAVOTTO_WORKER_PYTHON || (process.platform === 'win32' ? 'python' : 'python3'),
      ['-c', 'import fig_flow; fig_flow.main()'], { cwd: dir, timeout: 120_000, env: { ...process.env, MPLBACKEND: 'Agg' } })
  }
  return dir
}

function captureRenders(page: Page) {
  const observed: { latest: Render | null; requests: number } = { latest: null, requests: 0 }
  page.on('request', (req) => {
    if (new URL(req.url()).pathname === '/api/engine/render') observed.requests++
  })
  page.on('response', async (res) => {
    if (new URL(res.url()).pathname !== '/api/engine/render') return
    try {
      const body = await res.json() as Render
      if (body.manifest) observed.latest = body
    } catch { /* An interrupted response has no new geometry. */ }
  })
  return observed
}

async function exact(page: Page, differentFrom?: string | null) {
  const host = page.locator('[data-display]')
  await expect(host).toHaveCount(1)
  await expect.poll(async () => {
    const key = await host.getAttribute('data-display-key')
    return await host.getAttribute('data-display') === 'exact'
      && (differentFrom === undefined || key !== differentFrom)
  }, { timeout: 120_000 }).toBe(true)
  await expect(page.locator('[data-authority="ready"]')).toHaveCount(1)
  return host.getAttribute('data-display-key')
}

async function openFigure(page: Page, baseURL: string, runtime = false) {
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto(baseURL)
  if (runtime) {
    // ▶ 就是「打开并运行」（V2 PR4）：点一次，检查 → 运行 → 一张图自动进入编辑，下面的断言等的就是它
    await page.locator('[data-script-run="fig_flow.py"]').click({ timeout: 30_000 })
  } else {
    await page.locator('[data-card="Flowchart.pdf"]').dblclick({ timeout: 120_000 })
  }
  await expect(page.locator('[data-element-svg] > svg')).toBeVisible({ timeout: 120_000 })
  await exact(page)
}

async function inkBox(page: Page, gid = BOX) {
  return page.locator('[data-element-svg]').evaluate((host, id) => {
    const node = host.querySelector(`[id="${id}"]`)
    if (!node) throw new Error(`Missing SVG artist ${id}`)
    const r = node.getBoundingClientRect()
    return { x: r.x, y: r.y, w: r.width, h: r.height }
  }, gid)
}

async function selectBox(page: Page, gid = BOX) {
  const b = await inkBox(page, gid)
  // Empty part of this box, away from its independent text and connector endpoints.
  await page.mouse.click(b.x + b.w * 0.15, b.y + b.h * 0.65)
  await expect(page.locator('[data-patch-resize]')).toHaveCount(8)
  await expect(page.locator('[data-patch-size-block]')).toBeVisible()
}

async function beginResize(page: Page, edge: string, dx: number, dy: number) {
  const handle = page.locator(`[data-patch-resize="${edge}"]`)
  await expect(handle).toHaveCount(1)
  await expect(handle).toBeInViewport()
  const b = (await handle.boundingBox())!
  const start = { x: b.x + b.width / 2, y: b.y + b.height / 2 }
  const viewport = page.viewportSize()!
  expect(b.x).toBeGreaterThanOrEqual(0)
  expect(b.y).toBeGreaterThanOrEqual(0)
  expect(b.x + b.width).toBeLessThanOrEqual(viewport.width)
  expect(b.y + b.height).toBeLessThanOrEqual(viewport.height)
  expect(await handle.evaluate((node, p) => node.contains(document.elementFromPoint(p.x, p.y)), start)).toBe(true)
  await page.mouse.move(start.x, start.y)
  await page.mouse.down()
  await page.mouse.move(start.x + dx, start.y + dy, { steps: 8 })
  return start
}

function element(render: Render | null, gid: string) {
  const found = render?.manifest.elements.find((e) => e.gid === gid)
  if (!found) throw new Error(`Missing manifest artist ${gid}`)
  return found
}

function expectSameInk(a: Awaited<ReturnType<typeof inkBox>>, b: Awaited<ReturnType<typeof inkBox>>) {
  for (const key of ['x', 'y', 'w', 'h'] as const) expect(Math.abs(a[key] - b[key]), key).toBeLessThan(EPS_PX)
}

function savedRuntime(dataDir: string) {
  const dir = path.join(dataDir, 'layouts', '_autosave')
  if (!existsSync(dir)) return null
  for (const name of readdirSync(dir).filter((n) => n.endsWith('.json'))) {
    const doc = JSON.parse(readFileSync(path.join(dir, name), 'utf-8'))
    for (const canvas of doc.canvases ?? []) {
      const panel = (canvas.objects as SavedPanel[]).find((p) =>
        p.type === 'panel' && p.fileId === 'runtime:fig_flow.py#Flowchart'
        && p.overrides?.some((o) => o.gid === BOX && o.prop === 'size_frac'))
      if (panel) return panel
    }
  }
  return null
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('tavotto.ui', JSON.stringify({ prefsVersion: 2, snapEnabled: false }))
  })
})

test('流程图框：八方向手柄和精确宽高改变真图，文字箭头不变，撤销重做恢复',
  { tag: '@feature:figure.patch-resize' }, async ({ app, page }, testInfo) => {
    test.setTimeout(300_000)
    const a = await app({ figures: flowchartLibrary() })
    const renders = captureRenders(page)
    await openFigure(page, a.baseURL)
    await expect.poll(() => renders.latest?.manifest.elements.filter((e) => e.shape_bounds).length).toBe(12)
    await selectBox(page)
    const initial = await inkBox(page)
    const text = await inkBox(page, TEXT)
    const arrow = element(renders.latest, ARROW).arrow_endpoints
    const before = await exact(page)
    const requests = renders.requests

    await beginResize(page, 'se', 45, 22)
    expect(renders.requests).toBe(requests)
    await page.mouse.up()
    const resizedKey = await exact(page, before)
    expect(renders.requests).toBe(requests + 1)
    const resized = await inkBox(page)
    expect(Math.abs(resized.w - initial.w - 45)).toBeLessThan(EPS_PX)
    expect(Math.abs(resized.h - initial.h - 22)).toBeLessThan(EPS_PX)
    expect(Math.abs(resized.x - initial.x)).toBeLessThan(EPS_PX)
    expect(Math.abs(resized.y - initial.y)).toBeLessThan(EPS_PX)
    expectSameInk(await inkBox(page, TEXT), text)
    expect(element(renders.latest, ARROW).arrow_endpoints).toEqual(arrow)
    expect(renders.latest?.warnings ?? []).toEqual([])

    await page.keyboard.press('ControlOrMeta+z')
    await exact(page, resizedKey)
    expectSameInk(await inkBox(page), initial)
    await page.keyboard.press('ControlOrMeta+Shift+z')
    await exact(page, before)
    expectSameInk(await inkBox(page), resized)

    // Values are physical mm on the current page. Keep the upper left corner fixed.
    const width = page.locator('[data-inspector-prop="patch_width_mm"]')
    const height = page.locator('[data-inspector-prop="patch_height_mm"]')
    const w0 = Number(await width.inputValue())
    const h0 = Number(await height.inputValue())
    const keyW = await exact(page)
    await width.fill(String(w0 + 8))
    await width.press('Enter')
    await exact(page, keyW)
    await expect.poll(async () => Math.abs(Number(await width.inputValue()) - w0 - 8)).toBeLessThan(0.04)
    const keyH = await exact(page)
    await height.fill(String(h0 + 5))
    await height.press('Enter')
    await exact(page, keyH)
    const precise = await inkBox(page)
    expect(Math.abs(precise.w - resized.w - 8 * resized.w / w0)).toBeLessThan(EPS_PX)
    expect(Math.abs(precise.h - resized.h - 5 * resized.h / h0)).toBeLessThan(EPS_PX)
    expect(Math.abs(precise.x - resized.x)).toBeLessThan(EPS_PX)
    expect(Math.abs(precise.y - resized.y)).toBeLessThan(EPS_PX)
    expectSameInk(await inkBox(page, TEXT), text)
    expect(element(renders.latest, ARROW).arrow_endpoints).toEqual(arrow)
    await page.screenshot({ path: testInfo.outputPath('flowchart-resize.png') })

    // Every handle must really resize the SVG, with the opposite edge fixed.
    for (const dir of ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w']) {
      const dx = dir.includes('w') ? -12 : dir.includes('e') ? 12 : 0
      const dy = dir.includes('n') ? -10 : dir.includes('s') ? 10 : 0
      const key0 = await exact(page)
      await beginResize(page, dir, dx, dy)
      await page.mouse.up()
      const key1 = await exact(page, key0)
      const changed = await inkBox(page)
      expect(Math.abs(changed.w - precise.w - Math.abs(dx)), dir).toBeLessThan(EPS_PX)
      expect(Math.abs(changed.h - precise.h - Math.abs(dy)), dir).toBeLessThan(EPS_PX)
      expect(Math.abs(changed.x - precise.x - Math.min(dx, 0)), dir).toBeLessThan(EPS_PX)
      expect(Math.abs(changed.y - precise.y - Math.min(dy, 0)), dir).toBeLessThan(EPS_PX)
      expectSameInk(await inkBox(page, TEXT), text)
      await page.keyboard.press('ControlOrMeta+z')
      await exact(page, key1)
      expectSameInk(await inkBox(page), precise)
    }

    // Nested child boxes expose their own handles and do not resize the parent.
    const outer = await inkBox(page, 'axes_0.patches_9')
    await selectBox(page, INNER)
    const inner0 = await inkBox(page, INNER)
    const keyInner = await exact(page)
    await beginResize(page, 'e', -18, 0)
    await page.mouse.up()
    await exact(page, keyInner)
    expect(Math.abs((await inkBox(page, INNER)).w - inner0.w + 18)).toBeLessThan(EPS_PX)
    expectSameInk(await inkBox(page, 'axes_0.patches_9'), outer)
  })

test('流程图框：取消和拖回起点不保存尺寸改动', { tag: '@feature:figure.patch-resize' }, async ({ app, page }) => {
  const a = await app({ figures: flowchartLibrary() })
  const renders = captureRenders(page)
  await openFigure(page, a.baseURL)
  await selectBox(page)
  const initial = await inkBox(page)
  const key = await exact(page)
  const requests = renders.requests
  await beginResize(page, 'nw', -30, -20)
  await page.keyboard.press('Escape')
  await page.mouse.up()
  expect(await exact(page)).toBe(key)
  expectSameInk(await inkBox(page), initial)

  await selectBox(page)
  const start = await beginResize(page, 'se', 30, 20)
  await page.mouse.move(start.x, start.y, { steps: 8 })
  await page.mouse.up()
  expect(await exact(page)).toBe(key)
  expectSameInk(await inkBox(page), initial)
  expect(renders.requests).toBe(requests)
})

test('流程图框：单脚本运行图的尺寸保存到磁盘，冷重开后继续编辑并导出', { tag: '@feature:figure.patch-resize' }, async ({ app, page }) => {
  test.setTimeout(600_000)
  const figures = flowchartLibrary(true)
  const originalScript = readFileSync(path.join(figures, 'fig_flow.py'))
  const shared = mkdtempSync(path.join(os.tmpdir(), 'tavotto-resize-session-'))
  const env = {
    TAVOTTO_DATA_DIR: path.join(shared, 'data'),
    TAVOTTO_CONFIG_DIR: path.join(shared, 'config'),
  }
  mkdirSync(env.TAVOTTO_DATA_DIR, { recursive: true })
  const a1 = await app({ figures, env })
  const renders = captureRenders(page)
  await openFigure(page, a1.baseURL, true)
  await selectBox(page)
  const key = await exact(page)
  await beginResize(page, 'w', -28, 0)
  await page.mouse.up()
  await exact(page, key)
  const target = element(renders.latest, BOX).shape_bounds!
  const text = element(renders.latest, TEXT).bbox
  await page.keyboard.press('ControlOrMeta+s')
  await expect.poll(() => savedRuntime(env.TAVOTTO_DATA_DIR), { timeout: 30_000 }).not.toBeNull()
  const saved = savedRuntime(env.TAVOTTO_DATA_DIR)!
  // Carry only the index/preferences across the changed port, never the browser's document fallback.
  const storage = await page.evaluate(() => Object.fromEntries(Object.entries(localStorage)
    .filter(([k]) => k.startsWith('tavotto') && !k.startsWith('tavotto.autosave.'))))
  await a1.stop()

  const a2 = await app({ figures, env })
  const reopened = captureRenders(page)
  await page.addInitScript((entries: Record<string, string>) => {
    for (const [k, v] of Object.entries(entries)) localStorage.setItem(k, v)
  }, storage)
  const diskResponse = page.waitForResponse((r) => r.request().method() === 'GET'
    && /^\/api\/autosave\/[^/]+$/.test(new URL(r.url()).pathname) && r.ok(), { timeout: 90_000 })
  await page.goto(a2.baseURL)
  const diskDoc = await (await diskResponse).json()
  expect(diskDoc.canvases.flatMap((c: { objects: SavedPanel[] }) => c.objects)
    .find((o: SavedPanel) => o.fileId === saved.fileId)?.overrides).toEqual(saved.overrides)
  const object = page.locator('[data-canvas-stage] [data-object-id]')
  await expect(object).toHaveCount(1, { timeout: 90_000 })
  await object.dblclick()
  await exact(page)
  await expect.poll(() => reopened.latest?.manifest.elements.find((e) => e.gid === BOX)?.shape_bounds).toEqual(target)
  expect(reopened.requests).toBeGreaterThan(0)
  expect(element(reopened.latest, TEXT).bbox).toEqual(text)
  await selectBox(page)
  await expect(page.locator('[data-inspector-prop="patch_width_mm"]')).toBeEnabled()
  const reopenedKey = await exact(page)
  const reopenedInk = await inkBox(page)
  await beginResize(page, 'e', 16, 0)
  await page.mouse.up()
  await exact(page, reopenedKey)
  expect(Math.abs((await inkBox(page)).w - reopenedInk.w - 16)).toBeLessThan(EPS_PX)
  await expect.poll(() => JSON.stringify(savedRuntime(env.TAVOTTO_DATA_DIR)?.overrides),
    { timeout: 30_000 }).not.toBe(JSON.stringify(saved.overrides))
  const exportSaved = savedRuntime(env.TAVOTTO_DATA_DIR)!

  const response = await page.request.post(`${a2.baseURL}/api/export`, { data: {
    page_w_mm: 330.2, page_h_mm: 279.4, formats: ['pdf', 'png'], stem: 'flowchart_resized',
    objects: [{ type: 'panel', id: exportSaved.fileId, x_mm: 0, y_mm: 0,
      w_mm: 330.2, h_mm: 279.4, overrides: exportSaved.overrides }],
  }, timeout: 300_000 })
  expect(response.ok()).toBe(true)
  const result = await response.json()
  expect(result.warnings ?? []).toEqual([])
  expect(result.files).toHaveLength(2)
  for (const f of result.files as { name: string }[]) {
    const bytes = readFileSync(path.join(result.export_dir, f.name))
    expect(bytes.length).toBeGreaterThan(1000)
    if (f.name.endsWith('.pdf')) expect(bytes.subarray(0, 5).toString()).toBe('%PDF-')
    if (f.name.endsWith('.png')) expect([...bytes.subarray(0, 8)]).toEqual([137, 80, 78, 71, 13, 10, 26, 10])
  }
  expect(readFileSync(path.join(figures, 'fig_flow.py'))).toEqual(originalScript)
})
