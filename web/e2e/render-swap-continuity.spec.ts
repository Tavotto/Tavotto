import { expect, openElementsTab, test } from './fixtures'

/**
 * Keep the displayed bitmap until its replacement actually loads. The real
 * engine supplies the figure and manifest; the response is forced to the
 * supported raster representation so this small fixture reaches the bitmap
 * path. Only the replacement image's load is held at a controlled gate.
 */
test('raster 参数编辑：新图加载期间保留已显示的图', async ({ app, page }) => {
  test.setTimeout(240_000)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.addInitScript(() => {
    const proof = window as typeof window & {
      delayed: boolean
      pngs: Record<string, string>
      serial: number
    }
    proof.delayed = false
    proof.pngs = {}
    proof.serial = 0
    const set = Element.prototype.setAttribute
    Element.prototype.setAttribute = function (name, value) {
      if (proof.delayed && this instanceof HTMLImageElement && name === 'src' && value.startsWith('data:image/png;base64,')) {
        const id = String(++proof.serial)
        proof.pngs[id] = value.split(',')[1]
        value = `/__e2e_delayed_png/${id}`
      }
      return set.call(this, name, value)
    }
  })

  let signalRequest!: () => void
  const requestSeen = new Promise<void>((resolve) => { signalRequest = resolve })
  let release!: () => void
  const gate = new Promise<void>((resolve) => { release = resolve })
  await page.route('**/__e2e_delayed_png/*', async (route) => {
    const id = route.request().url().split('/').at(-1)!
    const png = await page.evaluate((key) =>
      (window as typeof window & { pngs: Record<string, string> }).pngs[key], id)
    signalRequest()
    await gate
    await route.fulfill({ status: 200, contentType: 'image/png', body: Buffer.from(png, 'base64') })
  })
  await page.route('**/api/engine/render**', async (route) => {
    const response = await route.fetch({ timeout: 120_000 })
    if (!response.ok()) return route.fulfill({ response })
    const body = await response.json()
    body.preview = { mode: 'raster', reason: 'fallback', svg_bytes: body.preview?.svg_bytes ?? 0, rasterized_artist_count: 0 }
    body.svg = null
    await route.fulfill({ response, json: body })
  })

  const a = await app()
  try {
    await page.goto(a.baseURL)
    const card = page.locator('[data-card="Fig1_kinetics.pdf"]')
    await expect(card).toHaveCount(1, { timeout: 30_000 })
    await card.dblclick()
    const authority = page.locator('[data-authority="ready"]')
    await expect(authority).toHaveCount(1, { timeout: 120_000 })
    await openElementsTab(page)
    const tree = page.locator('[data-tree="elements"]')
    await expect(tree).toHaveCount(1, { timeout: 30_000 })
    const axes = tree.locator('[data-el="axes_0"]')
    if (await axes.getAttribute('aria-expanded') === 'false') {
      await axes.focus()
      await axes.press('ArrowRight')
    }
    const title = tree.locator('[data-el="axes_0.title"]')
    await expect(title).toHaveCount(1)
    await title.click()
    const size = page.locator('[data-inspector-prop="fontsize"]')
    await expect(size).toHaveCount(1)
    const value = Number(await size.inputValue())
    const display = page.locator('[data-display]')
    await expect(display).toHaveCount(1)
    const old = await display.locator('img:not([aria-hidden])').getAttribute('src')
    expect(old).toMatch(/^data:image\/png;base64,/)

    await page.evaluate(() => { (window as typeof window & { delayed: boolean }).delayed = true })
    await size.fill(String(value + 3))
    await size.press('Enter')
    await requestSeen

    // The pending request is deliberately unresolved: the visible element
    // must still be the loaded old image, even with reduced motion enabled.
    const visible = display.locator('img:not([aria-hidden])')
    await expect(visible).toHaveCount(1)
    await expect(visible).toHaveAttribute('src', old!)
    expect(await visible.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true)
    release()
    await expect(visible).not.toHaveAttribute('src', old!, { timeout: 30_000 })
  } finally {
    release()
    await page.unrouteAll({ behavior: 'ignoreErrors' })
  }
})
