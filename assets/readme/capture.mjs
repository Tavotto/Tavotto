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
    searchPanels: 'Search panels…', searchElements: 'Search name / role / gid',
    textGroup: 'Text', title: 'Title “Reaction kinetics”',
    add: (n) => `Add ${n} to the canvas`,
  },
  'zh-CN': {
    assets: '素材', figureElements: '图内元素', properties: '属性',
    canvas: '画布', fitCanvas: '适应画布', pin: '钉住侧栏',
    panelLabels: '添加序号标签', editElements: '编辑图内元素',
    export: '导出', building: /正在构建|构建中/, preflight: /预检/,
    searchPanels: '搜索面板…', searchElements: '搜索名称 / 角色 / gid',
    textGroup: '文字', title: '标题 “Reaction kinetics”',
    add: (n) => `把 ${n} 加入画布`,
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

  const browser = await chromium.launch()
  const ctx = await browser.newContext({ viewport: VIEW, deviceScaleFactor: SCALE, locale: LOCALE })
  await ctx.addInitScript((l) => window.localStorage.setItem('tavotto.locale', l), LOCALE)
  const page = await ctx.newPage()
  await page.goto(baseURL)

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

/** The numeric fields carry their name in the scrub handle next to the input. */
const field = (label) =>
  page.locator(`div:has(> span:text-is("${label}")) > input.num-input`).first()

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

const ensureAssets = () => ensureDrawer(S.assets, page.getByPlaceholder(S.searchPanels))
const ensureFigureElements = () =>
  ensureDrawer(S.figureElements, page.getByPlaceholder(S.searchElements))

async function addPanel(name, { x, y, w }) {
  await ensureAssets()
  await page.getByRole('button', { name: S.add(name) }).click({ timeout: 60_000 })
  await page.waitForTimeout(2000)
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

  await addPanel('Fig1_kinetics.pdf', { x: 8, y: 8, w: 73.3 })
  await addPanel('Fig2_correlation.pdf', { x: 85, y: 8, w: 57 })
  await addPanel('Fig2_yield.pdf', { x: 85, y: 62, w: 57 })

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
  await page.getByRole('button', { name: S.editElements }).click()
  await page
    .getByText(S.building)
    .first()
    .waitFor({ state: 'detached', timeout: 300_000 })
    .catch(() => {})
  await page.waitForTimeout(2500)
  await ensureFigureElements()
  await page.getByRole('button', { name: S.fitCanvas }).click()
  await page.waitForTimeout(900)
  await page.getByText(S.textGroup, { exact: true }).first().click()
  await page.waitForTimeout(700)
  await page.getByText(S.title).first().click()
  await page.waitForTimeout(1500)
  await shot('workbench')

  // ── 3 · the publication preflight that runs before every export ──
  await page.keyboard.press('Escape')
  await page.waitForTimeout(800)
  await page.getByRole('button', { name: S.export, exact: true }).click()
  await page.waitForTimeout(4000)
  const items = await page.locator('button[aria-expanded] ~ ul li').allInnerTexts()
  console.log('preflight findings:\n  ' + items.join('\n  '))
  // Frame the findings list. The dialog is
  // taller than the window, so bring the block into view before measuring.
  const block = page
    .getByRole('dialog')
    .getByRole('button', { name: S.preflight })
    .locator('xpath=..')
  await block.scrollIntoViewIfNeeded()
  await page.waitForTimeout(600)
  const bb = await block.boundingBox()
  await shot('preflight', bb
    ? { x: bb.x - 12, y: bb.y - 6, width: bb.width + 24, height: bb.height + 12 }
    : undefined)
} catch (e) {
  console.error('capture failed:', e.message)
  await page.screenshot({ path: path.join(os.tmpdir(), 'tavotto-capture-failure.png') })
  console.error('last frame: ' + path.join(os.tmpdir(), 'tavotto-capture-failure.png'))
  console.error(app.logs.join('').slice(-3000))
  process.exitCode = 1
}
await app.close()
