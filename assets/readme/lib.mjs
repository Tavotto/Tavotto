/**
 * Shared by capture.mjs (stills) and record-demo.mjs (the walkthrough): boots a
 * real Tavotto against a throwaway user directory and drives it in the bundled
 * Playwright Chromium. Nothing here stages or retouches what is photographed.
 *
 * Requires: `pnpm install` + `npx playwright install chromium` in `web/`, and a
 * Python with the package installed (`pip install -e ".[worker]"`; from a
 * worktree also `PYTHONPATH=$PWD/src`). Run `python scripts/build_frontend.py`
 * first — the packaged `src/tavotto/web/` wins over `web/dist`, so otherwise you
 * photograph the previous interface — and `python scripts/fetch_fonts.py`.
 */
import { spawn } from 'node:child_process'
import { cpSync, existsSync, mkdirSync, mkdtempSync, rmSync } from 'node:fs'
import { createRequire } from 'node:module'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'

export const REPO = path.resolve(import.meta.dirname, '..', '..')
export const OUT = path.join(REPO, 'assets', 'readme')
// Playwright lives in web/node_modules; anchor the resolution there so the
// scripts run from any directory rather than only from `web/`.
const { chromium } = createRequire(path.join(REPO, 'web', 'package.json'))('@playwright/test')
const PY = process.env.TAVOTTO_PYTHON ?? path.join(REPO, '.venv', 'bin', 'python')

/** 1440 × 900 CSS px at 2× = 2880 × 1800: sharp on a Retina display. */
export const VIEW = { width: 1440, height: 900 }
export const SCALE = 2

/**
 * Every string a script clicks on, per interface language — keep it in step
 * with web/src/i18n/locales/. The language is forced, not inherited, so each
 * README shows the interface its readers will actually get.
 */
export const STRINGS = {
  'en-US': {
    assets: 'Assets', properties: 'Properties', canvas: 'Canvas', timeline: 'Timeline',
    assistant: 'Assistant', exportStart: 'Export', fitCanvas: 'Fit canvas', fitShort: 'Fit', export: 'Export',
    searchPanels: 'Search figures', panelLabels: 'Add panel labels',
    editElements: 'Edit figure elements', figureElements: 'Figure elements', connectAi: 'Connect AI',
    searchElements: 'Search figure elements',
    leaveEditing: /Leave figure editing|Back to canvas/, saveToProject: 'Save to project', untitled: /Untitled/, rename: 'Rename layout…', nameNow: 'Name this moment…',
    layoutName: 'Kinetics study · Figure 2', tabName: 'Figure 2',
    moments: ['Three panels', 'Bottom row 52 mm'],
    building: /Building/,
    mm: (axis) => `${axis} (mm)`,
  },
  'zh-CN': {
    assets: '素材', properties: '属性', canvas: '画布', timeline: '时间线',
    assistant: '改图助手', exportStart: '开始导出', fitCanvas: '适应画布', fitShort: '适应', export: '导出',
    searchPanels: '搜索图', panelLabels: '添加序号标签',
    editElements: '改图里的内容', figureElements: '图内元素', connectAi: '连接 AI',
    searchElements: '搜索图内元素',
    leaveEditing: /退出图内编辑|返回画布/, saveToProject: '存进项目', untitled: /新排版|未命名/, rename: '重命名排版…', nameNow: '给现在存个名字…',
    layoutName: '反应动力学 · 图 2', tabName: '图 2',
    moments: ['三幅图', '下排 52 mm'],
    building: /正在构建|构建中/,
    mm: (axis) => `${axis} (mm)`,
  },
}

const freePort = () =>
  new Promise((res, rej) => {
    const s = net.createServer()
    s.once('error', rej)
    s.listen(0, '127.0.0.1', () => {
      const p = s.address().port
      s.close(() => res(p))
    })
  })

/**
 * `opts.workdir` boots against a directory the caller owns (kept on close), so a
 * second process — the desktop window in hero-window.mjs — can reopen the same
 * project, user data and layout session.
 */
export async function boot(locale, recordVideoDir, opts = {}) {
  const workdir = opts.workdir ?? mkdtempSync(path.join(os.tmpdir(), 'tavotto-readme-'))
  const home = path.join(workdir, 'home')
  const dataDir = path.join(workdir, 'data')
  // A project folder with an ordinary name: it shows in the top bar and in the
  // export dialog's "Location" line.
  const figures = path.join(workdir, 'Research', 'Papers', 'kinetics-paper')
  mkdirSync(home, { recursive: true })
  mkdirSync(dataDir, { recursive: true })
  if (!opts.workdir || !existsSync(figures)) cpSync(path.join(REPO, 'examples', 'figures'), figures, { recursive: true })

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
  const ctx = await browser.newContext({
    viewport: VIEW,
    deviceScaleFactor: SCALE,
    locale,
    ...(recordVideoDir ? { recordVideo: { dir: recordVideoDir, size: VIEW } } : {}),
  })
  await ctx.addInitScript((l) => window.localStorage.setItem('tavotto.locale', l), locale)
  const page = await ctx.newPage()
  await page.goto(entry)

  return {
    page,
    logs,
    workdir,
    figures,
    async close() {
      await ctx.close()
      await browser.close()
      await fetch(`${baseURL}/api/shutdown`, { method: 'POST' }).catch(() => {})
      await new Promise((r) => setTimeout(r, 800))
      if (proc.exitCode === null) proc.kill('SIGKILL')
      if (!opts.workdir) rmSync(workdir, { recursive: true, force: true })
    },
  }
}

/** Page helpers bound to one page and one language table. */
export function driver(page, S) {
  const pause = (ms) => page.waitForTimeout(ms)
  /** The numeric fields are labelled `X (mm)` … `H (mm)`. */
  const field = (label) => page.getByLabel(S.mm(label), { exact: true }).first()

  async function setField(label, value) {
    const el = field(label)
    await el.click()
    await el.fill(String(value))
    await el.press('Enter')
    await pause(300)
  }

  /**
   * Left drawers are toggles: clicking the rail button of the drawer that is
   * already open closes it. Open by what the drawer actually shows.
   */
  async function ensureAssets() {
    const tell = page.getByLabel(S.searchPanels, { exact: true }).first()
    if (await tell.isVisible().catch(() => false)) return
    await page.getByRole('button', { name: S.assets, exact: true }).click()
    await tell.waitFor({ state: 'visible', timeout: 15_000 })
    await pause(500)
  }

  async function addPanel(name, { x, y, w }) {
    await ensureAssets()
    // The card is a listbox option: Shift+Enter adds it to the canvas.
    await page.locator(`li[data-card="${name}"]`).focus()
    await page.keyboard.press('Shift+Enter')
    await pause(2500)
    await page.getByRole('tab', { name: S.properties }).click()
    await pause(400)
    await setField('W', w) // width first: height follows through the aspect link
    await setField('X', x)
    await setField('Y', y)
  }

  /** Panel `i` (0 = kinetics, 1 = correlation, 2 = yield), in the order they were added. */
  const panel = (i) => page.locator('[data-object-id^="p_"]').nth(i)

  async function selectPanel(i) {
    const box = await panel(i).boundingBox()
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2)
    await pause(700)
    await page.getByRole('tab', { name: S.properties }).click()
    await pause(400)
  }

  async function renameLayout() {
    await page.getByText(S.untitled).first().click()
    await page.getByText(S.rename).click()
    await pause(500)
    await page.keyboard.press('ControlOrMeta+a')
    await page.keyboard.type(S.layoutName)
    await page.keyboard.press('Enter')
    await pause(1200)
  }

  /**
   * A 150 × 125 mm page in the shape journals call a double column: panel (a)
   * across the top, (b) and (c) side by side below, every panel at the size its
   * script drew it (scale 100 %), labelled, and the layout given a name.
   */
  async function buildLayout() {
    await pause(3000)
    await page.getByRole('tab', { name: S.canvas }).click()
    await pause(500)
    await setField('W', 150)
    await setField('H', 125)
    await addPanel('Fig1_kinetics.pdf', { x: 38, y: 7, w: 73.3 })
    await addPanel('Fig2_correlation.pdf', { x: 14, y: 70, w: 57 })
    await addPanel('Fig2_yield.pdf', { x: 79, y: 70, w: 57 })
    await page.getByRole('button', { name: S.panelLabels }).click()
    await pause(1200)
    await page.keyboard.press('Escape')
    await pause(500)
    // Label (c) lands on the 6 tick of the bar chart's y axis; the labels are
    // ordinary text objects, so nudge it up and left (arrow keys move 1 mm).
    const lb = await page.locator('[data-object-id^="t_"]').nth(2).boundingBox()
    await page.mouse.click(lb.x + lb.width / 2, lb.y + lb.height / 2)
    await pause(300)
    for (let i = 0; i < 4; i++) await page.keyboard.press('ArrowLeft')
    for (let i = 0; i < 3; i++) await page.keyboard.press('ArrowUp')
    await page.keyboard.press('Escape')
    await pause(300)
    await renameLayout()
    // The canvas tab is named like the layout's figure.
    await page.locator('[data-canvas-tab]').first().dblclick()
    await pause(400)
    await page.keyboard.press('ControlOrMeta+a')
    await page.keyboard.type(S.tabName)
    await page.keyboard.press('Enter')
    await pause(600)
    // The properties column is a little wider than its default, so the scale row's
    // "Original 73.3 × 57.8 mm" is not cut short.
    const hb = await page.locator('[data-resize-handle="inspector"]').boundingBox()
    await page.mouse.move(hb.x + hb.width / 2, hb.y + 300)
    await page.mouse.down()
    await page.mouse.move(hb.x - 100, hb.y + 300, { steps: 10 })
    await page.mouse.up()
    await pause(600)
    await page.getByRole('button', { name: S.fitCanvas, exact: true }).click()
    await page.evaluate(() => document.activeElement?.blur())
    await quickSave()
    await pause(5000) // let the "panel labels added" and hint toasts expire
  }

  /**
   * ⌘S: the first time it asks where to put the layout (the project), after that
   * it updates the file — so the top bar reads "Saved to project", never "Not saved".
   */
  async function quickSave() {
    await page.keyboard.press('ControlOrMeta+s')
    await pause(900)
    const ask = page.getByRole('button', { name: S.saveToProject })
    if (await ask.isVisible().catch(() => false)) await ask.click()
    await pause(1800)
  }

  return { pause, field, setField, ensureAssets, addPanel, panel, selectPanel, renameLayout, quickSave, buildLayout }
}
