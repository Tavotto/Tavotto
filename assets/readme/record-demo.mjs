/**
 * Records the README walkthrough from a real, running Tavotto: inside figure (a), pick the legend
 * and raise its size from 8 to 9 pt on the legend's own toolbar; matplotlib redraws, the result
 * holds, and the loop dips through white back to the start.
 *
 *     node assets/readme/record-demo.mjs [en-US|zh-CN]    # → assets/readme/demo(.zh).webp
 *
 * Needs, besides lib.mjs's requirements: ffmpeg (with libvpx-vp9) and img2webp (`brew install webp`).
 *
 * How it is framed (2026-11-01, after the stageshow review of the old GIF):
 *   * one action, one result, cropped to figure (a) and its breadcrumb at 2× device pixels — the
 *     whole 1440 px window shrunk to the README column made the 8 → 10 pt change unreadable;
 *   * no drag: at the larger size the legend has no better spot in this figure, and the old take
 *     ended with it over the dashed curve; 9 pt rather than 10 for the same reason (10 touches it);
 *   * the top bar is out of frame, so no "unsaved changes" / tutorial chip in the README;
 *   * a pointer arrow is drawn on the page (the recorder draws none) — the only thing on screen
 *     the product did not draw. No captions, no transitions besides the loop seam.
 *
 * Frames come from the CDP screencast, not Playwright's video: the video is always 1×, and the
 * headless shell only screencasts at 2× when launched with --force-device-scale-factor=2.
 */
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { OUT, STRINGS, boot, driver } from './lib.mjs'

const LOCALE = process.argv[2] ?? 'en-US'
const SUFFIX = LOCALE === 'en-US' ? '' : '.' + LOCALE.split('-')[0]
const S = STRINGS[LOCALE]
const work = mkdtempSync(path.join(os.tmpdir(), 'tavotto-demo-'))
const frameDir = path.join(work, 'frames')
const frames = [] // screencast timestamps (s), one per saved frame
const wall = () => Date.now() / 1000
let take = null // { start, end, crop } in seconds / CSS px

const app = await boot(LOCALE, null, { launch: { args: ['--force-device-scale-factor=2'] } })
const { page } = app
const d = driver(page, S)
const glide = async (x, y, steps = 34) => {
  await page.mouse.move(x, y, { steps })
  await d.pause(220)
}
const box = () => d.panel(0).boundingBox()
const at = async (fx, fy) => {
  const b = await box()
  return { x: b.x + b.width * fx, y: b.y + b.height * fy }
}
const legendSelected = async () => (await page.getByText(S.legendCols, { exact: true }).count()) > 0
/** Enter figure (a) and wait until its elements can be hit (the first entry is a cold build). */
async function enterFigure(p) {
  await page.mouse.dblclick(p.x, p.y)
  await page.getByText(S.building).first().waitFor({ state: 'detached', timeout: 180_000 }).catch(() => {})
  await page.getByText(new RegExp(S.cold)).first().waitFor({ timeout: 20_000 }).catch(() => {})
  await page.getByText(new RegExp(S.cold)).first().waitFor({ state: 'detached', timeout: 180_000 }).catch(() => {})
  await page.getByText(S.building).first().waitFor({ state: 'detached', timeout: 180_000 }).catch(() => {})
}

let cdp = null
try {
  await d.buildLayout()

  // Off camera: build the figure once, and find a point on the legend's own frame (between its two
  // rows, left of the swatches) that selects the legend rather than one entry — on camera it is
  // then one click, not a miss on "Catalyst" followed by a second try.
  const b0 = await box()
  await enterFigure({ x: b0.x + b0.width / 2, y: b0.y + b0.height / 2 })
  await d.pause(3000)
  let legendAt = null
  for (const fy of [0.745, 0.74, 0.75, 0.735]) {
    for (const fx of [0.41, 0.405, 0.415, 0.42, 0.4, 0.425, 0.95, 0.945, 0.955]) {
      const p = await at(fx, fy)
      await page.mouse.click(p.x, p.y)
      await d.pause(900)
      if (await legendSelected()) {
        legendAt = [fx, fy]
        break
      }
      await page.keyboard.press('Escape')
      await d.pause(400)
    }
    if (legendAt) break
  }
  if (!legendAt) throw new Error('no point on the legend frame selects the legend itself')
  await page.keyboard.press('Escape')
  await d.pause(600)
  await page.keyboard.press('Escape')
  await d.pause(2500)
  await d.quickSave()
  await d.pause(5000) // the "Saved to project" note leaves before recording starts

  await page.evaluate(() => {
    const c = document.createElement('div')
    c.innerHTML =
      '<svg width="22" height="30" viewBox="0 0 22 30"><path d="M2 2 L2 24 L7.5 18.5 L11.5 27.5 L15 26 L11 17 L18.5 17 Z" fill="#111" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>'
    c.style.cssText =
      'position:fixed;left:0;top:0;pointer-events:none;z-index:2147483647;transform-origin:2px 2px;' +
      'transition:transform 90ms ease-out;filter:drop-shadow(0 1px 1.5px rgba(0,0,0,.35))'
    document.documentElement.appendChild(c)
    addEventListener('mousemove', (e) => { c.style.left = e.clientX - 2 + 'px'; c.style.top = e.clientY - 2 + 'px' }, true)
    addEventListener('mousedown', () => { c.style.transform = 'scale(.86)' }, true)
    addEventListener('mouseup', () => { c.style.transform = '' }, true)
  })
  await page.evaluate(() => document.activeElement?.blur())

  rmSync(frameDir, { recursive: true, force: true })
  execFileSync('mkdir', ['-p', frameDir])
  cdp = await page.context().newCDPSession(page)
  cdp.on('Page.screencastFrame', async (f) => {
    frames.push(f.metadata.timestamp)
    writeFileSync(path.join(frameDir, String(frames.length).padStart(5, '0') + '.jpg'), Buffer.from(f.data, 'base64'))
    await cdp.send('Page.screencastFrameAck', { sessionId: f.sessionId }).catch(() => {})
  })
  await cdp.send('Page.startScreencast', { format: 'jpeg', quality: 95, maxWidth: 2880, maxHeight: 1800 })

  // Into figure (a) and zoom in while it still fits between the breadcrumb and the bottom toolbar.
  // This part is before the clip starts: the loop opens already inside the figure.
  const r0 = await at(1.08, 0.18)
  await page.mouse.move(r0.x, r0.y)
  await d.pause(800)
  await enterFigure(await at(0.5, 0.42))
  await d.pause(1000)
  const zc = await at(0.5, 0.5)
  await page.mouse.move(zc.x, zc.y + 30, { steps: 12 })
  for (let i = 0; i < 4; i++) {
    if ((await box()).height * 1.2 > 450) break
    await page.keyboard.down('Control')
    await page.mouse.wheel(0, -120)
    await page.keyboard.up('Control')
    await d.pause(450)
  }
  await d.pause(800)
  const rest = await at(0.6, 0.48) // empty plot area under the dashed curve, above the legend
  await glide(rest.x, rest.y, 20)
  await d.pause(900)
  const pb = await box()
  const start = wall() - 0.3

  // 1 · the legend, in one click
  await glide(...Object.values(await at(...legendAt)), 26)
  await page.mouse.down()
  await page.mouse.up()
  await d.pause(1200)
  // 2 · 8 → 9 pt on the legend's own toolbar: the input just above its selection box (found by
  //     geometry — labels differ per language, and the properties column has a size field too)
  const lb = await page.locator('[data-element-box]').first().boundingBox()
  const inputs = page.locator('input')
  let size = null
  let sb = null
  for (let i = 0; i < (await inputs.count()); i++) {
    const c = inputs.nth(i)
    const cb = await c.boundingBox()
    if (!cb || cb.y + cb.height > lb.y || cb.y < lb.y - 70 || cb.x > lb.x + lb.width) continue
    if (!sb || cb.x > sb.x) {
      size = c
      sb = cb
    }
  }
  if (!size) throw new Error('no size field on the legend toolbar')
  await glide(sb.x + sb.width * 0.3, sb.y + sb.height * 0.5)
  await size.click()
  await d.pause(250)
  await page.keyboard.press('ControlOrMeta+a')
  await page.keyboard.type('9', { delay: 140 })
  await d.pause(250)
  await size.press('Enter')
  await d.pause(2300) // matplotlib redraws
  // 3 · back to empty canvas and hold the result
  await glide(rest.x, rest.y, 30)
  await d.pause(3200)
  // figure (a) with its breadcrumb above; the bottom cut falls below the x-axis title
  take = { start, end: wall(), crop: [pb.x - 30, pb.y - 40, pb.width + 60, pb.height + 33] }
} catch (e) {
  console.error('recording failed:', e.message, (e.stack || '').split('\n').slice(1, 4).join(' | '))
  process.exitCode = 1
}
if (cdp) await cdp.send('Page.stopScreencast').catch(() => {})
await app.close()
if (!take) process.exit(1)

// Frames → a constant-rate clip: each frame lasts until the next one arrives (the screencast only
// sends frames when something changes).
const files = readdirSync(frameDir).sort()
const inTake = frames.map((t, i) => i).filter((i) => frames[i] >= take.start && frames[i] <= take.end)
const lines = []
for (let i = Math.max(0, inTake[0] - 1); i <= inTake.at(-1); i++) {
  const t0 = Math.max(frames[i], take.start)
  const t1 = i + 1 < frames.length && frames[i + 1] <= take.end ? frames[i + 1] : take.end
  if (t1 > t0) lines.push(`file '${path.join(frameDir, files[i])}'`, `duration ${(t1 - t0).toFixed(4)}`)
}
lines.push(`file '${path.join(frameDir, files[inTake.at(-1)])}'`)
const list = path.join(work, 'frames.txt')
writeFileSync(list, lines.join('\n') + '\n')

const even = (v) => 2 * Math.round(v)
const [cx, cy, cw, ch] = take.crop.map(even) // CSS px → device px (×2), kept even for yuv420p
const dur = take.end - take.start
const loop = path.join(work, 'loop.webm')
execFileSync('ffmpeg', [
  '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', list, '-filter_complex',
  `[0:v]crop=${cw}:${ch}:${cx}:${cy},fps=24,split[a][b];[b]trim=0:0.6,setpts=PTS-STARTPTS[h];` +
    `[a][h]xfade=transition=fadewhite:duration=0.6:offset=${(dur - 0.6).toFixed(3)},format=yuv420p`,
  '-c:v', 'libvpx-vp9', '-b:v', '0', '-crf', '20', loop,
], { stdio: 'inherit' })
const pngDir = path.join(work, 'png')
execFileSync('mkdir', ['-p', pngDir])
execFileSync('ffmpeg', ['-v', 'error', '-i', loop, '-vf', 'fps=20', path.join(pngDir, '%04d.png')], { stdio: 'inherit' })
const out = path.join(OUT, `demo${SUFFIX}.webp`)
const pngs = readdirSync(pngDir).sort().map((f) => path.join(pngDir, f))
execFileSync('img2webp', ['-loop', '0', '-lossy', '-q', '80', '-m', '6', '-d', '50', ...pngs, '-o', out], { stdio: 'inherit' })
rmSync(work, { recursive: true, force: true })
console.log('→', out, `(${dur.toFixed(1)} s, ${cw}×${ch})`)
