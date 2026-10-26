/**
 * Records the README walkthrough — select a panel, drag it, set its width,
 * export — from a real, running Tavotto, then trims the setup off the front.
 *
 *     node assets/readme/record-demo.mjs [en-US|zh-CN]    # → assets/readme/demo(.zh).webm
 *     sh assets/readme/demo-to-gif.sh [en-US|zh-CN]        # → demo(.zh).gif
 *
 * Playwright's recorder does not draw a pointer, so a small dot that follows the
 * mouse is added to the page for the recording — the only thing on screen that
 * the product did not draw. No captions, no transitions.
 */
import { mkdtempSync, renameSync, readdirSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import os from 'node:os'
import path from 'node:path'
import { OUT, STRINGS, boot, driver } from './lib.mjs'

const LOCALE = process.argv[2] ?? 'en-US'
const SUFFIX = LOCALE === 'en-US' ? '' : '.' + LOCALE.split('-')[0]
const S = STRINGS[LOCALE]
const videoDir = mkdtempSync(path.join(os.tmpdir(), 'tavotto-demo-'))

const t0 = Date.now()
const app = await boot(LOCALE, videoDir)
const { page } = app
const d = driver(page, S)

/** Glide the pointer instead of teleporting it. */
const glide = async (x, y, steps = 28) => {
  await page.mouse.move(x, y, { steps })
  await d.pause(250)
}
const centre = async (i) => {
  const b = await d.panel(i).boundingBox()
  return { x: b.x + b.width / 2, y: b.y + b.height / 2 }
}

let startAt = 0
try {
  await d.buildLayout()
  await page.evaluate(() => document.activeElement?.blur())
  await page.evaluate(() => {
    const dot = document.createElement('div')
    dot.style.cssText =
      'position:fixed;left:0;top:0;width:18px;height:18px;margin:-9px 0 0 -9px;border-radius:50%;' +
      'background:rgba(20,20,20,.55);border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.35);' +
      'pointer-events:none;z-index:2147483647;transition:transform .05s linear'
    document.documentElement.appendChild(dot)
    addEventListener('mousemove', (e) => { dot.style.left = e.clientX + 'px'; dot.style.top = e.clientY + 'px' }, true)
  })
  await page.mouse.move(1000, 600)
  await d.pause(1200)
  startAt = (Date.now() - t0) / 1000 - 1

  // 1 · point at the correlation panel (b) and select it
  const b = await centre(1)
  await glide(b.x, b.y)
  await page.mouse.down(); await page.mouse.up()
  await page.getByRole('tab', { name: S.properties }).click().catch(() => {})
  await d.pause(1500)
  // 2 · drag it a little to the right; the guides snap it
  await page.mouse.move(b.x, b.y)
  await page.mouse.down()
  await page.mouse.move(b.x + 40, b.y - 6, { steps: 20 })
  await page.mouse.up()
  await d.pause(1300)
  // 3 · set the width in the properties panel
  await d.setField('W', 56)
  await d.pause(1500)
  // 4 · export
  const exp = page.getByRole('button', { name: S.export, exact: true }).first()
  const eb = await exp.boundingBox()
  await glide(eb.x + eb.width / 2, eb.y + eb.height / 2)
  await exp.click()
  await page.getByRole('dialog').waitFor({ timeout: 15_000 })
  await d.pause(2200)
  const go = page.getByRole('dialog').getByRole('button', { name: S.exportStart, exact: true })
  const gb = await go.boundingBox()
  await glide(gb.x + gb.width / 2, gb.y + gb.height / 2)
  await go.click()
  await d.pause(2600)
} catch (e) {
  console.error('recording failed:', e.message)
  process.exitCode = 1
}
await app.close()

const webm = readdirSync(videoDir).find((f) => f.endsWith('.webm'))
const raw = path.join(videoDir, webm)
const out = path.join(OUT, `demo${SUFFIX}.webm`)
execFileSync('ffmpeg', ['-y', '-ss', startAt.toFixed(2), '-i', raw, '-c:v', 'libvpx-vp9', '-b:v', '0', '-crf', '30', '-an', out], { stdio: 'inherit' })
console.log('→', out, `(trimmed ${startAt.toFixed(1)} s of setup)`)
