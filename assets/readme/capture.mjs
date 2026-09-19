/**
 * Regenerates the README screenshots from a real, running Tavotto.
 *
 * Nothing here is staged or retouched: it boots the app against a throwaway
 * user directory, arranges `examples/figures/` on the canvas through the same
 * controls a user would use, and screenshots what comes out. If a shot in the
 * README looks impossible, this file is the thing to run to check.
 *
 * Usage, from anywhere in the repository:
 *
 *     node assets/readme/capture.mjs                    # → assets/readme/*.png (en-US)
 *     node assets/readme/capture.mjs zh-CN              # → assets/readme/*.zh.png
 *     TAVOTTO_PYTHON=/path/to/python node assets/readme/capture.mjs
 *
 * Requires: `pnpm install` + `npx playwright install chromium` in `web/`, and a
 * Python with the package installed (`pip install -e ".[worker]"`). Run
 * `python scripts/build_frontend.py` first — the packaged `src/tavotto/web/`
 * wins over `web/dist`, so otherwise you photograph the previous interface.
 *
 * The interface language is forced, not inherited, so each README shows the
 * interface its readers will actually get. Every string this script clicks on is
 * therefore looked up in STRINGS below rather than hard-coded — keep it in step
 * with web/src/i18n/locales/.
 */
import { spawn } from 'node:child_process'
import { cpSync, mkdirSync, mkdtempSync, rmSync } from 'node:fs'
import { createRequire } from 'node:module'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'

const REPO = path.resolve(import.meta.dirname, '..', '..')
// Playwright lives in web/node_modules; anchor the resolution there so this
// script runs from any directory rather than only from `web/`.
const { chromium } = createRequire(path.join(REPO, 'web', 'package.json'))('@playwright/test')
const OUT = path.join(REPO, 'assets', 'readme')
const PY = process.env.TAVOTTO_PYTHON ?? path.join(REPO, '.venv', 'bin', 'python')

const LOCALE = process.argv[2] ?? 'en-US'
/** Suffix on the file names, so the two sets sit side by side. */
const SUFFIX = LOCALE === 'en-US' ? '' : '.' + LOCALE.split('-')[0]

const STRINGS = {
  'en-US': {
    assets: 'Assets', figureElements: 'Figure elements', properties: 'Properties',
    canvas: 'Canvas', fitCanvas: 'Fit canvas', pin: 'Pin sidebar',
    panelLabels: 'Add panel labels', editElements: 'Edit figure elements',
    export: 'Export', building: /Building/, preflight: /Preflight/,
    searchPanels: 'Search figures', searchElements: 'Search figure elements',
    /** The numeric fields' accessible names, `<axis> (mm)`. */
    mm: (axis) => `${axis} (mm)`,
  },
  'zh-CN': {
    assets: '素材', figureElements: '图内元素', properties: '属性',
    canvas: '画布', fitCanvas: '适应画布', pin: '钉住侧栏',
    panelLabels: '添加序号标签', editElements: '编辑图内元素',
    export: '导出', building: /正在构建|构建中/, preflight: /预检/,
    searchPanels: '搜索图', searchElements: '搜索图内元素',
    mm: (axis) => `${axis} (mm)`,
  },
}
const S = STRINGS[LOCALE]
if (!S) throw new Error(`no string table for ${LOCALE}; add one`)

/**
 * Viewport of the photographed window. 1.5× keeps the text crisp at the width
 * GitHub actually renders a README image at, without a multi-megabyte PNG.
 */
const VIEW = { width: 1440, height: 800 }
const SCALE = 1.5

const freePort = () =>
  new Promise((res, rej) => {
    const s = net.createServer()
    s.once('error', rej)
    s.listen(0, '127.0.0.1', () => {
      const p = s.address().port
      s.close(() => res(p))
    })
  })

async function boot() {
  const workdir = mkdtempSync(path.join(os.tmpdir(), 'tavotto-readme-'))
  const home = path.join(workdir, 'home')
  const dataDir = path.join(workdir, 'data')
  const figures = path.join(workdir, 'figures')
  mkdirSync(home, { recursive: true })
  mkdirSync(dataDir, { recursive: true })
  cpSync(path.join(REPO, 'examples', 'figures'), figures, { recursive: true })

  const port = await freePort()
  const proc = spawn(
    PY,
    ['-m', 'tavotto', '--port', String(port), '--no-browser', '--figures', figures],
    {
      env: {
        ...process.env,
        TAVOTTO_DATA_DIR: dataDir,
        TAVOTTO_CONFIG_DIR: path.join(workdir, 'config'),
        TAVOTTO_ALLOW_SHUTDOWN: '1',
        // A throwaway profile is asked for telemetry consent on first run; the
        // hard switch keeps the consent dialog off a screenshot of the editor.
        TAVOTTO_NO_TELEMETRY: '1',
        HOME: home,
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    },
  )
  const logs = []
  proc.stdout.on('data', (b) => logs.push(String(b)))
  proc.stderr.on('data', (b) => logs.push(String(b)))

  const baseURL = `http://127.0.0.1:${port}`
  for (let i = 0; i < 240; i++) {
    if (proc.exitCode !== null) throw new Error(`app exited early\n${logs.join('')}`)
    try {
      const r = await fetch(`${baseURL}/api/version`)
      if (r.ok) break
    } catch {
      /* still starting */
    }
    await new Promise((r) => setTimeout(r, 500))
  }

  // The app admits a browser only through the credentialed link it prints
  // (session authentication, ADR 0008): a bare `/` renders "This page has no
  // Tavotto session". The link is on stdout as `* 打开 http://…/#dnonce=…`, so
  // wait for it rather than for `/api/version` alone.
  let entry = null
  for (let i = 0; i < 40 && !entry; i++) {
    entry = logs.join('').match(/(http:\/\/127\.0\.0\.1:\d+\/#dnonce=[^\s]+)/)?.[1] ?? null
    if (!entry) await new Promise((r) => setTimeout(r, 250))
  }
  if (!entry) throw new Error(`the app never printed its credentialed link\n${logs.join('')}`)

  const browser = await chromium.launch()
  const ctx = await browser.newContext({ viewport: VIEW, deviceScaleFactor: SCALE, locale: LOCALE })
  await ctx.addInitScript((l) => window.localStorage.setItem('tavotto.locale', l), LOCALE)
  const page = await ctx.newPage()
  await page.goto(entry)

  return {
    page,
    logs,
    async close() {
      await browser.close()
      await fetch(`${baseURL}/api/shutdown`, { method: 'POST' }).catch(() => {})
      await new Promise((r) => setTimeout(r, 800))
      if (proc.exitCode === null) proc.kill('SIGKILL')
      rmSync(workdir, { recursive: true, force: true })
    },
  }
}

/* ------------------------------------------------------------------ */

const app = await boot()
const { page } = app

/** The numeric fields are labelled `X (mm)` … `H (mm)` (2026-09 inspector). */
const field = (label) => page.getByLabel(S.mm(label), { exact: true }).first()

async function setField(label, value) {
  const el = field(label)
  await el.click()
  await el.fill(String(value))
  await el.press('Enter')
  await page.waitForTimeout(300)
}

/**
 * Left drawers are toggles: clicking the rail button of the drawer that is
 * already open closes it. Open by what the drawer actually shows, never by
 * clicking the button and hoping.
 */
async function ensureDrawer(railButton, tell) {
  if (await tell.isVisible().catch(() => false)) return
  await page.getByRole('button', { name: railButton, exact: true }).click()
  await tell.waitFor({ state: 'visible', timeout: 15_000 })
  await page.waitForTimeout(500)
}

const ensureAssets = () => ensureDrawer(S.assets, page.getByLabel(S.searchPanels).first())
const ensureFigureElements = () =>
  ensureDrawer(S.figureElements, page.getByLabel(S.searchElements).first())

async function addPanel(name, { x, y, w }) {
  await ensureAssets()
  // The card is a listbox option: Enter opens the figure for editing,
  // Shift+Enter adds it to the canvas (AssetBrowser.tsx, `CARD_KEYSHORTCUTS`).
  await page.locator(`li[data-card="${name}"]`).focus()
  await page.keyboard.press('Shift+Enter')
  await page.waitForTimeout(2500)
  await page.getByRole('tab', { name: S.properties }).click()
  await page.waitForTimeout(400)
  await setField('W', w) // width first: height follows through the aspect link
  await setField('X', x)
  await setField('Y', y)
}

async function shot(name, clip) {
  const file = `${name}${SUFFIX}.png`
  await page.screenshot({ path: path.join(OUT, file), clip })
  console.log('→', path.join('assets/readme', file))
}

try {
  await page.waitForTimeout(2500)

  // A double-column page at 4:3 — one of the profile's accepted shapes — with
  // the three panels close to the size their scripts drew them at.
  await page.getByRole('tab', { name: S.canvas }).click()
  await page.waitForTimeout(700)
  await setField('W', 150)
  await setField('H', 112.5)

  // Panel (a) at the 80 mm its script drew it (the figure size, not the
  // tight PDF box), so the inside-the-figure shot is not cropped by a frame
  // narrower than the figure; (b) and (c) at the profile's single column.
  await addPanel('Fig1_kinetics.pdf', { x: 5, y: 8, w: 80 })
  await addPanel('Fig2_correlation.pdf', { x: 88, y: 8, w: 57 })
  await addPanel('Fig2_yield.pdf', { x: 88, y: 62, w: 57 })

  await page.getByRole('button', { name: S.panelLabels }).click()
  await page.waitForTimeout(1200)
  await page.keyboard.press('Escape')
  await page.waitForTimeout(400)

  // ── 1 · the page, with one panel selected and its physical size on show ──
  await ensureAssets()
  // The drawer auto-hides the moment the canvas is clicked; pin it first.
  await page.getByRole('button', { name: S.pin }).click().catch(() => {})
  await page.waitForTimeout(400)
  await page.getByRole('button', { name: S.fitCanvas }).click()
  await page.waitForTimeout(900)
  await page.locator('[data-canvas-stage]').click({ position: { x: 300, y: 220 } })
  await page.waitForTimeout(4000) // let the "panel labels added" toast expire
  await shot('layout')

  // ── 2 · inside a figure: element tree, canvas, properties of the title ──
  // Two buttons carry this name once a panel is selected: the inspector's and
  // the quick-edit bar's. Either works; the inspector's is the first in DOM.
  await page.getByRole('button', { name: S.editElements }).first().click()
  await page
    .getByText(S.building)
    .first()
    .waitFor({ state: 'detached', timeout: 300_000 })
    .catch(() => {})
  await page.waitForTimeout(2500)
  await ensureFigureElements()
  await page.getByRole('button', { name: S.fitCanvas }).click()
  await page.waitForTimeout(900)
  // The tree opens with its groups expanded, and every row carries the
  // element's gid in `data-el` — language-neutral, so the title is addressed
  // by what it is rather than by what this locale calls it.
  await page.locator('li[role="treeitem"][data-el="axes_0.title"]').click()
  await page.waitForTimeout(1500)
  await shot('workbench')

  // ── 3 · the publication preflight that runs before every export ──
  await page.keyboard.press('Escape')
  await page.waitForTimeout(800)
  await page.getByRole('button', { name: S.export, exact: true }).click()
  await page.waitForTimeout(4000)
  // Since the 2026-09 export dialog the checks are a block of the dialog
  // itself — the counts by severity, the first findings with their values, the
  // written confirmation a blocking finding demands — so the shot is the
  // dialog, not a list cropped out of it. What it found is logged so the
  // README's caption can be checked against the run.
  const dialog = page.getByRole('dialog')
  console.log('export dialog:\n  ' + (await dialog.innerText()).split('\n').join('\n  '))
  const bb = await dialog.boundingBox()
  await shot('preflight', bb
    ? { x: bb.x - 12, y: bb.y - 12, width: bb.width + 24, height: bb.height + 24 }
    : undefined)
} catch (e) {
  console.error('capture failed:', e.message)
  await page.screenshot({ path: path.join(os.tmpdir(), 'tavotto-capture-failure.png') })
  console.error('last frame: ' + path.join(os.tmpdir(), 'tavotto-capture-failure.png'))
  console.error(app.logs.join('').slice(-3000))
  process.exitCode = 1
}
await app.close()
