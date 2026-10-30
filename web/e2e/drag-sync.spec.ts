import { existsSync, readFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import path from 'node:path'
import { expect, openElementsTab, test, waitForDocumentReady } from './fixtures'
import type { Page, Route } from '@playwright/test'

async function openFigure(page: Page, baseURL: string) {
  await page.addInitScript(() => {
    localStorage.setItem('tavotto.ui', JSON.stringify({ prefsVersion: 2, snapEnabled: false }))
  })
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto(baseURL)
  await page.locator('[data-card="Fig1_kinetics.pdf"]').dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-element-svg] > svg')).toBeVisible({ timeout: 90_000 })
  await expect(page.locator('[data-display]')).toHaveAttribute('data-display', 'exact', { timeout: 90_000 })
}

async function box(page: Page, gid: string) {
  return page.locator(`[data-element-svg] [id="${gid}"]`).evaluate((node) => {
    const r = node.getBoundingClientRect()
    return { x: r.x, y: r.y, width: r.width, height: r.height }
  })
}

async function figureGeometry(page: Page) {
  return page.locator('[data-element-svg]').evaluate((root) => {
    const svg = root.querySelector('svg')!.getBoundingClientRect()
    const legend = root.querySelector('[id="axes_0.legend"]')!.getBoundingClientRect()
    const axes = root.querySelector('[id="axes_0"]')!.getBoundingClientRect()
    const xlabel = root.querySelector('[id="axes_0.xlabel"]')!.getBoundingClientRect()
    const ylabel = root.querySelector('[id="axes_0.ylabel"]')!.getBoundingClientRect()
    return {
      legendX: (legend.x - svg.x) / svg.width,
      legendY: (legend.y - svg.y) / svg.height,
      legendWidth: legend.width / svg.width,
      axesX: (axes.x - svg.x) / svg.width,
      xlabelHeight: xlabel.height / svg.height,
      ylabelWidth: ylabel.width / svg.width,
      aspect: svg.width / svg.height,
    }
  })
}

async function drag(page: Page, x: number, y: number, dx: number, dy: number) {
  await page.mouse.move(x, y)
  await page.mouse.down()
  await page.mouse.move(x + dx, y + dy, { steps: 8 })
  await page.mouse.up()
}

async function nextExact(page: Page, previous: string | null) {
  const display = page.locator('[data-display]')
  await expect.poll(async () =>
    (await display.getAttribute('data-display')) === 'exact' &&
    (await display.getAttribute('data-display-key')) !== previous, { timeout: 90_000 }).toBe(true)
}

test('a second legend drag during rendering moves the legend, not its panel', async ({ app, page }) => {
  const a = await app()
  await openFigure(page, a.baseURL)
  const held: Route[] = []
  await page.route('**/api/engine/render**', (route) => { held.push(route) })
  const axes0 = await box(page, 'axes_0')
  const legend0 = await box(page, 'axes_0.legend')
  const transform0 = await page.locator('[data-object-id]').getAttribute('style')
  await drag(page, legend0.x + legend0.width * 0.3, legend0.y + legend0.height * 0.5, -35, -20)
  await expect.poll(() => held.length).toBe(1)
  const legend1 = await box(page, 'axes_0.legend')
  await drag(page, legend1.x + legend1.width * 0.3, legend1.y + legend1.height * 0.5, -30, 0)
  const axes2 = await box(page, 'axes_0')
  const legend2 = await box(page, 'axes_0.legend')
  expect(Math.abs(axes2.x - axes0.x)).toBeLessThan(2)
  expect(await page.locator('[data-object-id]').getAttribute('style')).toBe(transform0)
  expect(legend1.x - legend2.x).toBeGreaterThan(20)
  // A third gesture is still its own edit even though neither response has arrived.
  await drag(page, legend2.x + legend2.width * 0.3, legend2.y + legend2.height * 0.5, -20, 0)
  await held.shift()!.continue()
  await expect.poll(() => held.length, { timeout: 90_000 }).toBeGreaterThan(0)
  await held.shift()!.continue()
  await expect.poll(() => held.length, { timeout: 90_000 }).toBeGreaterThan(0)
  await held.shift()!.continue()
  await expect(page.locator('[data-display]')).toHaveAttribute('data-display', 'exact', { timeout: 90_000 })
  const axesFinal = await box(page, 'axes_0')
  const legendFinal = await box(page, 'axes_0.legend')
  expect(Math.abs(axesFinal.x - axes0.x)).toBeLessThan(2)
  expect((legend0.x - axes0.x) - (legendFinal.x - axesFinal.x)).toBeGreaterThan(75)
  await page.unroute('**/api/engine/render**')
  await page.keyboard.press('ControlOrMeta+z')
  await expect.poll(async () => (await box(page, 'axes_0.legend')).x, { timeout: 90_000 })
    .toBeGreaterThan(legendFinal.x + 15)
  await page.keyboard.press('ControlOrMeta+Shift+z')
  await expect.poll(async () => Math.abs((await box(page, 'axes_0.legend')).x - legendFinal.x), { timeout: 90_000 })
    .toBeLessThan(3)
})

test('cancelled and invalidated queued drags do not move the figure', async ({ app, page }) => {
  const a = await app()
  await openFigure(page, a.baseURL)
  const held: Route[] = []
  await page.route('**/api/engine/render**', (route) => { held.push(route) })
  const legend0 = await box(page, 'axes_0.legend')
  const axes0 = await box(page, 'axes_0')
  await drag(page, legend0.x + legend0.width * 0.3, legend0.y + legend0.height * 0.5, -35, 0)
  await expect.poll(() => held.length).toBe(1)
  const legend1 = await box(page, 'axes_0.legend')
  const x = legend1.x + legend1.width * 0.3, y = legend1.y + legend1.height * 0.5
  await page.mouse.move(x, y)
  await page.mouse.down()
  await page.mouse.move(x - 30, y, { steps: 8 })
  await page.evaluate(() => window.dispatchEvent(new PointerEvent('pointercancel', { bubbles: true })))
  await page.mouse.up()
  await expect.poll(async () => Math.abs((await box(page, 'axes_0.legend')).x - legend1.x)).toBeLessThan(2)
  await drag(page, x, y, -30, 0)
  await page.keyboard.press('ControlOrMeta+z')
  await held.shift()!.continue()
  await expect.poll(async () => Math.abs((await box(page, 'axes_0.legend')).x - legend0.x), { timeout: 15_000 })
    .toBeLessThan(3)
  expect(Math.abs((await box(page, 'axes_0')).x - axes0.x)).toBeLessThan(2)
  expect(held).toHaveLength(0)
})

test('a held drag survives the previous SVG arriving', async ({ app, page }) => {
  const a = await app()
  await openFigure(page, a.baseURL)
  const held: Route[] = []
  await page.route('**/api/engine/render**', (route) => { held.push(route) })
  const start = await box(page, 'axes_0.legend')
  await drag(page, start.x + start.width * 0.3, start.y + start.height * 0.5, -35, 0)
  await expect.poll(() => held.length).toBe(1)
  const legend1 = await box(page, 'axes_0.legend')
  const x = legend1.x + legend1.width * 0.3, y = legend1.y + legend1.height * 0.5
  await page.mouse.move(x, y)
  await page.mouse.down()
  await page.mouse.move(x - 30, y, { steps: 8 })
  const preview = await box(page, 'axes_0.legend')
  await held.shift()!.continue()
  await expect(page.locator('[data-authority]')).toHaveAttribute('data-authority', 'ready', { timeout: 90_000 })
  await expect.poll(async () => Math.abs((await box(page, 'axes_0.legend')).x - preview.x), { timeout: 5_000 })
    .toBeLessThan(3)
  await page.mouse.up()
  await expect.poll(() => held.length, { timeout: 90_000 }).toBe(1)
  await held.shift()!.continue()
  await expect(page.locator('[data-display]')).toHaveAttribute('data-display', 'exact', { timeout: 90_000 })
  expect(start.x - (await box(page, 'axes_0.legend')).x).toBeGreaterThan(55)
})

test('failed authority cancels the queued drag and keeps the committed preview', async ({ app, page }) => {
  const a = await app()
  await openFigure(page, a.baseURL)
  const held: Route[] = []
  await page.route('**/api/engine/render**', (route) => { held.push(route) })
  const start = await box(page, 'axes_0.legend')
  const axes0 = await box(page, 'axes_0')
  await drag(page, start.x + start.width * 0.3, start.y + start.height * 0.5, -35, 0)
  await expect.poll(() => held.length).toBe(1)
  const first = await box(page, 'axes_0.legend')
  await drag(page, first.x + first.width * 0.3, first.y + first.height * 0.5, -30, 0)
  await held.shift()!.fulfill({ status: 500, json: { error: 'synthetic legend failure' } })
  await expect(page.locator('[data-panel-badge="error"]')).toBeVisible()
  await expect.poll(async () => Math.abs((await box(page, 'axes_0.legend')).x - first.x)).toBeLessThan(2)
  expect(Math.abs((await box(page, 'axes_0')).x - axes0.x)).toBeLessThan(2)
  await expect(page.locator('[data-status-tone="error"]')).toBeVisible()
})

test('figure resize shows current rendering and failure states', async ({ app, page }) => {
  const a = await app()
  await openFigure(page, a.baseURL)
  const held: Route[] = []
  await page.route('**/api/engine/render**', (route) => { held.push(route) })
  const edge = (await page.locator('[data-figure-edge="r"]').boundingBox())!
  await drag(page, edge.x + edge.width / 2, edge.y + edge.height / 2, 50, 0)
  await expect.poll(() => held.length).toBe(1)
  await expect(page.locator('[data-figure-pending]')).toBeVisible()
  await expect(page.locator('[data-panel-badge="busy"]')).toBeVisible({ timeout: 5_000 })
  await expect.poll(() => page.locator('[data-panel-badge="busy"]').evaluate((node) => {
    for (let el: Element | null = node; el; el = el.parentElement) {
      if (Number(getComputedStyle(el).opacity) === 0) return false
    }
    return true
  })).toBe(true)
  await held.shift()!.fulfill({ status: 500, json: { error: 'synthetic resize failure' } })
  await expect(page.locator('[data-panel-badge="error"]')).toBeVisible({ timeout: 5_000 })
  await expect(page.locator('[data-panel-render-live]')).toHaveText('渲染失败')
  await expect(page.locator('[data-figure-pending]')).toHaveCount(0, { timeout: 5_000 })
})

test('a grouped font size shows pending until all labels are rendered', async ({ app, page }) => {
  const a = await app()
  await openFigure(page, a.baseURL)
  const rail = page.locator('[data-rail="style"]')
  if (await rail.getAttribute('aria-expanded') !== 'true') await rail.click()
  const input = page.locator('[data-style-cell="axis_label.size"] input')
  await expect(input).toBeEnabled()
  const x0 = await box(page, 'axes_0.xlabel')
  const y0 = await box(page, 'axes_0.ylabel')
  const held: Route[] = []
  await page.route('**/api/engine/render**', (route) => { held.push(route) })
  await input.fill('18')
  await input.press('Enter')
  await expect.poll(() => held.length).toBe(1)
  await expect(input).toHaveValue('18')
  await expect(page.locator('[data-style-sync-status="pending"]')).toBeVisible()
  await expect(input).toBeEnabled()
  await input.fill('20')
  await input.press('Enter')
  await input.fill('22')
  await input.press('Enter')
  await expect(input).toHaveValue('22')
  expect(held).toHaveLength(1)
  await held.shift()!.continue()
  await expect.poll(() => held.length, { timeout: 90_000 }).toBe(1)
  const body = held[0].request().postDataJSON() as { patches: { gid: string; prop: string; value: unknown }[] }
  const sizes = body.patches.filter((p) => p.prop === 'fontsize' && ['axes_0.xlabel', 'axes_0.ylabel'].includes(p.gid))
  expect(sizes).toHaveLength(2)
  expect(sizes.map((p) => p.value)).toEqual([22, 22])
  await held.shift()!.continue()
  await expect(page.locator('[data-style-sync-status]')).toHaveCount(0, { timeout: 90_000 })
  await expect(page.locator('[data-display]')).toHaveAttribute('data-display', 'exact', { timeout: 90_000 })
  expect((await box(page, 'axes_0.xlabel')).height).toBeGreaterThan(x0.height)
  expect((await box(page, 'axes_0.ylabel')).width).toBeGreaterThan(y0.width)
})

test('failed font rendering discards the queued size and reports the failure', async ({ app, page }) => {
  const a = await app()
  await openFigure(page, a.baseURL)
  const rail = page.locator('[data-rail="style"]')
  if (await rail.getAttribute('aria-expanded') !== 'true') await rail.click()
  const input = page.locator('[data-style-cell="axis_label.size"] input')
  await expect(input).toBeEnabled()
  const held: Route[] = []
  await page.route('**/api/engine/render**', (route) => { held.push(route) })
  await input.fill('18')
  await input.press('Enter')
  await expect.poll(() => held.length).toBe(1)
  await input.fill('22')
  await input.press('Enter')
  await expect(input).toHaveValue('22')
  await held.shift()!.fulfill({ status: 500, json: { error: 'synthetic font failure' } })
  await expect(page.locator('[data-style-sync-status="error"]')).toBeVisible()
  await expect(input).toHaveValue('18')
  await expect(page.locator('[data-status-tone="error"]')).toBeVisible()
  expect(held).toHaveLength(0)
})

test('legend, font and figure size survive saving and reopening', async ({ app, page }) => {
  const a = await app()
  await openFigure(page, a.baseURL)
  const legend = await box(page, 'axes_0.legend')
  const legendKey = await page.locator('[data-display]').getAttribute('data-display-key')
  await drag(page, legend.x + legend.width * .3, legend.y + legend.height * .5, -30, -15)
  await nextExact(page, legendKey)
  const rail = page.locator('[data-rail="style"]')
  if (await rail.getAttribute('aria-expanded') !== 'true') await rail.click()
  const input = page.locator('[data-style-cell="axis_label.size"] input')
  const fontKey = await page.locator('[data-display]').getAttribute('data-display-key')
  await input.fill('18')
  await input.press('Enter')
  await nextExact(page, fontKey)
  const paper = page.locator('[data-fast-edit-paper]')
  const width = (await paper.boundingBox())!.width
  const edge = (await page.locator('[data-figure-edge="r"]').boundingBox())!
  const frameKey = await page.locator('[data-display]').getAttribute('data-display-key')
  await drag(page, edge.x + edge.width / 2, edge.y + edge.height / 2, 35, 0)
  await expect.poll(async () => (await paper.boundingBox())?.width ?? 0, { timeout: 90_000 }).toBeGreaterThan(width + 20)
  await nextExact(page, frameKey)
  await expect(page.locator('[data-authority]')).toHaveAttribute('data-authority', 'ready', { timeout: 90_000 })
  const before = await figureGeometry(page)
  await input.blur()
  await page.keyboard.press('ControlOrMeta+s')
  const dialog = page.getByRole('dialog', { name: '存进项目' })
  await expect(dialog).toBeVisible()
  await dialog.locator('#layout-save-name').fill('拖动同步回归')
  await dialog.getByRole('button', { name: '存进项目' }).click()
  await expect(dialog).toBeHidden()
  const file = path.join(a.figures, 'tavottofile', '拖动同步回归.json')
  await expect.poll(() => existsSync(file)).toBe(true)
  const saved = JSON.parse(readFileSync(file, 'utf-8')) as { canvases: { objects: { overrides?: { gid: string; prop: string; value: unknown }[] }[] }[] }
  const patches = saved.canvases.flatMap((canvas) => canvas.objects.flatMap((object) => object.overrides ?? []))
  expect(patches).toEqual(expect.arrayContaining([
    expect.objectContaining({ gid: 'axes_0.legend', prop: 'loc_frac' }),
    expect.objectContaining({ gid: 'axes_0.xlabel', prop: 'fontsize', value: 18 }),
    expect.objectContaining({ gid: 'axes_0.ylabel', prop: 'fontsize', value: 18 }),
    expect.objectContaining({ gid: 'figure', prop: 'size_mm' }),
  ]))
  await page.reload()
  await waitForDocumentReady(page)
  await page.getByRole('button', { name: /^(文档|排版)：/ }).click()
  await page.getByRole('menuitem', { name: /^打开(文档|排版)/ }).click()
  const open = page.getByRole('dialog')
  await open.getByRole('button', { name: '拖动同步回归' }).click()
  await expect(open).toBeHidden()
  if (!(await page.locator('[data-element-svg] > svg').count())) {
    await page.locator('[data-object-id]').dblclick()
  }
  await expect(page.locator('[data-element-svg] > svg')).toBeVisible({ timeout: 90_000 })
  await expect(page.locator('[data-display]')).toHaveAttribute('data-display', 'exact', { timeout: 90_000 })
  await expect(page.locator('[data-authority]')).toHaveAttribute('data-authority', 'ready', { timeout: 90_000 })
  const after = await figureGeometry(page)
  expect(Math.abs(after.legendX - before.legendX)).toBeLessThan(.005)
  expect(Math.abs(after.legendY - before.legendY)).toBeLessThan(.005)
  expect(Math.abs(after.legendWidth - before.legendWidth)).toBeLessThan(.005)
  expect(Math.abs(after.xlabelHeight - before.xlabelHeight)).toBeLessThan(.005)
  expect(Math.abs(after.ylabelWidth - before.ylabelWidth)).toBeLessThan(.005)
  expect(Math.abs(after.aspect - before.aspect)).toBeLessThan(.005)
})

test('legend, font and figure size survive writing the original and reopening it', async ({ app, page }) => {
  const a = await app()
  const original = path.join(a.figures, 'Fig1_kinetics.pdf')
  const originalBytes = readFileSync(original)
  const script = path.join(a.figures, 'fig1_kinetics.py')
  const scriptBytes = readFileSync(script)
  await openFigure(page, a.baseURL)
  const initial = await figureGeometry(page)

  const legend = await box(page, 'axes_0.legend')
  let key = await page.locator('[data-display]').getAttribute('data-display-key')
  await drag(page, legend.x + legend.width * .3, legend.y + legend.height * .5, -30, -15)
  await nextExact(page, key)
  const rail = page.locator('[data-rail="style"]')
  if (await rail.getAttribute('aria-expanded') !== 'true') await rail.click()
  const input = page.locator('[data-style-cell="axis_label.size"] input')
  key = await page.locator('[data-display]').getAttribute('data-display-key')
  await input.fill('18')
  await input.press('Enter')
  await nextExact(page, key)
  const edge = (await page.locator('[data-figure-edge="r"]').boundingBox())!
  key = await page.locator('[data-display]').getAttribute('data-display-key')
  await drag(page, edge.x + edge.width / 2, edge.y + edge.height / 2, 35, 0)
  await nextExact(page, key)
  await expect(page.locator('[data-authority]')).toHaveAttribute('data-authority', 'ready', { timeout: 90_000 })
  const before = await figureGeometry(page)
  expect(initial.legendX - before.legendX).toBeGreaterThan(.01)
  expect(before.xlabelHeight).toBeGreaterThan(initial.xlabelHeight)
  expect(before.ylabelWidth).toBeGreaterThan(initial.ylabelWidth)
  expect(before.aspect).toBeGreaterThan(initial.aspect)

  await input.blur()
  await page.keyboard.press('Escape')
  await page.keyboard.press('Escape')
  await page.locator('[data-more-menu]').click()
  await page.locator('[data-write-back="open"][data-write-back-entry="menu"]').click()
  const result = page.waitForResponse((r) => r.url().includes('/api/engine/update_source') && r.request().method() === 'POST')
  await page.locator('[data-write-back="confirm"]').click()
  const response = await result
  const receipt = await response.json()
  expect(response.status(), JSON.stringify(receipt)).toBe(200)
  expect(receipt.verification).toEqual(expect.objectContaining({ replay: 'ok', pixels: 'ok' }))
  expect(receipt.verification.elements).toBeGreaterThan(0)
  expect(receipt.updated).toContain('Fig1_kinetics.pdf')
  expect(receipt.warnings).toEqual([])
  expect(receipt.post_check).toBeUndefined()
  expect(readFileSync(original).equals(originalBytes)).toBe(false)
  expect(readFileSync(path.join(receipt.backup_dir, 'Fig1_kinetics.pdf')).equals(originalBytes)).toBe(true)
  expect(receipt.source_sha1['Fig1_kinetics.pdf']).toBe(createHash('sha1').update(readFileSync(original)).digest('hex'))
  expect(readFileSync(script).equals(scriptBytes)).toBe(true)
  expect(response.request().postDataJSON().patches).toEqual(expect.arrayContaining([
    expect.objectContaining({ gid: 'axes_0.legend', prop: 'loc_frac' }),
    expect.objectContaining({ gid: 'axes_0.xlabel', prop: 'fontsize', value: 18 }),
    expect.objectContaining({ gid: 'axes_0.ylabel', prop: 'fontsize', value: 18 }),
    expect.objectContaining({ gid: 'figure', prop: 'size_mm' }),
  ]))
  await expect(page.locator('[data-write-back="verified"]')).toBeVisible({ timeout: 90_000 })

  // 重启页面并从素材库重新打开磁盘文件，不能借用原画布的 overrides。
  await page.reload()
  await page.locator('[data-card="Fig1_kinetics.pdf"]').dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-element-svg] > svg')).toBeVisible({ timeout: 90_000 })
  await expect(page.locator('[data-display]')).toHaveAttribute('data-display', 'exact', { timeout: 90_000 })
  await expect(page.locator('[data-authority]')).toHaveAttribute('data-authority', 'ready', { timeout: 90_000 })
  const after = await figureGeometry(page)
  expect(Math.abs(after.legendX - before.legendX)).toBeLessThan(.005)
  expect(Math.abs(after.legendY - before.legendY)).toBeLessThan(.005)
  expect(Math.abs(after.legendWidth - before.legendWidth)).toBeLessThan(.005)
  expect(Math.abs(after.xlabelHeight - before.xlabelHeight)).toBeLessThan(.005)
  expect(Math.abs(after.ylabelWidth - before.ylabelWidth)).toBeLessThan(.005)
  expect(Math.abs(after.aspect - before.aspect)).toBeLessThan(.005)

  // 几何相同不保证公式与周围文字的顺序相同：重开后也核对图例文本本身。
  const label = page.locator('[data-element-svg] [id="axes_0.legend.texts_0"]')
  const labelRect = (await label.boundingBox())!
  await page.mouse.click(labelRect.x + labelRect.width / 2, labelRect.y + labelRect.height / 2)
  const field = page.locator('[data-prop="text"] [data-rich-text="field"]')
  await expect(field.locator('[data-rt-math] [data-math-state="ok"]')).toHaveCount(1, { timeout: 60_000 })
  const textParts = await field.evaluate((element) => [...element.childNodes].map((node) => {
    if (node.nodeType === Node.TEXT_NODE) return `text:${node.nodeValue}`
    return `math:${(node as HTMLElement).dataset.rtMath}`
  }))
  expect(textParts).toEqual(['text:Catalyst (k = 0.125 ', 'math:\\mathrm{min^{-1}}', 'text:)'])
})

test('selecting a legend formula and leaving it without typing does not edit its text', async ({ app, page }) => {
  const a = await app()
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto(a.baseURL)
  await page.locator('[data-card="Fig1_kinetics.pdf"]').dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-element-svg] > svg')).toBeVisible({ timeout: 90_000 })
  await expect(page.locator('[data-display]')).toHaveAttribute('data-display', 'exact', { timeout: 90_000 })
  const label = page.locator('[data-element-svg] [id="axes_0.legend.texts_0"]')
  const rect = (await label.boundingBox())!
  await page.mouse.click(rect.x + rect.width * .5, rect.y + rect.height * .5)
  const field = page.locator('[data-prop="text"] [data-rich-text="field"]')
  await expect(field).toBeVisible({ timeout: 15_000 })
  await expect(field.locator('[data-rt-math] [data-math-state="ok"]')).toHaveCount(1, { timeout: 60_000 })
  await drag(page, rect.x + rect.width * .5, rect.y + rect.height * .5, -25, -10)
  await openElementsTab(page)
  await page.locator('[data-tree="elements"] [data-el="figure"]').click()
  await page.keyboard.press('ControlOrMeta+s')
  const dialog = page.getByRole('dialog', { name: '存进项目' })
  await dialog.locator('#layout-save-name').fill('math-blur')
  await dialog.getByRole('button', { name: '存进项目' }).click()
  await expect(dialog).toBeHidden()
  const saved = JSON.parse(readFileSync(path.join(a.figures, 'tavottofile', 'math-blur.json'), 'utf-8')) as {
    canvases: { objects: { overrides?: { gid: string; prop: string; value: unknown }[] }[] }[]
  }
  const patches = saved.canvases.flatMap((canvas) => canvas.objects.flatMap((object) => object.overrides ?? []))
  expect(patches.filter((p) => p.gid === 'axes_0.legend.texts_0' && p.prop === 'text')).toEqual([])
})
