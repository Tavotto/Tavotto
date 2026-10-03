import type { APIRequestContext, Locator, Page, Response } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { expect, test } from './fixtures'

/** Real saved PNGs, real UI admission, and real disk identity. No injected stores or mocked routes. */
const FILE = 'same.png'
const POLICY = 'selected-figsize-v1'
const FRAME = { gid: 'figure', prop: 'frame', value: 'figsize' }
const TITLE = 'axes_0.title'
const VIEWPORT = { width: 1600, height: 1100 }
const SOURCE_CHANGED = 'The source file changed. Your edits are preserved.'

test.use({ viewport: VIEWPORT, locale: 'en-US' })

type Patch = { gid: string; prop: string; value: unknown; identity?: string }
type Guard = { version: 1; requiredFrame: 'figsize'; sourceId: string; bytesSha256: string; sizeBytes: number }
type Panel = {
  id: string; type: 'panel'; fileId: string; fileKind: 'raster'; script: string; cost: string
  x: number; y: number; w: number; h: number; nativeW: number; nativeH: number
  pxW: number; pxH: number; crop: { x: number; y: number; w: number; h: number }
  figureFrame: 1; overrides: Patch[]; artifactValidation?: Guard
}
type Saved = { canvases: { id: string; objects: Panel[] }[] }
type PanelInfo = {
  id: string; kind: string; script: string; cost: string
  native_w_mm: number; native_h_mm: number; px_w: number; px_h: number
}
type RenderRequest = {
  id: string; patches: Patch[]; source_policy?: string
  expected_source?: { bytes_sha256: string; size_bytes: number }; with_manifest?: boolean
}
type Manifest = {
  size_mm: [number, number]
  elements: { gid: string; editable: { prop: string; value: unknown }[] }[]
}
type RenderBody = {
  manifest?: Manifest; png?: string; svg?: string
  artifact_source?: { render_policy: string; origin: string; kind: string; bytes_sha256: string; size_bytes: number }
  code?: string; params?: { reason?: string }
}
type RenderRecord = { endpoint: string; request: RenderRequest; body: RenderBody; status: number }

const workerPython = () => process.env.TAVOTTO_WORKER_PYTHON || (process.platform === 'win32' ? 'python' : 'python3')
const sha256 = (bytes: Buffer) => createHash('sha256').update(bytes).digest('hex')

function sourceProject(root: string, tightLayout: boolean) {
  const figures = path.join(root, 'figures')
  const staging = path.join(root, 'staging')
  mkdirSync(figures, { recursive: true })
  mkdirSync(staging)
  const tmp = path.join(root, 'tmp')
  const cache = path.join(root, 'cache')
  mkdirSync(tmp)
  mkdirSync(cache)
  const script = [
    'import matplotlib',
    'matplotlib.use("Agg")',
    'import matplotlib.pyplot as plt',
    '',
    'def main():',
    '    fig, ax = plt.subplots(figsize=(4, 3))',
    '    ax.plot([0, 1, 2, 3], [0.2, 0.8, 0.4, 0.9])',
    '    ax.set_title("Guarded source", fontsize=12)',
    '    ax.set_xlabel("Time")',
    '    ax.set_ylabel("Response")',
    tightLayout ? '    fig.tight_layout()' : '    fig.subplots_adjust(left=0.18, bottom=0.20, right=0.92, top=0.82)',
    '    fig.savefig("same.pdf", bbox_inches="tight", pad_inches=0.02)',
    `    fig.savefig("same.png", bbox_inches=None, dpi=${tightLayout ? 100 : 72}, transparent=${tightLayout ? 'False' : 'True'})`,
    '    plt.close(fig)',
    '',
  ].join('\n')
  writeFileSync(path.join(figures, 'figure.py'), script)
  // Match the project's canonical serializer so the byte check measures content changes.
  writeFileSync(path.join(figures, 'tavotto_registry.json'), JSON.stringify({
    scripts: { 'figure.py': { entry: 'main', cost: 'light', stems: ['same'] } },
  }, null, 1))
  // Generate both originals with the scientific worker, never the Flask-only interpreter.
  execFileSync(workerPython(), ['-c', 'import figure; figure.main()'], {
    cwd: figures, timeout: 120_000,
    env: {
      ...process.env, MPLCONFIGDIR: path.join(root, 'mplconfig'), XDG_CACHE_HOME: cache,
      TMPDIR: tmp, TMP: tmp, TEMP: tmp, OPENBLAS_NUM_THREADS: '1', OMP_NUM_THREADS: '1',
    },
  })
  const png = readFileSync(path.join(figures, FILE))
  const pdf = readFileSync(path.join(figures, 'same.pdf'))
  const registry = readFileSync(path.join(figures, 'tavotto_registry.json'))
  renameSync(path.join(figures, 'same.pdf'), path.join(staging, 'same.pdf'))
  return {
    figures, png, pdf,
    guard: { version: 1, requiredFrame: 'figsize', sourceId: FILE, bytesSha256: sha256(png), sizeBytes: png.length } as Guard,
    revealPdf() { renameSync(path.join(staging, 'same.pdf'), path.join(figures, 'same.pdf')) },
    expectUnchanged(expectedPng = png) {
      expect(readFileSync(path.join(figures, FILE))).toEqual(expectedPng)
      expect(readFileSync(path.join(figures, 'same.pdf'))).toEqual(pdf)
      expect(readFileSync(path.join(figures, 'figure.py'), 'utf8')).toBe(script)
      expect(readFileSync(path.join(figures, 'tavotto_registry.json'))).toEqual(registry)
    },
  }
}

async function only(locator: Locator) {
  await expect(locator).toHaveCount(1)
  return locator
}
const object = (page: Page, id: string) => page.locator(`[data-object-id="${id}"]`)
const currentImage = (page: Page, id: string) => object(page, id).locator('[data-display] img:not([aria-hidden="true"])')
const endpoint = (r: Response, pathname: string, method = 'POST') =>
  new URL(r.url()).pathname === pathname && r.request().method() === method

function watchRenders(page: Page) {
  const requests: { endpoint: string; request: RenderRequest }[] = []
  const records: RenderRecord[] = []
  const paths = ['/api/engine/render', '/api/engine/preview_png']
  page.on('request', r => {
    const pathname = new URL(r.url()).pathname
    if (r.method() === 'POST' && paths.includes(pathname)) requests.push({ endpoint: pathname, request: r.postDataJSON() })
  })
  page.on('response', async r => {
    const pathname = new URL(r.url()).pathname
    if (r.request().method() !== 'POST' || !paths.includes(pathname)) return
    try {
      records.push({ endpoint: pathname, request: r.request().postDataJSON(), body: await r.json(), status: r.status() })
    } catch { /* Aborted preview requests have no response body; positive witnesses below remain required. */ }
  })
  return { requests, records }
}

async function seed(request: APIRequestContext, baseURL: string, ids: string[], docId: string) {
  const projects = await request.get(`${baseURL}/api/projects`)
  expect(projects.ok()).toBe(true)
  const { default: pj } = await projects.json() as { default: string }
  expect(pj).toBeTruthy()
  const query = `?pj=${encodeURIComponent(pj)}`
  const panelsResponse = await request.get(`${baseURL}/api/panels${query}`)
  expect(panelsResponse.ok()).toBe(true)
  const { panels } = await panelsResponse.json() as { panels: PanelInfo[] }
  expect(panels.map(p => p.id)).toEqual([FILE])
  const info = panels[0]
  expect(info).toMatchObject({ kind: 'raster', script: 'figure.py', cost: 'light' })
  expect(info.px_w).toBeGreaterThan(0)
  expect(info.px_h).toBeGreaterThan(0)
  const objects: Panel[] = ids.map((id, index) => ({
    id, type: 'panel', fileId: FILE, fileKind: 'raster', script: info.script, cost: info.cost,
    nativeW: info.native_w_mm, nativeH: info.native_h_mm, pxW: info.px_w, pxH: info.px_h,
    x: 15 + 105 * index, y: 20, w: 84, h: 63,
    crop: { x: 0.025, y: 0.015, w: 0.95, h: 0.95 }, figureFrame: 1, overrides: [],
  }))
  const doc = {
    schema: 3, project: { id: docId, name: 'Guarded PNG acceptance' },
    canvases: [{ id: 'c1', name: 'Guarded PNG', page: { w: 225, h: 110 }, objects, guides: [] }],
    activeCanvasId: 'c1', createdAt: 1756800000000, updatedAt: 1756800000000,
  }
  expect((await request.put(`${baseURL}/api/autosave/${docId}${query}`, { data: doc })).ok()).toBe(true)
  expect((await request.put(`${baseURL}/api/layout-session/last${query}`, {
    data: { doc_id: docId, name: 'Guarded PNG acceptance' },
  })).ok()).toBe(true)
  expect((await request.put(`${baseURL}/api/preferences/export-defaults`, {
    data: { formats: ['png'], dpi: '100', withProof: false, strictInspection: false },
  })).ok()).toBe(true)
  const saved = async () => {
    const r = await request.get(`${baseURL}/api/autosave/${docId}${query}`)
    expect(r.ok()).toBe(true)
    const doc = await r.json() as Saved
    expect(doc.canvases).toHaveLength(1)
    expect(doc.canvases[0].objects).toHaveLength(ids.length)
    return doc.canvases[0].objects
  }
  const savedPanel = async (id: string) => {
    const matches = (await saved()).filter(p => p.id === id)
    expect(matches).toHaveLength(1)
    return matches[0]
  }
  return { pj, query, objects, saved, savedPanel }
}

type Seed = Awaited<ReturnType<typeof seed>>
function placement(p: Panel) {
  const { id, fileId, fileKind, script, cost, x, y, w, h, crop, pxW, pxH, figureFrame } = p
  return { id, fileId, fileKind, script, cost, x, y, w, h, crop, pxW, pxH, figureFrame }
}
function expectedSource(guard: Guard) {
  return { source_policy: POLICY, expected_source: { bytes_sha256: guard.bytesSha256, size_bytes: guard.sizeBytes } }
}
async function settled(page: Page) {
  await expect(page.locator('[data-view-tweening]')).toHaveCount(0)
  // Sidebar CSS transitions are separate from the world-transform tween.
  await page.waitForTimeout(400)
  await expect(page.locator('[data-view-tweening]')).toHaveCount(0)
}
async function paintedOriginal(page: Page, id: string, bytes: Buffer) {
  const img = await only(currentImage(page, id))
  await expect(img).toBeVisible()
  await expect.poll(() => img.evaluate(n => (n as HTMLImageElement).complete && (n as HTMLImageElement).naturalWidth > 0)).toBe(true)
  const src = await img.getAttribute('src')
  expect(src).toBeTruthy()
  const url = new URL(src!, page.url())
  expect(url.pathname).toBe('/api/file')
  expect(url.searchParams.get('id')).toBe(FILE)
  const response = await page.request.get(url.href)
  expect(response.ok()).toBe(true)
  expect(await response.body()).toEqual(bytes)
}
async function revealCompanion(page: Page, source: ReturnType<typeof sourceProject>) {
  source.revealPdf()
  const refreshed = page.waitForResponse(r => endpoint(r, '/api/panels', 'GET') && r.ok())
  await (await only(page.locator('[data-asset-refresh]'))).click()
  const { panels } = await (await refreshed).json() as { panels: PanelInfo[] }
  expect(panels.map(p => p.id)).toContain('same.pdf')
  expect(panels.map(p => p.id)).not.toContain(FILE)
  await expect(page.locator('[data-card="same.pdf"]')).toHaveCount(1)
  await expect(page.locator('[data-card="same.png"]')).toHaveCount(0)
}
async function enter(page: Page, id: string, guard?: Guard) {
  await (await only(object(page, id))).click({ button: 'right' })
  const menu = await only(page.locator('[data-quick-menu="panel"]'))
  await expect(menu).toBeVisible()
  const admitted = page.waitForResponse(r => endpoint(r, '/api/engine/render') &&
    r.request().postDataJSON().id === FILE && r.request().postDataJSON().patches.length === 0)
  await (await only(menu.locator('[data-quick-item="edit-elements"]'))).click()
  const response = await admitted
  const req = response.request().postDataJSON() as RenderRequest
  expect(req).toMatchObject({ id: FILE, patches: [], source_policy: POLICY })
  if (guard) expect(req).toMatchObject(expectedSource(guard))
  else expect(req.expected_source).toBeUndefined()
  return response
}
async function exact(page: Page, id: string) {
  const host = await only(object(page, id))
  const display = await only(host.locator('[data-display]'))
  await expect(display).toHaveAttribute('data-display', 'exact', { timeout: 120_000 })
  await expect(display).toHaveAttribute('data-display-key', /.+/)
  await expect(await only(host.locator('[data-authority]'))).toHaveAttribute('data-authority', 'ready', { timeout: 120_000 })
  const svg = await only(host.locator(`[data-element-svg="${id}"] > svg`))
  await expect(svg).toBeVisible()
  return (await display.getAttribute('data-display-key'))!
}
async function exitEdit(page: Page) {
  await (await only(page.locator('[data-exit-element-edit]'))).click()
  await expect(page.locator('[data-element-svg]')).toHaveCount(0)
}
async function screenGeometry(page: Page, id: string, content: Locator) {
  const panel = await only(object(page, id))
  const sheet = await only(page.locator('[data-page-sheet]'))
  const world = await only(page.locator('[data-world-transform]'))
  const rect = async (loc: Locator) => {
    const b = await loc.boundingBox()
    expect(b).not.toBeNull()
    return { left: b!.x, top: b!.y, right: b!.x + b!.width, bottom: b!.y + b!.height }
  }
  return {
    panel: await rect(panel), sheet: await rect(sheet), content: await rect(await only(content)),
    transform: await world.evaluate(n => getComputedStyle(n).transform),
  }
}
async function openOriginalExport(page: Page, id: string) {
  await (await only(object(page, id))).click()
  await page.keyboard.press('ControlOrMeta+e')
  const dialog = await only(page.locator('[data-dialog="export"]'))
  await expect(dialog).toBeVisible()
  await (await only(dialog.locator('[data-onboarding-anchor="export-scope"] [data-value="original"]'))).click()
  // The displayed name is a stem; identity belongs to the actual source thumbnail and request.
  const target = await only(dialog.locator('[data-export-target]'))
  const thumb = await only(target.locator('img'))
  await expect.poll(async () => {
    const src = await thumb.getAttribute('src')
    return src ? new URL(src, page.url()).searchParams.get('id') : null
  }).toBe(FILE)
  const confirm = dialog.locator('input[data-export-confirm]')
  if (await confirm.count()) await (await only(confirm)).check()
  // Deliberately stays red on the old asset-list-only sourceReachable predicate.
  await expect(await only(dialog.locator('[data-export-start]'))).toBeEnabled()
  return dialog
}
function titleSize(manifest: Manifest) {
  const elements = manifest.elements.filter(e => e.gid === TITLE)
  expect(elements).toHaveLength(1)
  const fields = elements[0].editable.filter(p => p.prop === 'fontsize')
  expect(fields).toHaveLength(1)
  return fields[0].value
}
async function fullRender(records: RenderRecord[], patches: Patch[], guard: Guard) {
  const matches = () => records.filter(r => r.endpoint === '/api/engine/render' && r.status === 200 &&
    JSON.stringify(r.request.patches) === JSON.stringify(patches))
  await expect.poll(() => matches().length, { timeout: 120_000 }).toBeGreaterThan(0)
  const record = matches().at(-1)!
  expect(record.request).toMatchObject({ id: FILE, ...expectedSource(guard) })
  expect(record.body.manifest).toBeTruthy()
  return record.body.manifest!
}

/** Decode actual PNG pixels; URL identity and compressed bytes are not the visual oracle. */
async function decodedPixels(page: Page, png: string) {
  return page.evaluate(async data => {
    const img = new Image()
    img.src = `data:image/png;base64,${data}`
    await img.decode()
    const canvas = document.createElement('canvas')
    canvas.width = img.naturalWidth
    canvas.height = img.naturalHeight
    const ctx = canvas.getContext('2d')!
    ctx.drawImage(img, 0, 0)
    const rgba = ctx.getImageData(0, 0, canvas.width, canvas.height).data
    const digest = await crypto.subtle.digest('SHA-256', rgba)
    return { w: canvas.width, h: canvas.height, rgba: Array.from(new Uint8Array(digest)).map(n => n.toString(16).padStart(2, '0')).join('') }
  }, png)
}
async function paintedPreview(page: Page, id: string, records: RenderRecord[], panel: Panel) {
  await exitEdit(page)
  const matches = () => records.filter(r => r.endpoint === '/api/engine/preview_png' && r.status === 200 &&
    JSON.stringify(r.request.patches) === JSON.stringify(panel.overrides))
  await expect.poll(() => matches().length, { timeout: 120_000 }).toBeGreaterThan(0)
  const img = await only(currentImage(page, id))
  await expect(img).toBeVisible()
  // Match the response actually mounted now, including a valid cached preview on re-entry.
  let paired: RenderRecord | undefined
  await expect.poll(async () => {
    const src = await img.getAttribute('src')
    paired = matches().find(r => src === `data:image/png;base64,${r.body.png}`)
    return !!paired
  }, { timeout: 120_000 }).toBe(true)
  expect(paired!.request).toMatchObject({ id: FILE, with_manifest: true, ...expectedSource(panel.artifactValidation!) })
  expect(paired!.body.png).toBeTruthy()
  expect(titleSize(paired!.body.manifest!)).toBe(panel.overrides.find(p => p.gid === TITLE && p.prop === 'fontsize')!.value)
  await expect.poll(() => img.evaluate(n => (n as HTMLImageElement).complete && (n as HTMLImageElement).naturalWidth > 0)).toBe(true)
  await expect(await only(object(page, id).locator('[data-display]'))).toHaveAttribute('data-display', 'exact')
  return decodedPixels(page, paired!.body.png!)
}
async function changeTitle(page: Page, id: string, data: Seed, records: RenderRecord[], guard: Guard, size: number) {
  await exact(page, id)
  await settled(page)
  const title = await only(object(page, id).locator(`[data-element-svg="${id}"] [id="${TITLE}"]`))
  const before = await title.boundingBox()
  expect(before).not.toBeNull()
  await page.mouse.click(before!.x + before!.width / 2, before!.y + before!.height / 2)
  const input = await only(page.locator('input[data-inspector-prop="fontsize"]'))
  await expect(input).toBeVisible()
  await input.fill(String(size))
  await input.press('Enter')
  await expect.poll(async () => (await data.savedPanel(id)).overrides.some(p => p.gid === TITLE && p.prop === 'fontsize'), { timeout: 30_000 }).toBe(true)
  const panel = await data.savedPanel(id)
  expect(panel.overrides).toHaveLength(2)
  expect(panel.overrides).toContainEqual(FRAME)
  const manifest = await fullRender(records, panel.overrides, guard)
  expect(titleSize(manifest)).toBe(panel.overrides.find(p => p.gid === TITLE && p.prop === 'fontsize')!.value)
  const key = await exact(page, id)
  await expect(input).toHaveValue(String(size))
  await expect.poll(async () => (await title.boundingBox())!.height).toBeGreaterThan(before!.height)
  return { panel, key }
}

function replaceAncillaryText(figures: string) {
  // Insert a valid tEXt chunk only. Keep IHDR, IDAT, pHYs, alpha, and all original chunks verbatim.
  execFileSync(workerPython(), ['-c', [
    'from pathlib import Path',
    'import binascii, io, struct',
    'from PIL import Image',
    'p = Path("same.png")',
    'old = p.read_bytes()',
    'assert old[-12:] == bytes.fromhex("0000000049454e44ae426082")',
    'text = b"tEXt" + b"GuardedAcceptance\\0changed identity, same pixels"',
    'chunk = struct.pack(">I", len(text) - 4) + text + struct.pack(">I", binascii.crc32(text) & 0xffffffff)',
    'new = old[:-12] + chunk + old[-12:]',
    'a, b = Image.open(io.BytesIO(old)), Image.open(io.BytesIO(new))',
    'assert a.mode == b.mode and a.size == b.size and a.tobytes() == b.tobytes()',
    'assert a.info.get("dpi") == b.info.get("dpi")',
    'p.write_bytes(new)',
  ].join('\n')], { cwd: figures, timeout: 30_000 })
  return readFileSync(path.join(figures, FILE))
}

test('guarded PNG: hidden companion, stable adoption, undo, re-entry, and exact original copy', async ({ app, page }, testInfo) => {
  test.setTimeout(300_000)
  const source = sourceProject(testInfo.outputPath('source'), false)
  const a = await app({ figures: source.figures })
  const data = await seed(page.request, a.baseURL, ['a'], 'guarded-png-copy')
  const traffic = watchRenders(page)
  await page.goto(`${a.baseURL}/${data.query}`)
  await expect(await only(object(page, 'a'))).toBeVisible({ timeout: 60_000 })
  await paintedOriginal(page, 'a', source.png)
  expect(await data.savedPanel('a')).toEqual(data.objects[0])
  expect(traffic.requests).toEqual([])

  await revealCompanion(page, source)
  await paintedOriginal(page, 'a', source.png)
  expect(await data.savedPanel('a')).toEqual(data.objects[0])
  expect(traffic.requests).toEqual([])
  await object(page, 'a').click()
  await settled(page)
  const before = await screenGeometry(page, 'a', currentImage(page, 'a'))
  const admission = await enter(page, 'a')
  expect(admission.ok()).toBe(true)
  const body = await admission.json() as RenderBody
  expect(body.artifact_source).toMatchObject({
    render_policy: POLICY, origin: 'static', kind: 'png', bytes_sha256: source.guard.bytesSha256, size_bytes: source.png.length,
  })
  expect(body.manifest!.size_mm[0]).toBeCloseTo(101.6, 6)
  expect(body.manifest!.size_mm[1]).toBeCloseTo(76.2, 6)
  await exact(page, 'a')
  await expect.poll(async () => (await data.savedPanel('a')).artifactValidation).toEqual(source.guard)
  const adopted = await data.savedPanel('a')
  expect(adopted.overrides).toEqual([FRAME])
  expect(placement(adopted)).toEqual(placement(data.objects[0]))
  expect([adopted.nativeW, adopted.nativeH]).toEqual(body.manifest!.size_mm)
  await fullRender(traffic.records, [FRAME], source.guard)
  await settled(page)
  const after = await screenGeometry(page, 'a', object(page, 'a').locator('[data-element-svg="a"]'))
  expect(after.transform).toBe(before.transform)
  for (const part of ['panel', 'sheet', 'content'] as const) {
    for (const edge of ['left', 'top', 'right', 'bottom'] as const) {
      expect(Math.abs(after[part][edge] - before[part][edge]), `${part}.${edge} moved on admission`).toBeLessThanOrEqual(0.5)
    }
  }

  const undoStart = traffic.requests.length
  await (await only(page.locator('[data-canvas-stage]'))).focus()
  await page.keyboard.press('ControlOrMeta+z')
  await expect(page.locator('[data-exit-element-edit]')).toHaveCount(0)
  await expect(page.locator('[data-element-svg]')).toHaveCount(0)
  await expect.poll(() => data.savedPanel('a')).toEqual(data.objects[0])
  await paintedOriginal(page, 'a', source.png)
  expect(traffic.requests.slice(undoStart)).toEqual([])
  await page.keyboard.press('ControlOrMeta+Shift+z')
  await expect.poll(() => data.savedPanel('a')).toEqual(adopted)
  await expect(page.locator('[data-exit-element-edit]')).toHaveCount(0)
  expect((await enter(page, 'a', source.guard)).ok()).toBe(true)
  await exact(page, 'a')
  await exitEdit(page)

  const dialog = await openOriginalExport(page, 'a')
  const preflight = page.waitForResponse(r => endpoint(r, '/api/export/validate'))
  const started = page.waitForResponse(r => endpoint(r, '/api/export/start'))
  await dialog.locator('[data-export-start]').click()
  const validation = await preflight
  expect(validation.ok()).toBe(true)
  expect((await validation.json()).artifact_sources[FILE]).toMatchObject({
    render_policy: POLICY, bytes_sha256: source.guard.bytesSha256, size_bytes: source.png.length,
  })
  const start = await started
  expect(start.ok()).toBe(true)
  expect(start.request().postDataJSON()).toMatchObject({ scope: 'original', formats: ['png'], original: { figure_id: FILE, ...expectedSource(source.guard) } })
  expect(start.request().postDataJSON().original).not.toHaveProperty('overrides')
  const job = await start.json() as { job_id: string; export_dir: string }
  let result: { status: string; outputs: { status: string; format: string; name: string }[] } | undefined
  await expect.poll(async () => {
    const r = await page.request.get(`${a.baseURL}/api/export/state${data.query}&job_id=${encodeURIComponent(job.job_id)}`)
    expect(r.ok()).toBe(true)
    result = await r.json()
    return result!.status
  }, { timeout: 120_000 }).toBe('done')
  expect(result!.outputs).toHaveLength(1)
  expect(result!.outputs[0]).toMatchObject({ status: 'done', format: 'png' })
  await expect(dialog).toContainText(result!.outputs[0].name)
  expect(readFileSync(path.join(job.export_dir, result!.outputs[0].name))).toEqual(source.png)
  source.expectUnchanged()
})

test('guarded PNG: independent A→B→A variants survive empty-context reopen and refuse changed bytes', async ({ app, browser }, testInfo) => {
  test.setTimeout(420_000)
  const source = sourceProject(testInfo.outputPath('source'), true)
  const a = await app({ figures: source.figures })
  const first = await browser.newContext({ viewport: VIEWPORT, locale: 'en-US' })
  let second: Awaited<ReturnType<typeof browser.newContext>> | undefined
  try {
    const page = await first.newPage()
    const data = await seed(page.request, a.baseURL, ['a', 'b'], 'guarded-png-variants')
    const traffic = watchRenders(page)
    await page.goto(`${a.baseURL}/${data.query}`)
    await expect(page.locator('[data-object-id]')).toHaveCount(2)
    await paintedOriginal(page, 'a', source.png)
    await paintedOriginal(page, 'b', source.png)
    expect(traffic.requests).toEqual([])
    await revealCompanion(page, source)

    expect((await enter(page, 'a')).ok()).toBe(true)
    const variantA = await changeTitle(page, 'a', data, traffic.records, source.guard, 18)
    const pixelsA = await paintedPreview(page, 'a', traffic.records, variantA.panel)
    expect((await enter(page, 'b')).ok()).toBe(true)
    const variantB = await changeTitle(page, 'b', data, traffic.records, source.guard, 25)
    const pixelsB = await paintedPreview(page, 'b', traffic.records, variantB.panel)
    expect(pixelsA.w).toBe(pixelsB.w)
    expect(pixelsA.h).toBe(pixelsB.h)
    expect(pixelsA.rgba).not.toBe(pixelsB.rgba)
    expect(await data.savedPanel('a')).toEqual(variantA.panel)
    expect(variantA.panel.artifactValidation).toEqual(source.guard)
    expect(variantB.panel.artifactValidation).toEqual(source.guard)
    expect(placement(variantA.panel)).toEqual(placement(data.objects[0]))
    expect(placement(variantB.panel)).toEqual(placement(data.objects[1]))
    expect(variantA.panel.overrides).not.toEqual(variantB.panel.overrides)
    expect(variantA.key).not.toBe(variantB.key)

    expect((await enter(page, 'a', source.guard)).ok()).toBe(true)
    expect(await exact(page, 'a')).toBe(variantA.key)
    const backA = await fullRender(traffic.records, variantA.panel.overrides, source.guard)
    expect(titleSize(backA)).toBe(variantA.panel.overrides.find(p => p.prop === 'fontsize')!.value)
    expect(await paintedPreview(page, 'a', traffic.records, variantA.panel)).toEqual(pixelsA)
    const saved = [variantA.panel, variantB.panel]
    await expect.poll(data.saved).toEqual(saved)
    // seed() awaited the real session PUT; an unchanged open document correctly deduplicates later writes.
    const last = await page.request.get(`${a.baseURL}/api/layout-session${data.query}`)
    expect((await last.json()).last.doc_id).toBe('guarded-png-variants')
    source.expectUnchanged()
    await first.close()

    second = await browser.newContext({ viewport: VIEWPORT, locale: 'en-US' })
    expect((await second.storageState()).origins).toEqual([])
    const reopened = await second.newPage()
    const reopenedTraffic = watchRenders(reopened)
    await reopened.goto(`${a.baseURL}/${data.query}`)
    await expect(reopened.locator('[data-object-id]')).toHaveCount(2)
    // Read persistence from the fresh context, not the closed context's request handle.
    const reopenedSaved = async () => {
      const r = await reopened.request.get(`${a.baseURL}/api/autosave/guarded-png-variants${data.query}`)
      expect(r.ok()).toBe(true)
      return (await r.json() as Saved).canvases[0].objects
    }
    await expect.poll(reopenedSaved).toEqual(saved)
    expect((await enter(reopened, 'a', source.guard)).ok()).toBe(true)
    await exact(reopened, 'a')
    const reopenedManifest = await fullRender(reopenedTraffic.records, variantA.panel.overrides, source.guard)
    expect(titleSize(reopenedManifest)).toBe(titleSize(backA))
    expect(await paintedPreview(reopened, 'a', reopenedTraffic.records, variantA.panel)).toEqual(pixelsA)

    const replacement = replaceAncillaryText(source.figures)
    expect(sha256(replacement)).not.toBe(source.guard.bytesSha256)
    const refusal = reopened.waitForResponse(r => endpoint(r, '/api/engine/render') && r.status() === 409)
    await reopened.reload()
    const rejected = await refusal
    expect(rejected.request().postDataJSON()).toMatchObject({ id: FILE, ...expectedSource(source.guard) })
    expect(await rejected.json()).toMatchObject({ code: 'artifact_source_unavailable', params: { reason: 'source_changed' } })
    await expect(reopened.locator('[data-element-svg]')).toHaveCount(0)
    await expect(reopened.locator('[data-authority="ready"]')).toHaveCount(0)
    await paintedOriginal(reopened, 'a', replacement)
    await paintedOriginal(reopened, 'b', replacement)
    await expect.poll(reopenedSaved).toEqual(saved)
    const entryRefusal = await enter(reopened, 'a', source.guard)
    expect(entryRefusal.status()).toBe(409)
    expect(await entryRefusal.json()).toMatchObject({ code: 'artifact_source_unavailable', params: { reason: 'source_changed' } })
    const live = await only(reopened.locator('[data-status-live]'))
    const toast = await only(live.locator('..').locator('[data-state]:not([data-onboarding-hint]):not([data-fast-edit-added-note])'))
    await expect(toast).toBeVisible()
    await expect(toast).toContainText(SOURCE_CHANGED)
    await expect(reopened.locator('[data-element-svg]')).toHaveCount(0)
    await expect(reopened.locator('[data-authority="ready"]')).toHaveCount(0)

    const publications: string[] = []
    reopened.on('request', r => {
      if (r.method() === 'POST' && ['/api/export/start', '/api/export'].includes(new URL(r.url()).pathname)) publications.push(r.url())
    })
    const dialog = await openOriginalExport(reopened, 'a')
    const exportRefused = reopened.waitForResponse(r => endpoint(r, '/api/export/validate') && r.status() === 409)
    await dialog.locator('[data-export-start]').click()
    expect(await (await exportRefused).json()).toMatchObject({ code: 'artifact_source_unavailable', params: { reason: 'source_changed' } })
    const visibleReason = await only(dialog.locator('[data-export-start-error]'))
    await expect(visibleReason).toBeVisible()
    await expect(visibleReason).toContainText(SOURCE_CHANGED)
    expect(publications).toEqual([])
    expect(await reopenedSaved()).toEqual(saved)
    source.expectUnchanged(replacement)
  } finally {
    await first.close()
    await second?.close()
  }
})
