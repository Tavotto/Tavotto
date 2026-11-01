/**
 * Records the README walkthrough — double-click into figure (a), enlarge the
 * legend text, drag the legend — from a real, running Tavotto, then trims the setup off the front.
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
const cold = (S) => new RegExp(S.cold)
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
  const box = () => d.panel(0).boundingBox()
  const at = async (fx, fy) => {
    const b = await box()
    return { x: b.x + b.width * fx, y: b.y + b.height * fy }
  }
  // Warm-up, off camera: the first time a figure is opened for editing it is built from cold
  // (several seconds); go in and out once so the take shows the ordinary, quick path.
  const b0 = await box()
  await page.mouse.dblclick(b0.x + b0.width / 2, b0.y + b0.height / 2)
  await page.getByText(S.building).first().waitFor({ state: 'detached', timeout: 120_000 }).catch(() => {})
  await page.getByText(cold(S)).first().waitFor({ timeout: 20_000 }).catch(() => {})
  await page.getByText(cold(S)).first().waitFor({ state: 'detached', timeout: 120_000 }).catch(() => {})
  await d.pause(3000)
  await page.keyboard.press('Escape')
  await d.pause(2500)
  await d.quickSave()
  await d.pause(5000) // the "Saved to project" note leaves before recording starts
  await page.evaluate(() => {
    const dot = document.createElement('div')
    dot.style.cssText =
      'position:fixed;left:0;top:0;width:18px;height:18px;margin:-9px 0 0 -9px;border-radius:50%;' +
      'background:rgba(20,20,20,.55);border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.35);' +
      'pointer-events:none;z-index:2147483647'
    document.documentElement.appendChild(dot)
    addEventListener('mousemove', (e) => { dot.style.left = e.clientX + 'px'; dot.style.top = e.clientY + 'px' }, true)
  })
  await page.evaluate(() => document.activeElement?.blur())
  await page.mouse.move(1000, 700)
  await d.pause(700)
  startAt = (Date.now() - t0) / 1000 - 1

  // 1 · double-click panel (a): inside the figure
  const a = await at(0.5, 0.5)
  await glide(a.x, a.y)
  await page.mouse.dblclick(a.x, a.y)
  await page.getByText(S.building).first().waitFor({ state: 'detached', timeout: 60_000 }).catch(() => {})
  await page.getByText(cold(S)).first().waitFor({ state: 'detached', timeout: 60_000 }).catch(() => {})
  await d.pause(900)
  // 2 · zoom in on the figure, around its centre
  const zc = await at(0.5, 0.5)
  await page.mouse.move(zc.x, zc.y + 30, { steps: 10 })
  await page.keyboard.down('Control')
  for (let i = 0; i < 1; i++) { await page.mouse.wheel(0, -120); await d.pause(120) }
  await page.keyboard.up('Control')
  await d.pause(600)
  // 3 · pick the legend and make its text a notch larger
  // The legend itself (not one of its entries): try the spots inside its frame until the
  // breadcrumb up top says "Legend" with no "entry".
  const isLegend = async () => (await page.getByText(S.legendCols, { exact: true }).count()) > 0
  let lg = null
  for (const [fx, fy] of [[0.67, 0.74], [0.67, 0.7], [0.62, 0.74], [0.72, 0.78], [0.67, 0.66]]) {
    lg = await at(fx, fy)
    await glide(lg.x, lg.y, 14)
    await page.mouse.down(); await page.mouse.up()
    await d.pause(900)
    if (await isLegend()) break
  }
  await d.pause(300)
  const size = page.getByLabel(S.sizeField, { exact: true }).first()
  await size.click()
  await size.fill('10')
  await size.press('Enter')
  await d.pause(1200)
  // 4 · drag the legend up to the empty middle of the right-hand side
  const from = lg
  await glide(from.x, from.y)
  await page.mouse.down()
  await page.mouse.move(from.x, from.y - 55, { steps: 30 })
  await page.mouse.up()
  await d.pause(2000) // rests on the redrawn figure
} catch (e) {
  console.error('recording failed:', e.message, (e.stack || '').split('\n').slice(1, 4).join(' | '))
  await page.screenshot({ path: '/private/tmp/claude-501/-Volumes-Projects-Tavotto/544eade4-c05b-41be-b245-b01aaaa8c1c7/scratchpad/readme-shots/fail.png' }).catch(() => {})
  process.exitCode = 1
}
await app.close()

const webm = readdirSync(videoDir).find((f) => f.endsWith('.webm'))
const raw = path.join(videoDir, webm)
const out = path.join(OUT, `demo${SUFFIX}.webm`)
execFileSync('ffmpeg', ['-y', '-ss', startAt.toFixed(2), '-i', raw, '-c:v', 'libvpx-vp9', '-b:v', '0', '-crf', '30', '-an', out], { stdio: 'inherit' })
console.log('→', out, `(trimmed ${startAt.toFixed(1)} s of setup)`)
