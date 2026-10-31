/**
 * Regenerates the README screenshots from a real, running Tavotto.
 *
 * Nothing here is staged or retouched: it boots the app against a throwaway
 * user directory (see lib.mjs), arranges `examples/figures/` on the canvas
 * through the same controls a user would use, and screenshots what comes out.
 * If a shot in the README looks impossible, this file is the thing to run.
 *
 * Usage, from anywhere in the repository:
 *
 *     node assets/readme/capture.mjs                        # every scene, en-US → assets/readme/*.png
 *     node assets/readme/capture.mjs zh-CN                  # → assets/readme/*.zh.png
 *     node assets/readme/capture.mjs en-US layout,timeline  # only the named scenes
 *     TAVOTTO_PYTHON=/path/to/python node assets/readme/capture.mjs
 *
 * Scenes: run-script, connect-ai, layout, timeline, preflight, canvas-edit.
 * Shots are 2880 × 1800 (1440 × 900 at 2×); dialog shots are cropped to the dialog.
 * The walkthrough is a separate script: record-demo.mjs.
 */
import os from 'node:os'
import path from 'node:path'
import { OUT, STRINGS, boot, driver } from './lib.mjs'

const LOCALE = process.argv[2] ?? 'en-US'
const ONLY = (process.argv[3] ?? 'all').split(',')
const wants = (scene) => ONLY.includes('all') || ONLY.includes(scene)
/** Suffix on the file names, so the two sets sit side by side. */
const SUFFIX = LOCALE === 'en-US' ? '' : '.' + LOCALE.split('-')[0]

const S = STRINGS[LOCALE]
if (!S) throw new Error(`no string table for ${LOCALE}; add one`)

/**
 * run-script · the ▶ next to a linked script in the asset library, on a fresh
 * project, a few seconds into the run: the Prepare · Run · Edit card and the
 * script's row saying it is running. (Its own app: once the run lands, the
 * figure opens for editing and the canvas is no longer the one the other
 * scenes need.)
 */
async function runScriptScene() {
  const fresh = await boot(LOCALE)
  const p = fresh.page
  const d = driver(p, S)
  try {
    await d.pause(3500)
    await d.renameLayout()
    await p.locator('li:has-text("fig1_kinetic") button').first().click()
    // The run ends by opening the produced figure for editing; step back out to the canvas,
    // where it now sits, with the asset library showing the script as "Captured 1 figure".
    await p.getByRole('button', { name: S.leaveEditing }).first().waitFor({ timeout: 120_000 })
    await d.pause(1500)
    await p.keyboard.press('Escape')
    await d.pause(2500)
    await d.ensureAssets()
    // Wider asset column and properties column, so script names and "Original 73.5 × 57.8" are whole.
    const drag = async (sel, dx) => {
      const hb = await p.locator(sel).boundingBox()
      await p.mouse.move(hb.x + hb.width / 2, hb.y + 300)
      await p.mouse.down()
      await p.mouse.move(hb.x + hb.width / 2 + dx, hb.y + 300, { steps: 10 })
      await p.mouse.up()
      await d.pause(500)
    }
    await drag('[data-resize-handle="left-drawer"]', 140)
    await drag('[data-resize-handle="inspector"]', -100)
    await d.quickSave()
    await p.mouse.move(1000, 1000)
    await p.mouse.move(1900, 1100) // off the canvas, so nothing is hovered
    await p.evaluate(() => document.activeElement?.blur())
    await d.pause(30_000) // let the toasts expire
    const file = `run-script${SUFFIX}.png`
    await p.screenshot({ path: path.join(OUT, file) })
    console.log('→', path.join('assets/readme', file))
  } finally {
    await fresh.close()
  }
}
if (wants('run-script')) await runScriptScene()

const app = await boot(LOCALE)
const { page } = app
const { pause, setField, selectPanel, quickSave, buildLayout } = driver(page, S)

async function shot(name, clip) {
  const file = `${name}${SUFFIX}.png`
  await page.screenshot({ path: path.join(OUT, file), clip })
  console.log('→', path.join('assets/readme', file))
}

/** The dialog's box, padded, for a cropped shot of a dialog. */
async function dialogClip(pad = 14) {
  const bb = await page.getByRole('dialog').boundingBox()
  return { x: bb.x - pad, y: bb.y - pad, width: bb.width + 2 * pad, height: bb.height + 2 * pad }
}

async function nameMoment(text) {
  await page.getByRole('button', { name: S.nameNow }).click()
  await pause(400)
  await page.keyboard.type(text)
  await page.keyboard.press('Enter')
  await pause(1200)
}

try {
  await buildLayout()

  // ── connect-ai · the assistant tab and the "Connect an AI" dialog ──
  if (wants('connect-ai')) {
    await page.getByRole('tab', { name: S.assistant }).click()
    await pause(600)
    await page.getByRole('button', { name: S.connectAi }).click()
    await pause(1800)
    await shot('connect-ai', await dialogClip(0))
    await page.keyboard.press('Escape')
    await pause(600)
  }

  // ── layout · the page with panel (a) selected and its size in mm ──
  if (wants('layout')) {
    await selectPanel(0)
    await pause(800)
    await shot('layout')
    await page.keyboard.press('Escape')
    await pause(500)
  }

  // ── timeline · named moments on the right-hand tab ──
  if (wants('timeline')) {
    await page.getByRole('tab', { name: S.timeline }).click()
    await pause(800)
    await nameMoment(S.moments[0])
    // The bottom row narrowed to 52 mm each, still centred on the page.
    await selectPanel(1)
    await setField('W', 52)
    await setField('X', 21)
    await selectPanel(2)
    await setField('W', 52)
    await setField('X', 77)
    await pause(800)
    await quickSave()
    await page.getByRole('tab', { name: S.timeline }).click()
    await pause(500)
    await nameMoment(S.moments[1])
    await page.keyboard.press('Escape')
    await page.getByRole('tab', { name: S.timeline }).click()
    await pause(4500) // the "saved as named point" toast
    await shot('timeline')
  }

  // ── preflight · the checks block of the export dialog, in a real state ──
  if (wants('preflight')) {
    // Panel (a) drawn at 58 mm (79 % of its script's size): its tick text lands
    // below what the default spec accepts — a finding to review, not a wall of
    // red, and not a staged all-clear either.
    await selectPanel(0)
    await setField('W', 58)
    await setField('X', 46)
    await pause(800)
    await quickSave()
    await page.keyboard.press('Escape')
    await page.getByRole('button', { name: S.export, exact: true }).first().click()
    await pause(4500)
    console.log('export dialog:\n  ' + (await page.getByRole('dialog').innerText()).split('\n').join('\n  '))
    await shot('preflight', await dialogClip(0))
    await page.keyboard.press('Escape')
    await pause(600)
  }

  // ── canvas-edit · inside a figure: the element tree, one line selected ──
  if (wants('canvas-edit')) {
    await selectPanel(0)
    await page.getByRole('button', { name: S.editElements }).first().click()
    await page.getByText(S.building).first().waitFor({ state: 'detached', timeout: 300_000 }).catch(() => {})
    await pause(2500)
    const tell = page.getByLabel(S.searchElements).first()
    if (!(await tell.isVisible().catch(() => false))) {
      await page.getByRole('button', { name: S.figureElements, exact: true }).click()
      await tell.waitFor({ state: 'visible', timeout: 15_000 })
    }
    // Fit the page, then zoom in two notches around its centre (ctrl + wheel, as a pinch would):
    // the whole page stays in view, centred, with (a) large and nothing under the floating toolbar.
    await page.getByText(S.fitShort, { exact: true }).last().click()
    await pause(900)
    const st = await page.locator('[data-canvas-stage]').boundingBox()
    await page.mouse.move(st.x + st.width / 2, st.y + st.height / 2 - 40)
    await page.keyboard.down('Control')
    for (let i = 0; i < 2; i++) { await page.mouse.wheel(0, -120); await pause(150) }
    await page.keyboard.up('Control')
    await pause(1200)
    await page.locator('li[role="treeitem"]', { hasText: /Title|标题/ }).first().click()
    await quickSave()
    await pause(4500) // the "created a canvas for editing" toast
    await shot('canvas-edit')
    await page.keyboard.press('Escape')
    await pause(1200)
  }
} catch (e) {
  console.error('capture failed:', e.message)
  const fail = path.join(os.tmpdir(), 'tavotto-capture-failure.png')
  await page.screenshot({ path: fail })
  console.error('last frame: ' + fail)
  console.error(app.logs.join('').slice(-3000))
  process.exitCode = 1
}
await app.close()
