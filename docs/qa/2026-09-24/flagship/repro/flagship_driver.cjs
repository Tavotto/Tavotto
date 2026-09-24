/* eslint-disable */
/**
 * QA-FLAGSHIP-01 / ACCEPT-1 / ACCEPT-2 驱动：真实后端（HTTP，会话认证开）+ Playwright 真 Chromium。
 *
 * 入口：浏览器模式（`python -m tavotto --no-browser` 打印的 `#dnonce=` 落地 URL），**不是**桌面壳。
 * 全新 TAVOTTO_DATA_DIR / CONFIG_DIR / HOME；PATH 最前面是 E1 的环境 B；不设 TAVOTTO_WORKER_PYTHON。
 *
 * 独立参考（不调用任何生产坐标函数）：
 *   T：图内分数坐标（top-origin）↔ 屏幕 CSS px，取自 `[data-element-svg] svg` 根节点的 getBoundingClientRect
 *      （SVG viewBox = 整张 figure，先验 viewBox 长宽比 = 屏幕框长宽比）。
 *   拖动 Δs 的期望：anchor' = anchor + Δs / (rect.w, rect.h)。
 *   组缩放（右下手柄）：c = 组框左上角，s = 主导比例；box' = c + s (box − c)。
 *   对齐 / 分布：按 layoutBoxes 的书面合同（选区边界 / 等间距、首尾不动）独立重算。
 *
 * 用法（在 worktree 根目录）：
 *   node docs/qa/2026-09-24/flagship/repro/flagship_driver.cjs <fixture_dir> <run_dir>
 * 环境：QA_PORT（默认 5306）、QA_PY（后端解释器）、QA_FONTS（批准字体目录，只读）
 */
const { createRequire } = require('node:module')
const path = require('node:path')
const fs = require('node:fs')
const crypto = require('node:crypto')
const { spawn, execFileSync } = require('node:child_process')

const WT = path.resolve(__dirname, '..', '..', '..', '..', '..')
const req = createRequire(path.join(WT, 'web', 'package.json'))
const { chromium } = req('@playwright/test')

const FIX = path.resolve(process.argv[2])
const RUN = path.resolve(process.argv[3])
const PORT = Number(process.env.QA_PORT || 5306)
const PY = process.env.QA_PY || '/Volumes/Projects/Tavotto/.venv/bin/python'
const FONTS = process.env.QA_FONTS || ''
const STOP_AFTER = process.env.QA_STOP_AFTER || ''
const TRUTH = JSON.parse(fs.readFileSync(path.join(FIX, 'truth.json'), 'utf-8'))
const PAPER = path.join(FIX, 'paper')

fs.rmSync(RUN, { recursive: true, force: true })
for (const d of ['data', 'config', 'home', 'snap']) fs.mkdirSync(path.join(RUN, d), { recursive: true })

const sha = (buf) => crypto.createHash('sha256').update(buf).digest('hex')
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const now = () => new Date().toISOString()

// ------------------------------------------------------------------ 记账
const report = {
  started_at: now(),
  port: PORT,
  entry: 'HTTP + Playwright Chromium（浏览器模式落地 URL，会话认证开）；非桌面壳',
  fixture: FIX,
  run_dir: RUN,
  phases: [],
  first_deviation: null,
  checks: [],
  snapshots: {},
  exports: {},
  env_evidence: {},
  data_evidence: {},
  side_effects: {},
}
let currentPhase = null
function check(name, ok, detail) {
  const c = { phase: currentPhase, name, ok: !!ok, detail }
  report.checks.push(c)
  console.log(`${ok ? 'PASS' : 'FAIL'} [${currentPhase}] ${name}${detail !== undefined ? ' :: ' + JSON.stringify(detail).slice(0, 400) : ''}`)
  if (!ok && !report.first_deviation) report.first_deviation = { phase: currentPhase, check: name, detail }
  return !!ok
}
function saveReport() {
  report.updated_at = now()
  fs.writeFileSync(path.join(RUN, 'report.json'), JSON.stringify(report, null, 2))
}
async function phase(name, fn) {
  currentPhase = name
  const p = { name, started_at: now(), ok: null }
  report.phases.push(p)
  console.log(`\n=== ${name}`)
  try {
    await fn()
    p.ok = !report.checks.some((c) => c.phase === name && !c.ok)
  } catch (e) {
    p.ok = false
    p.error = String(e && e.stack ? e.stack : e).slice(0, 2000)
    check('phase threw', false, p.error.split('\n')[0])
  }
  p.finished_at = now()
  saveReport()
  if (STOP_AFTER && STOP_AFTER === name) throw new Error('STOP_AFTER ' + name)
  return p.ok
}

// ------------------------------------------------------------------ 进程与 HTTP
function treeHashes(root, skip = ['.venv']) {
  const out = {}
  const walk = (d) => {
    for (const n of fs.readdirSync(d)) {
      const p = path.join(d, n)
      const rel = path.relative(root, p)
      if (skip.some((s) => rel === s || rel.startsWith(s + path.sep))) continue
      const st = fs.lstatSync(p)
      if (st.isDirectory()) walk(p)
      else out[rel] = sha(fs.readFileSync(p))
    }
  }
  walk(root)
  return out
}
function distList(python) {
  return execFileSync(python, ['-c', "import importlib.metadata as m, json; print(json.dumps(sorted(set((d.metadata['Name'] or '').lower() for d in m.distributions()))))"], { encoding: 'utf-8' }).trim()
}
function sitePackagesListing(venv) {
  const lib = path.join(venv, 'lib')
  const py = fs.readdirSync(lib).find((n) => n.startsWith('python'))
  return fs.readdirSync(path.join(lib, py, 'site-packages')).sort()
}
function processesMentioning(needle) {
  const out = execFileSync('ps', ['-axo', 'pid=,command='], { encoding: 'utf-8' })
  return out.split('\n').filter((l) => l.includes(needle) && !l.includes('ps -axo') && !l.includes('flagship_driver.cjs'))
}

class Server {
  constructor(tag) {
    this.tag = tag
    this.log = ''
  }
  async start() {
    const env = { ...process.env }
    for (const k of ['TAVOTTO_WORKER_PYTHON', 'MM_WORKER_PYTHON', 'TAVOTTO_INSECURE_NO_AUTH', 'VIRTUAL_ENV']) delete env[k]
    Object.assign(env, {
      PATH: `${path.join(FIX, 'envB', 'bin')}:${process.env.PATH}`,
      PYTHONPATH: path.join(WT, 'src'),
      PYTHONDONTWRITEBYTECODE: '1',
      TAVOTTO_DATA_DIR: path.join(RUN, 'data'),
      TAVOTTO_CONFIG_DIR: path.join(RUN, 'config'),
      HOME: path.join(RUN, 'home'),
      TAVOTTO_NO_TELEMETRY: '1',
      TAVOTTO_NO_UPDATE_CHECK: '1',
      TAVOTTO_ALLOW_SHUTDOWN: '1',
    })
    if (FONTS) env.TAVOTTO_FONTS_DIR = FONTS
    this.env_public = { PATH_head: env.PATH.split(':')[0], TAVOTTO_WORKER_PYTHON: env.TAVOTTO_WORKER_PYTHON ?? null, TAVOTTO_FONTS_DIR: env.TAVOTTO_FONTS_DIR ?? null }
    this.proc = spawn(PY, ['-m', 'tavotto', '--port', String(PORT), '--no-browser', '--figures', PAPER], { env, cwd: RUN })
    this.proc.stdout.on('data', (b) => { this.log += b })
    this.proc.stderr.on('data', (b) => { this.log += b })
    for (let i = 0; i < 400; i++) {
      const m = /\* 打开 (\S+#dnonce=\S+)/.exec(this.log)
      if (m) { this.url = m[1]; break }
      if (this.proc.exitCode !== null) throw new Error('server exited early:\n' + this.log.slice(-3000))
      await sleep(300)
    }
    if (!this.url) throw new Error('no landing url:\n' + this.log.slice(-3000))
    for (let i = 0; i < 200; i++) {
      try { const r = await fetch(`http://127.0.0.1:${PORT}/api/version`); if (r.ok) { this.version = await r.json(); break } } catch {}
      await sleep(300)
    }
    const cred = JSON.parse(fs.readFileSync(path.join(RUN, 'data', 'session', `port-${PORT}.json`), 'utf-8'))
    this.auth = { 'X-Tavotto-Auth': cred.secret }
    // 默认拒绝：未认证 401
    const denied = await fetch(`http://127.0.0.1:${PORT}/api/panels`)
    this.unauth_status = denied.status
  }
  async api(p, body, method) {
    const r = await fetch(`http://127.0.0.1:${PORT}${p}`, {
      method: method || (body ? 'POST' : 'GET'),
      headers: { ...this.auth, ...(body ? { 'Content-Type': 'application/json' } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    })
    let j = null
    try { j = await r.json() } catch {}
    return { status: r.status, body: j }
  }
  async stop() {
    try { await this.api('/api/shutdown', {}) } catch {}
    for (let i = 0; i < 100 && this.proc.exitCode === null; i++) await sleep(200)
    if (this.proc.exitCode === null) { this.proc.kill('SIGKILL'); this.killed = true }
    fs.writeFileSync(path.join(RUN, `server-${this.tag}.log`), this.log)
    return this.proc.exitCode
  }
}

// ------------------------------------------------------------------ 浏览器侧记录
function wirePage(page, rec) {
  page.on('request', (r) => {
    const u = r.url()
    if (u.includes('/api/engine/render')) rec.renderReqs.push({ at: Date.now(), body: r.postData() })
    if (u.includes('/api/autosave/') && r.method() === 'PUT') {
      try { rec.docs.push({ at: Date.now(), url: u, doc: JSON.parse(r.postData()) }) } catch {}
    }
    if (u.includes('/api/export')) rec.exportReqs.push({ at: Date.now(), url: u, body: r.postData() })
  })
  page.on('response', async (r) => {
    const u = r.url()
    if (u.includes('/api/engine/render')) {
      let body = null
      try { body = await r.json() } catch {}
      let reqBody = null
      try { reqBody = JSON.parse(r.request().postData() || 'null') } catch {}
      rec.renders.push({ at: Date.now(), status: r.status(), body, req: reqBody })
    }
    if (u.includes('/api/autosave/') && r.request().method() === 'PUT') rec.autosaveStatus.push({ at: Date.now(), status: r.status() })
  })
}

const OK_RENDERS = (rec, since = 0) => rec.renders.filter((r) => r.status === 200 && r.at >= since && r.body && r.body.manifest)

async function waitSettled(page, rec, since, { minRenders = 1, quietMs = 1500, timeout = 120000 } = {}) {
  const t0 = Date.now()
  for (;;) {
    const ok = OK_RENDERS(rec, since)
    const lastReq = Math.max(0, ...rec.renderReqs.map((r) => r.at))
    const lastResp = Math.max(0, ...rec.renders.map((r) => r.at))
    const pending = rec.renderReqs.filter((r) => r.at >= since).length > rec.renders.filter((r) => r.at >= since).length
    if (ok.length >= minRenders && !pending && Date.now() - Math.max(lastReq, lastResp) > quietMs) {
      const auth = await page.locator('[data-authority="ready"]').count()
      if (auth > 0) return ok.at(-1)
    }
    if (Date.now() - t0 > timeout) {
      await page.screenshot({ path: path.join(RUN, 'snap', `unsettled-${Date.now()}.png`) }).catch(() => {})
      throw new Error(`render did not settle (ok=${ok.length}, pending=${pending}, reqs_since=${rec.renderReqs.filter((r) => r.at >= since).length}, non200_since=${rec.renders.filter((r) => r.at >= since && r.status !== 200).map((r) => r.status + ':' + JSON.stringify(r.body).slice(0, 200)).join('|')})`)
    }
    await sleep(250)
  }
}

async function svgRect(page) {
  return page.evaluate(() => {
    const svg = document.querySelector('[data-element-svg] svg')
    if (!svg) return null
    const r = svg.getBoundingClientRect()
    const vb = (svg.getAttribute('viewBox') || '').split(/\s+/).map(Number)
    return { x: r.left, y: r.top, w: r.width, h: r.height, vb }
  })
}
const toScreen = (R, fx, fy) => ({ x: R.x + fx * R.w, y: R.y + fy * R.h })
/** 整张图必须完整落在画布舞台里（被侧栏盖住的点点不到）：不在就 ⌘− 缩小视图（视图倍率不改文档） */
async function ensureVisible(page) {
  for (let i = 0; i < 5; i++) {
    const R = await svgRect(page)
    const st = await page.evaluate(() => {
      const n = document.querySelector('[data-canvas-stage]')
      if (!n) return null
      const r = n.getBoundingClientRect()
      return { x: r.left, y: r.top, w: r.width, h: r.height }
    })
    if (R && st && R.x >= st.x + 4 && R.y >= st.y + 4 && R.x + R.w <= st.x + st.w - 4 && R.y + R.h <= st.y + st.h - 4) return { R, zoomed_out: i }
    await page.evaluate(() => document.activeElement && document.activeElement.blur())
    await page.keyboard.press('Meta+Minus')
    await sleep(900)
  }
  return { R: await svgRect(page), zoomed_out: -1 }
}
const byGid = (manifest) => Object.fromEntries(manifest.elements.map((e) => [e.gid, e]))
const center = (b) => [b[0] + b[2] / 2, b[1] + b[3] / 2]

async function selectedRows(page) {
  return page.evaluate(() => [...document.querySelectorAll('[data-el][aria-selected="true"]')].map((e) => e.getAttribute('data-el')))
}

function lastDoc(rec) {
  const d = rec.docs.at(-1)
  return d ? d.doc : null
}
function panelOf(doc) {
  if (!doc) return null
  const canv = doc.canvases ? doc.canvases.find((c) => c.id === doc.activeCanvasId) || doc.canvases[0] : doc
  const objs = (canv.doc || canv).objects || []
  return objs.find((o) => o.type === 'panel') || null
}

async function snapshot(page, rec, name, since, opts = {}) {
  const r = await waitSettled(page, rec, since, opts)
  // 等一次在动作之后的自动保存（文档快照）
  const t0 = Date.now()
  while (!rec.docs.some((d) => d.at >= since) && Date.now() - t0 < (opts.docTimeout ?? 15000)) await sleep(250)
  const doc = rec.docs.filter((d) => d.at >= since).at(-1)?.doc ?? lastDoc(rec)
  const dir = path.join(RUN, 'snap')
  const man = JSON.stringify(r.body.manifest, null, 1)
  fs.writeFileSync(path.join(dir, `${name}.manifest.json`), man)
  fs.writeFileSync(path.join(dir, `${name}.svg`), r.body.svg)
  fs.writeFileSync(path.join(dir, `${name}.request.json`), JSON.stringify(r.req, null, 1))
  if (doc) fs.writeFileSync(path.join(dir, `${name}.document.json`), JSON.stringify(doc, null, 1))
  await page.screenshot({ path: path.join(dir, `${name}.png`) })
  const panel = panelOf(doc)
  report.snapshots[name] = {
    at: now(),
    manifest_sha256: sha(man),
    svg_sha256: sha(r.body.svg),
    document_sha256: doc ? sha(JSON.stringify(doc, null, 1)) : null,
    render_request_patches: r.req ? r.req.patches : null,
    render_count_total: rec.renderReqs.length,
    overrides: panel ? panel.overrides : null,
    rev: r.body.rev,
  }
  return { manifest: r.body.manifest, svg: r.body.svg, doc, req: r.req }
}

// ------------------------------------------------------------------ 独立数据核对
function linePathYs(svg, gid) {
  const re = new RegExp(`<g id="${gid.replace(/\./g, '\\.')}"[\\s\\S]*?<path d="([^"]+)"`)
  const m = re.exec(svg)
  if (!m) return null
  const pts = [...m[1].matchAll(/[ML]\s*(-?[\d.]+)\s+(-?[\d.]+)/g)].map((q) => [Number(q[1]), Number(q[2])])
  return pts
}
/** 最小二乘 y_svg = a + b*y_data，回残差最大值（pt）与 b */
function affineFit(ysSvg, ysData) {
  const n = ysData.length
  const mx = ysData.reduce((a, b) => a + b, 0) / n
  const my = ysSvg.reduce((a, b) => a + b, 0) / n
  let sxy = 0, sxx = 0
  for (let i = 0; i < n; i++) { sxy += (ysData[i] - mx) * (ysSvg[i] - my); sxx += (ysData[i] - mx) ** 2 }
  const b = sxy / sxx
  const a = my - b * mx
  const res = Math.max(...ysData.map((v, i) => Math.abs(a + b * v - ysSvg[i])))
  return { a, b, maxResidualPt: res }
}
function dataChecks(svg, manifest, tag, expectY = TRUTH.y_correct, expectProv = TRUTH.expected_provenance_text) {
  const pts = linePathYs(svg, 'axes_0.lines_0')
  const sig = linePathYs(svg, 'axes_2.lines_0')
  const out = {}
  if (!check(`${tag}: points line path present with 4 vertices`, pts && pts.length === 4, pts)) return out
  const ys = pts.map((p) => p[1])
  const fitC = affineFit(ys, expectY)
  const fitD = affineFit(ys, expectY === TRUTH.y_correct ? TRUTH.y_decoy : TRUTH.y_correct)
  out.points = { svg_y: ys, fit_expected: fitC, fit_other: fitD }
  const others = [TRUTH.y_correct, TRUTH.y_decoy, TRUTH.y_changed].filter((s) => JSON.stringify(s) !== JSON.stringify(expectY)).map((s) => affineFit(ys, s))
  out.points.fit_others = others
  check(`${tag}: plotted y sequence == expected ${JSON.stringify(expectY)} (affine residual < 0.01pt, every other candidate > 1pt or wrong sign)`, fitC.maxResidualPt < 0.01 && fitC.b < 0 && others.every((f) => f.maxResidualPt > 1 || f.b >= 0), out.points)
  const xs = pts.map((p) => p[0])
  const dx = xs.slice(1).map((v, i) => v - xs[i])
  check(`${tag}: x spacing uniform (t = 0..3)`, dx.every((d) => Math.abs(d - dx[0]) < 0.01), xs)
  if (sig && sig.length === 4) {
    const fs_ = affineFit(sig.map((p) => p[1]), TRUTH.signal_A)
    const fb = affineFit(sig.map((p) => p[1]), TRUTH.signal_B)
    out.signal = { fitA: fs_, fitB: fb }
    check(`${tag}: twin-axis signal == env A sequence (not B)`, fs_.maxResidualPt < 0.01 && fs_.b < 0 && (fb.maxResidualPt > 1 || fb.b >= 0), out.signal)
  } else check(`${tag}: signal line present`, false, sig)
  const g = byGid(manifest)
  const prov = g['fig.texts_1'] && (g['fig.texts_1'].editable || []).find((f) => f.prop === 'text')
  out.provenance = prov && prov.value
  check(`${tag}: provenance text == ${expectProv}`, prov && prov.value === expectProv, prov && prov.value)
  const legendLabel = (g['axes_2.lines_0'] || {}).label
  check(`${tag}: twin series label says env A`, typeof legendLabel === 'string' && legendLabel.includes('sig A'), legendLabel)
  return out
}

// ------------------------------------------------------------------ 几何核对
const TOL_PX = 0.5
function tolFrac(R) { return { x: TOL_PX / R.w + 1e-4, y: TOL_PX / R.h + 1e-4 } }
function near(a, b, t) { return Math.abs(a - b) <= t }
function anchorsOf(m) {
  const o = {}
  for (const e of m.elements) if (e.anchor) o[e.gid] = e.anchor
  return o
}
function boxesOf(m) {
  const o = {}
  for (const e of m.elements) if (e.bbox) o[e.gid] = e.bbox
  return o
}
function compareAnchors(tag, before, after, expect, R, { movable = null, ignore = [] } = {}) {
  const t = tolFrac(R)
  const A0 = anchorsOf(before), A1 = anchorsOf(after)
  const details = []
  let ok = true
  for (const [gid, exp] of Object.entries(expect)) {
    const got = A1[gid]
    const good = got && near(got[0], exp[0], t.x) && near(got[1], exp[1], t.y)
    details.push({ gid, expected: exp, got, err_px: got ? [(got[0] - exp[0]) * R.w, (got[1] - exp[1]) * R.h] : null })
    ok = ok && !!good
  }
  check(`${tag}: target anchors match independent model (≤${TOL_PX}px)`, ok, details)
  const others = []
  for (const [gid, a0] of Object.entries(A0)) {
    if (gid in expect || ignore.includes(gid)) continue
    const a1 = A1[gid]
    if (!a1 || !near(a1[0], a0[0], t.x) || !near(a1[1], a0[1], t.y)) others.push({ gid, before: a0, after: a1 })
  }
  check(`${tag}: non-target anchors unchanged`, others.length === 0, others)
  return { details, others }
}

// ------------------------------------------------------------------ 主流程
let S1, S2, browser
async function main() {
  const rec = { renderReqs: [], renders: [], docs: [], exportReqs: [], autosaveStatus: [] }
  let ctx, page, R
  const pre = {}
  let M = {}

  await phase('P0 fixture baseline', async () => {
    pre.paper = treeHashes(PAPER)
    pre.external = sha(fs.readFileSync(TRUTH.external_abs))
    pre.venvA_dists = distList(path.join(PAPER, '.venv', 'bin', 'python'))
    pre.venvB_dists = distList(path.join(FIX, 'envB', 'bin', 'python'))
    pre.venvA_site = sitePackagesListing(path.join(PAPER, '.venv'))
    pre.venvB_site = sitePackagesListing(path.join(FIX, 'envB'))
    report.side_effects.before = { paper: pre.paper, external: pre.external, venvA_site: pre.venvA_site, venvB_site: pre.venvB_site }
    check('fixture files match generator hashes', Object.entries(TRUTH.files_sha256).every(([rel, h]) => sha(fs.readFileSync(path.join(FIX, rel))) === h))
    check('config dir starts empty (fresh user config)', fs.readdirSync(path.join(RUN, 'config')).length === 0)
  })

  await phase('P1 first launch (fresh config)', async () => {
    S1 = new Server('S1')
    await S1.start()
    report.server_S1 = { version: S1.version, env: S1.env_public, unauth_status: S1.unauth_status }
    check('unauthenticated API denied (401)', S1.unauth_status === 401, S1.unauth_status)
    check('landing url carries one-time nonce', /#dnonce=/.test(S1.url))
    browser = await chromium.launch()
    report.browser = { name: 'chromium', version: browser.version() }
    ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 }, locale: 'zh-CN' })
    page = await ctx.newPage()
    wirePage(page, rec)
    await page.goto(S1.url)
    await page.getByText('Fig_flagship.pdf').first().waitFor({ timeout: 60000 })
  })

  await phase('P2 first open: workdir confirmation (D1 ambiguity)', async () => {
    await page.getByText('Fig_flagship.pdf').first().dblclick()
    await page.locator('[data-workdir-option]').first().waitFor({ timeout: 180000 })
    const opts = await page.locator('[data-workdir-option]').evaluateAll((els) => els.map((e) => ({ mode: e.getAttribute('data-workdir-option'), checked: !!e.querySelector('input:checked'), text: e.textContent })))
    report.data_evidence.workdir_dialog = opts
    const blocked = rec.renders.filter((r) => r.status !== 200)
    const need = blocked.map((r) => r.body).find((b) => b && b.code === 'workdir_confirmation_required')
    report.data_evidence.required_input = need ? need.confirmation : null
    check('render blocked with workdir_confirmation_required before any run', !!need && OK_RENDERS(rec).length === 0, { blocked: blocked.length, ok: OK_RENDERS(rec).length })
    check('reason = ambiguous_data, conflicts = data/points.csv', need && need.confirmation.reason === 'ambiguous_data' && JSON.stringify(need.confirmation.conflicts) === '["data/points.csv"]', need && need.confirmation.reason)
    check('no option preselected / no recommendation (machine does not decide)', opts.length === 3 && opts.every((o) => !o.checked) && need && need.confirmation.recommended === null, opts.map((o) => [o.mode, o.checked]))
    check('run button disabled until a choice is made', await page.getByRole('button', { name: /运行/ }).last().isDisabled())
    await page.screenshot({ path: path.join(RUN, 'snap', 'P2-workdir-dialog.png') })
    const t = Date.now()
    await page.locator('[data-workdir-option="project_root"]').click()
    await page.getByRole('button', { name: /运行/ }).last().click()
    await page.locator('[data-element-svg] svg').first().waitFor({ timeout: 180000 })
    M.s0 = await snapshot(page, rec, 's0-first-open', t, { timeout: 180000 })
    R = await svgRect(page)
    report.T = R
    const vbAspect = R.vb[2] / R.vb[3]
    check('T sanity: svg viewBox aspect == screen box aspect (±0.5%)', Math.abs(vbAspect / (R.w / R.h) - 1) < 0.005, { vbAspect, screen: R.w / R.h })
    check('figure size_mm = 7×3.2 in', JSON.stringify(M.s0.manifest.size_mm) === JSON.stringify([177.8, 81.28]), M.s0.manifest.size_mm)
  })

  await phase('P3 environment identity (E1)', async () => {
    const envst = await S1.api('/api/engine/environment')
    report.env_evidence.environment = envst.body
    const proj = envst.body && envst.body.project
    const pyRel = proj && proj.python
    const pyAbs = pyRel && (path.isAbsolute(pyRel) ? pyRel : path.join(PAPER, pyRel))
    check('environment endpoint: project python is paper/.venv (A), source project_venv', pyAbs === TRUTH.env_A.executable && /project_venv/.test(JSON.stringify(proj)), { python: pyRel, source: proj && proj.source })
    const panels = await S1.api('/api/panels')
    const panel = panels.body.panels.find((p) => p.id.endsWith('Fig_flagship.pdf'))
    const prep = await S1.api('/api/engine/preparation', { id: panel.id })
    let state = null
    for (let i = 0; i < 600; i++) {
      state = (await S1.api(`/api/engine/preparation/${prep.body.plan.plan_id}`)).body
      if (['ready', 'error', 'cancelled', 'static_source_available', 'needs_input'].includes(state.result.status)) break
      await sleep(250)
    }
    report.env_evidence.preparation = state
    const rc = state.result.receipt || {}
    const rt = rc.runtime || {}
    check('preparation ready without new input (decision remembered)', state.result.status === 'ready' && !state.result.required_input, state.result.status)
    check('receipt python_source = project_venv', rc.python_source === 'project_venv', rc.python_source)
    // 独立核对：回执自报的 worker pid → ps 看它的命令行是不是 paper/.venv/bin/python（不经产品代码）
    let pidCmd = '', pidEnv = '', pidCwd = ''
    try { pidCmd = execFileSync('ps', ['-o', 'command=', '-p', String(rt.pid)], { encoding: 'utf-8' }).trim() } catch {}
    try { pidEnv = execFileSync('ps', ['-E', '-ww', '-o', 'command=', '-p', String(rt.pid)], { encoding: 'utf-8' }) } catch {}
    try { pidCwd = execFileSync('lsof', ['-a', '-p', String(rt.pid), '-d', 'cwd', '-Fn'], { encoding: 'utf-8' }).split('\n').filter((l) => l.startsWith('n')).map((l) => l.slice(1))[0] || '' } catch {}
    const launcher = (/__PYVENV_LAUNCHER__=(\S+)/.exec(pidEnv) || [])[1] || null
    report.env_evidence.worker_pid = { pid: rt.pid, command: pidCmd.slice(0, 400), pyvenv_launcher: launcher, cwd: pidCwd }
    check('worker pid (from receipt) was launched through paper/.venv/bin/python (ps -E __PYVENV_LAUNCHER__ or argv0)', launcher === TRUTH.env_A.executable || pidCmd.startsWith(TRUTH.env_A.executable), report.env_evidence.worker_pid)
    check('worker pid cwd == project root (lsof)', pidCwd && fs.realpathSync(pidCwd) === fs.realpathSync(PAPER), pidCwd)
    const mods = (rt.inputs && rt.inputs.local_modules) || []
    const qa = mods.find((m) => m.name === 'qa_signal')
    const lab = mods.find((m) => m.name === 'labmod')
    check('receipt: qa_signal imported from paper/.venv (env A), not envB', qa && qa.path.startsWith('.venv/') && fs.realpathSync(path.join(PAPER, qa.path)) === fs.realpathSync(TRUTH.env_A.qa_signal_file), qa)
    check('receipt: labmod imported from scripts/labmod.py (local module, not a package)', lab && lab.path === 'scripts/labmod.py', lab)
    const files = (rt.inputs && rt.inputs.files) || []
    const pts = files.find((f) => f.path === 'data/points.csv')
    check('receipt: input data/points.csv sha256 == true file (not decoy)', pts && pts.sha256 === TRUTH.files_sha256['paper/data/points.csv'] && pts.sha256 !== TRUTH.files_sha256['paper/scripts/data/points.csv'], pts)
    report.env_evidence.receipt_inputs = rt.inputs
    report.env_evidence.external_file_in_receipt = files.some((f) => String(f.path).includes('offsets.csv'))
    check('receipt cwd origin = project.root', rc.launch_context && rc.launch_context.cwd_origin === 'project.root', rc.launch_context && rc.launch_context.cwd_origin)
    check('plan does not propose installing anything', JSON.stringify(state.plan.dependency_intents || []) === '[]', state.plan.dependency_intents)
    check('no worker process runs envB python', processesMentioning(path.join(FIX, 'envB')).length === 0)
  })

  await phase('P4 data identity (D1) + external file + local module', async () => {
    report.data_evidence.s0 = dataChecks(M.s0.svg, M.s0.manifest, 's0')
    const g = byGid(M.s0.manifest)
    // 外部绝对路径 × labmod.scale(2.0)：柱高 = offsets × 2 = 1,3,5,7（按柱 bbox 高度比独立核对）
    const bars = [0, 1, 2, 3].map((i) => g[`axes_1.barseries_0.bar_${i}`].bbox[3])
    const exp = TRUTH.offsets.map((o) => o * TRUTH.labmod_scale)
    const ratio = bars.map((b, i) => b / exp[i])
    check('bar heights ∝ external offsets × labmod.scale (1,3,5,7)', ratio.every((r) => Math.abs(r / ratio[0] - 1) < 0.01), { bars, exp })
  })

  const HOMEBREW_PY = process.env.QA_READER_PY || '/opt/homebrew/opt/python@3.13/libexec/bin/python3'
  fs.mkdirSync(path.join(RUN, 'exports'), { recursive: true })
  const doExport = async (server, tag) => {
    const isStart = (r) => {
      try { const u = new URL(r.url()); return r.request().method() === 'POST' && (u.pathname === '/api/export' || u.pathname === '/api/export/start') } catch { return false }
    }
    const startResp = page.waitForResponse(isStart, { timeout: 120000 })
    startResp.catch(() => {})
    await page.getByRole('button', { name: '导出', exact: true }).first().click()
    const dlg = page.getByRole('dialog').filter({ hasText: '开始导出' })
    await dlg.waitFor({ timeout: 15000 })
    const orig = dlg.getByText('原图尺寸', { exact: true })
    if (await orig.count()) await orig.first().click()
    for (let k = 0; k < 3; k++) {
      for (const f of ['PNG', 'EPS', 'TIFF']) {
        const cb = dlg.getByRole('checkbox', { name: f, exact: true })
        if ((await cb.count()) && (await cb.first().isChecked()) && (await cb.first().isEnabled())) await cb.first().setChecked(false)
      }
      const pdf = dlg.getByRole('checkbox', { name: 'PDF', exact: true })
      if ((await pdf.count()) && !(await pdf.first().isChecked())) await pdf.first().setChecked(true)
      await sleep(300)
    }
    const fmt = {}
    for (const f of ['PDF', 'PNG', 'EPS', 'TIFF']) { const cb = dlg.getByRole('checkbox', { name: f, exact: true }); fmt[f] = (await cb.count()) ? await cb.first().isChecked() : null }
    const checkText = await dlg.locator('section').filter({ hasText: /阻断|警告|建议/ }).last().innerText().catch(() => '')
    const ack = dlg.getByRole('checkbox', { name: /知悉上述/ })
    const needsAck = (await ack.count()) > 0
    if (needsAck) await ack.first().setChecked(true)
    report.exports_gate = report.exports_gate || {}
    report.exports_gate[tag] = { formats: fmt, check_text: checkText.slice(0, 600), acknowledged_blockers: needsAck }
    await dlg.locator('#export-filename').fill(`qa_${tag}`)
    await page.screenshot({ path: path.join(RUN, 'snap', `${tag}-export-dialog.png`) })
    await dlg.getByRole('button', { name: /开始导出/ }).click()
    const resp = await startResp
    let job = await resp.json()
    for (let i = 0; i < 600 && ['pending', 'running'].includes(job.status); i++) {
      await sleep(300)
      job = (await server.api('/api/export/state?job_id=' + encodeURIComponent(job.job_id))).body
    }
    fs.writeFileSync(path.join(RUN, 'exports', `${tag}.job.json`), JSON.stringify(job, null, 1))
    await page.screenshot({ path: path.join(RUN, 'snap', `${tag}-export-done.png`) })
    await page.keyboard.press('Escape')
    await sleep(500)
    if (!(await page.locator('[data-element-svg] svg').count())) {
      report.exports_note = (report.exports_note || []).concat([tag + ': Escape left element edit; re-entered by double-click'])
      await page.locator('[data-object-id]').first().dblclick()
      await page.locator('[data-element-svg] svg').first().waitFor({ timeout: 60000 })
      await sleep(1500)
    }
    const vis = await ensureVisible(page)
    R = vis.R
    if (vis.zoomed_out) report.exports_note = (report.exports_note || []).concat([`${tag}: view zoomed out ${vis.zoomed_out}× to keep the figure inside the stage`])
    const out = (job.outputs || []).find((o) => o.format === 'pdf' && o.status === 'done' && o.url)
    const res = { tag, job, file: null, sha256: null, inspect: null }
    if (!out) return res
    const r = await fetch(`http://127.0.0.1:${PORT}${out.url}`, { headers: server.auth })
    const buf = Buffer.from(await r.arrayBuffer())
    res.http_status = r.status
    res.file = path.join(RUN, 'exports', `${tag}.pdf`)
    fs.writeFileSync(res.file, buf)
    res.sha256 = sha(buf)
    const ij = path.join(RUN, 'exports', `${tag}.inspect.json`)
    execFileSync(HOMEBREW_PY, [path.join(__dirname, 'inspect_export.py'), res.file, ij, '--raster', path.join(RUN, 'exports', `${tag}.png`)], { encoding: 'utf-8' })
    res.inspect = JSON.parse(fs.readFileSync(ij, 'utf-8'))
    report.exports[tag] = { status: job.status, export_dir: job.export_dir, output: { name: out.name, bytes: out.bytes, vector: out.vector, manifest: out.manifest || null }, sha256: res.sha256, raster_sha256: res.inspect.raster_sha256, mediabox_pt: res.inspect.mediabox_pt, document_revision: job.document_revision ?? null }
    return res
  }

  const textOf = (e) => {
    const f = (e.editable || []).find((q) => q.prop === 'text')
    if (f && typeof f.value === 'string') return f.value
    const m = /“(.*)”/.exec(e.label || '')
    return m ? m[1] : null
  }
  const TEXT_ROLES = new Set(['title', 'axis_label', 'text', 'legend_text', 'suptitle'])
  /** manifest 文字元素 ↔ PDF 文字行：按文本配对，同名（跨面板的两个 "Signal"）按 x 次序配对 */
  const pairTexts = (manifest, inspect) => {
    // 旋转的多字符文字（竖排 y 轴标签）pdfminer 按字拆行、配不上：不进配对，记为缺口
    const els = manifest.elements.filter((e) => TEXT_ROLES.has(e.role) && textOf(e) && !(e.bbox[3] * 81.28 > e.bbox[2] * 177.8 && textOf(e).length > 1))
    const byText = {}
    for (const e of els) (byText[textOf(e)] ||= { m: [], p: [] }).m.push(e)
    for (const t of inspect.texts) if (byText[t.text]) byText[t.text].p.push(t)
    const pairs = []
    for (const [txt, g] of Object.entries(byText)) {
      if (g.m.length !== g.p.length) { pairs.push({ text: txt, unmatched: { manifest: g.m.length, pdf: g.p.length } }); continue }
      const ms = [...g.m].sort((a, b) => center(a.bbox)[0] - center(b.bbox)[0])
      const ps = [...g.p].sort((a, b) => a.center_frac_top[0] - b.center_frac_top[0])
      ms.forEach((e, i) => pairs.push({ text: txt, gid: e.gid, m: center(e.bbox), p: ps[i].center_frac_top }))
    }
    return pairs
  }

  const exportChecks = (tag, e, manifest, expectY, expectProv) => {
    check(`${tag}: export job done with a PDF output`, e.job && e.job.status === 'done' && !!e.file, e.job && { status: e.job.status, error: e.job.error, outputs: (e.job.outputs || []).map((o) => [o.format, o.status, o.error]) })
    if (!e.inspect) return
    const [w, h] = e.inspect.mediabox_pt
    check(`${tag}: page box == figure size 504×230.4 pt (±0.01)`, Math.abs(w - 504) < 0.01 && Math.abs(h - 230.4) < 0.01, e.inspect.mediabox_pt)
    const art = report.exports[tag].output.manifest
    report.exports[tag].artifact_verdict = art ? art.status || art.verdict || art : null
    const fits = e.inspect.curves4.map((c) => {
      const ys = c.map((q) => q[1])
      const xs = c.map((q) => q[0])
      const dx = xs.slice(1).map((v, i) => v - xs[i])
      return { uniform: dx.every((d) => Math.abs(d - dx[0]) < 0.01), exp: affineFit(ys, expectY), sigA: affineFit(ys, TRUTH.signal_A), others: [TRUTH.y_correct, TRUTH.y_decoy, TRUTH.y_changed].filter((s) => JSON.stringify(s) !== JSON.stringify(expectY)).map((s) => affineFit(ys, s)) }
    })
    const hit = fits.find((f) => f.uniform && f.exp.maxResidualPt < 0.01 && f.exp.b > 0)
    check(`${tag}: PDF (pdfminer) point series == ${JSON.stringify(expectY)}; no other candidate fits`, !!hit && hit.others.every((o) => o.maxResidualPt > 1 || o.b <= 0), fits.map((f) => ({ uniform: f.uniform, res: f.exp.maxResidualPt, b: f.exp.b })))
    check(`${tag}: PDF twin-axis series == env A signal`, fits.some((f) => f.uniform && f.sigA.maxResidualPt < 0.01 && f.sigA.b > 0))
    check(`${tag}: PDF text layer carries provenance "${expectProv}"`, e.inspect.texts.some((t) => t.text === expectProv), e.inspect.texts.map((t) => t.text).filter((x) => x.startsWith('src')))
    const pairs = pairTexts(manifest, e.inspect)
    report.exports[tag].text_pairs = pairs
    check(`${tag}: every manifest text element found once in PDF text layer`, pairs.every((p) => !p.unmatched), pairs.filter((p) => p.unmatched))
  }

  /** 导出物之间的位移（独立 PDF 读取）== 权威 manifest 之间的位移；单位 mm */
  const displacementChecks = (tag, E0, E1, m0, m1, fontChanged = []) => {
    if (!E0 || !E1 || !E0.inspect || !E1.inspect) return check(`${tag}: both exports inspected`, false)
    const p0 = Object.fromEntries(pairTexts(m0, E0.inspect).filter((p) => p.gid).map((p) => [p.gid, p]))
    const p1 = Object.fromEntries(pairTexts(m1, E1.inspect).filter((p) => p.gid).map((p) => [p.gid, p]))
    const rows = []
    for (const gid of Object.keys(p1)) {
      if (!p0[gid]) continue
      const a0 = byGid(m0)[gid]?.anchor, a1 = byGid(m1)[gid]?.anchor
      // 有锚点的用锚点位移（整段文字刚体平移的量）；注释的 bbox 含箭头，中心不等于文字位移
      const dm = a0 && a1 ? [a1[0] - a0[0], a1[1] - a0[1]] : [p1[gid].m[0] - p0[gid].m[0], p1[gid].m[1] - p0[gid].m[1]]
      const dp = [p1[gid].p[0] - p0[gid].p[0], p1[gid].p[1] - p0[gid].p[1]]
      const errMm = [(dp[0] - dm[0]) * 177.8, fontChanged.includes(gid) ? 0 : (dp[1] - dm[1]) * 81.28]
      rows.push({ gid, measure: a0 && a1 ? 'anchor' : 'bbox_center', manifest_disp_mm: [dm[0] * 177.8, dm[1] * 81.28], pdf_disp_mm: [dp[0] * 177.8, dp[1] * 81.28], err_mm: errMm, vertical_excluded: fontChanged.includes(gid) })
    }
    const maxErr = Math.max(...rows.map((r) => Math.max(Math.abs(r.err_mm[0]), Math.abs(r.err_mm[1]))))
    report.exports[`${tag} displacement`] = { rows, max_err_mm: maxErr }
    check(`${tag}: PDF text displacement == manifest displacement for ${rows.length} texts (≤0.05 mm; candidate 0.01 mm reported)`, rows.length >= 8 && maxErr <= 0.05, { n: rows.length, max_err_mm: maxErr })
  }

  await phase('P4b zero-edit export E0 (calibration baseline)', async () => {
    const e = await doExport(S1, 'E0')
    M.E0 = e
    exportChecks('E0', e, M.s0.manifest, TRUTH.y_correct, TRUTH.expected_provenance_text)
  })

  // ---------------- 编辑
  await phase('P5 single drag (suptitle)', async () => {
    const m0 = M.s0.manifest
    const el = byGid(m0)['fig.texts_0']
    const [cx, cy] = center(el.bbox)
    const p = toScreen(R, cx, cy)
    const d = { x: 40, y: 12 }
    const t = Date.now()
    await page.mouse.move(p.x, p.y)
    await page.mouse.down()
    for (let i = 1; i <= 20; i++) await page.mouse.move(p.x + (d.x * i) / 20, p.y + (d.y * i) / 20)
    const midRenders = rec.renderReqs.filter((r) => r.at >= t).length
    await page.mouse.up()
    check('zero render requests during drag', midRenders === 0, midRenders)
    M.s1 = await snapshot(page, rec, 's1-single-drag', t)
    check('exactly one render after release', rec.renderReqs.filter((r) => r.at >= t).length === 1, rec.renderReqs.filter((r) => r.at >= t).length)
    compareAnchors('s1', m0, M.s1.manifest, { 'fig.texts_0': [el.anchor[0] + d.x / R.w, el.anchor[1] + d.y / R.h] }, R)
  })

  await phase('P5b calibration export Ecal (same RenderCore path, after first edit)', async () => {
    const e = await doExport(S1, 'Ecal')
    M.Ecal = e
    exportChecks('Ecal', e, M.s1.manifest, TRUTH.y_correct, TRUTH.expected_provenance_text)
    report.exports.renderer_note = 'E0（零 override）走原件路径（Type3 字体，与用户终端 matplotlib 原件同形）；带 override 的导出由 RenderCore 重画（Type0/CID）。两种字体的 pdfminer 竖向包围盒度量不同，位移对比只在同一渲染路径之间做（Ecal ↔ E1）。'
  })

  await phase('P6 mixed multi-select drag (G2)', async () => {
    const m1 = M.s1.manifest
    const g = byGid(m1)
    const click = async (fx, fy, shift) => {
      const p = toScreen(R, fx, fy)
      if (shift) await page.keyboard.down('Shift')
      await page.mouse.click(p.x, p.y)
      if (shift) await page.keyboard.up('Shift')
      await sleep(250)
    }
    const targets = ['axes_0.title', 'axes_1.title', 'axes_0.legend', 'axes_0.texts_0', 'fig.texts_1']
    const pts = {
      'axes_0.title': center(g['axes_0.title'].bbox),
      'axes_1.title': center(g['axes_1.title'].bbox),
      'axes_0.legend': [g['axes_0.legend'].bbox[0] + g['axes_0.legend'].bbox[2] * 0.15, g['axes_0.legend'].bbox[1] + g['axes_0.legend'].bbox[3] / 2],
      'axes_0.texts_0': [g['axes_0.texts_0'].anchor[0] + 0.015, g['axes_0.texts_0'].anchor[1] - 0.012],
      'fig.texts_1': center(g['fig.texts_1'].bbox),
    }
    for (const [i, gid] of targets.entries()) await click(...pts[gid], i > 0)
    const sel = await selectedRows(page)
    report.snapshots.multi_selection_rows = sel
    check('element tree shows exactly the 5 intended members selected', targets.every((t) => sel.includes(t)) && sel.length === targets.length, sel)
    const d = { x: 25, y: -18 }
    const start = toScreen(R, ...pts['axes_0.title'])
    const t = Date.now()
    await page.mouse.move(start.x, start.y)
    await page.mouse.down()
    for (let i = 1; i <= 20; i++) await page.mouse.move(start.x + (d.x * i) / 20, start.y + (d.y * i) / 20)
    const mid = rec.renderReqs.filter((r) => r.at >= t).length
    await page.mouse.up()
    check('zero render requests during group drag', mid === 0, mid)
    M.s2 = await snapshot(page, rec, 's2-multi-drag', t)
    check('exactly one render after release', rec.renderReqs.filter((r) => r.at >= t).length === 1, rec.renderReqs.filter((r) => r.at >= t).length)
    const expect = Object.fromEntries(targets.map((gid) => [gid, [g[gid].anchor[0] + d.x / R.w, g[gid].anchor[1] + d.y / R.h]]))
    compareAnchors('s2', m1, M.s2.manifest, expect, R)
    // 组内相对间距守恒
    const A = anchorsOf(M.s2.manifest), A0 = anchorsOf(m1)
    const rel = []
    for (const a of targets) for (const b of targets) if (a < b) rel.push(Math.hypot((A[a][0] - A[b][0]) - (A0[a][0] - A0[b][0]), (A[a][1] - A[b][1]) - (A0[a][1] - A0[b][1])))
    check('pairwise relative offsets preserved (≤1e-3 frac)', Math.max(...rel) < 1e-3, Math.max(...rel))
    // 注释箭头：文字动、被注点不动（跟随合同：箭头尾随文字，xy 不变）
    const arr0 = byGid(m1)['axes_0.texts_0.arrow'].bbox, arr1 = byGid(M.s2.manifest)['axes_0.texts_0.arrow'].bbox
    report.snapshots.annotation_arrow = { before: arr0, after: arr1 }
    // 每个成员恰好一条 override
    const ov = M.s2.doc ? panelOf(M.s2.doc).overrides : []
    const counts = targets.map((gid) => ov.filter((o) => o.gid === gid && /pos_frac|loc_frac/.test(o.prop)).length)
    check('each moved member has exactly one position override', counts.every((c) => c === 1), Object.fromEntries(targets.map((t_, i) => [t_, counts[i]])))
  })

  await phase('P7 group scale of two Axes (se handle)', async () => {
    const m2 = M.s2.manifest
    const g = byGid(m2)
    // 先点空白处收掉多选，再选 axes_0（绘图区右下空白），⇧ 加选 axes_1（柱左上空白）
    const blank = toScreen(R, 0.5, 0.97)
    await page.mouse.click(blank.x, blank.y)
    await sleep(300)
    const a0 = g['axes_0'].bbox, a1 = g['axes_1'].bbox
    const p0 = toScreen(R, a0[0] + a0[2] * 0.93, a0[1] + a0[3] * 0.9)
    const p1 = toScreen(R, a1[0] + a1[2] * 0.08, a1[1] + a1[3] * 0.15)
    await page.mouse.click(p0.x, p0.y)
    await sleep(300)
    await page.keyboard.down('Shift')
    await page.mouse.click(p1.x, p1.y)
    await page.keyboard.up('Shift')
    await sleep(400)
    const sel = await selectedRows(page)
    report.snapshots.group_scale_selection = sel
    check('selection = {axes_0, axes_1}', sel.length === 2 && sel.includes('axes_0') && sel.includes('axes_1'), sel)
    const gx = Math.min(a0[0], a1[0]), gy = Math.min(a0[1], a1[1])
    const gr = Math.max(a0[0] + a0[2], a1[0] + a1[2]), gb = Math.max(a0[1] + a0[3], a1[1] + a1[3])
    const gw = gr - gx, gh = gb - gy
    const h = toScreen(R, gr, gb)
    const d = { x: -60, y: -20 }
    const t = Date.now()
    await page.mouse.move(h.x, h.y)
    await page.mouse.down()
    for (let i = 1; i <= 15; i++) await page.mouse.move(h.x + (d.x * i) / 15, h.y + (d.y * i) / 15)
    await page.mouse.up()
    M.s3 = await snapshot(page, rec, 's3-group-scale', t)
    const sx = (gw + d.x / R.w) / gw, sy = (gh + d.y / R.h) / gh
    const s = Math.abs(sx - 1) >= Math.abs(sy - 1) ? sx : sy
    const exp = (b) => [gx + s * (b[0] - gx), gy + s * (b[1] - gy), s * b[2], s * b[3]]
    const B1 = boxesOf(M.s3.manifest)
    const t_ = tolFrac(R)
    const cmp = ['axes_0', 'axes_1'].map((gid) => {
      const e = exp(gid === 'axes_0' ? a0 : a1)
      const got = B1[gid]
      return { gid, expected: e, got, ok: got.every((v, i) => near(v, e[i], i % 2 ? t_.y : t_.x)) }
    })
    report.snapshots.group_scale = { s, sx, sy, group: [gx, gy, gw, gh], cmp }
    check('Axes boxes = c + s(box − c) about group top-left (≤0.5px)', cmp.every((c) => c.ok), cmp)
    check('twin axes_2 follows axes_0 exactly once (same box)', B1['axes_2'].every((v, i) => Math.abs(v - B1['axes_0'][i]) < 1e-6), { axes_2: B1['axes_2'], axes_0: B1['axes_0'] })
    // 已被显式摆过的文字（pos_frac 绝对位置）与 figure 级文字在缩放中不动（「缩放不带随行元素」）
    const keep = ['fig.texts_0', 'fig.texts_1', 'axes_0.title', 'axes_1.title', 'axes_0.legend', 'axes_0.texts_0']
    const A2 = anchorsOf(m2), A3 = anchorsOf(M.s3.manifest)
    const moved = keep.filter((k) => !(near(A3[k][0], A2[k][0], t_.x) && near(A3[k][1], A2[k][1], t_.y)))
    check('explicitly placed texts keep their absolute anchors through Axes scale', moved.length === 0, moved.map((k) => ({ k, before: A2[k], after: A3[k] })))
  })

  await phase('P8 font size change (axes_0.xlabel → 14)', async () => {
    const m3 = M.s3.manifest
    const g = byGid(m3)
    const blank = toScreen(R, 0.5, 0.97)
    await page.mouse.click(blank.x, blank.y)
    await sleep(300)
    const p = toScreen(R, ...center(g['axes_0.xlabel'].bbox))
    await page.mouse.click(p.x, p.y)
    await sleep(500)
    const field = page.locator('input[data-inspector-prop="fontsize"]').first()
    await field.waitFor({ timeout: 15000 })
    const before = (g['axes_0.xlabel'].editable || []).find((f) => f.prop === 'fontsize')
    const t = Date.now()
    await field.fill('14')
    await field.press('Enter')
    await page.evaluate(() => document.activeElement && document.activeElement.blur())
    M.s4 = await snapshot(page, rec, 's4-fontsize', t)
    const after = (byGid(M.s4.manifest)['axes_0.xlabel'].editable || []).find((f) => f.prop === 'fontsize')
    check('fontsize editable value 14 in authoritative manifest', after && Number(after.value) === 14, { before: before && before.value, after: after && after.value })
    check('xlabel bbox grew (font really larger)', byGid(M.s4.manifest)['axes_0.xlabel'].bbox[3] > g['axes_0.xlabel'].bbox[3] * 1.05)
    compareAnchors('s4', m3, M.s4.manifest, {}, R, { ignore: [] })
  })

  const pickSet = async (gids, m) => {
    const g = byGid(m)
    const blank = toScreen(R, 0.5, 0.97)
    await page.mouse.click(blank.x, blank.y)
    await sleep(300)
    for (const [i, gid] of gids.entries()) {
      const p = toScreen(R, ...center(g[gid].bbox))
      if (i) await page.keyboard.down('Shift')
      await page.mouse.click(p.x, p.y)
      if (i) await page.keyboard.up('Shift')
      await sleep(250)
    }
    return selectedRows(page)
  }

  await phase('P9 align bottom (xlabels + provenance text)', async () => {
    const m4 = M.s4.manifest
    const gids = ['axes_0.xlabel', 'axes_1.xlabel', 'fig.texts_1']
    const sel = await pickSet(gids, m4)
    check('selection = 3 intended texts', gids.every((x) => sel.includes(x)) && sel.length === 3, sel)
    const t = Date.now()
    await page.getByRole('button', { name: '底对齐', exact: true }).first().click()
    M.s5 = await snapshot(page, rec, 's5-align-bottom', t)
    const B0 = boxesOf(m4), B1 = boxesOf(M.s5.manifest)
    const maxB = Math.max(...gids.map((x) => B0[x][1] + B0[x][3]))
    const t_ = tolFrac(R)
    const res = gids.map((x) => ({ gid: x, bottom: B1[x][1] + B1[x][3], dx: B1[x][0] - B0[x][0], h: B1[x][3] - B0[x][3] }))
    check('every member bottom == max bottom of selection (≤0.5px), x unchanged', res.every((r) => near(r.bottom, maxB, t_.y) && Math.abs(r.dx) <= t_.x), { maxB, res })
    const A0 = anchorsOf(m4)
    const others = Object.keys(A0).filter((k) => !gids.includes(k))
    compareAnchors('s5 (non-members)', m4, M.s5.manifest, {}, R, { ignore: gids })
  })

  await phase('P10 distribute horizontally (titles + suptitle)', async () => {
    const m5 = M.s5.manifest
    const gids = ['axes_0.title', 'fig.texts_0', 'axes_1.title']
    const sel = await pickSet(gids, m5)
    check('selection = 3 intended texts', gids.every((x) => sel.includes(x)) && sel.length === 3, sel)
    const t = Date.now()
    await page.getByRole('button', { name: /^水平等距/ }).first().click()
    M.s6 = await snapshot(page, rec, 's6-distribute', t)
    const B0 = boxesOf(m5), B1 = boxesOf(M.s6.manifest)
    const sorted = [...gids].sort((a, b) => B0[a][0] - B0[b][0])
    const L = Math.min(...gids.map((x) => B0[x][0])), Rr = Math.max(...gids.map((x) => B0[x][0] + B0[x][2]))
    const total = gids.reduce((s, x) => s + B0[x][2], 0)
    const gap = (Rr - L - total) / (gids.length - 1)
    let cur = L
    const exp = {}
    for (const x of sorted) { exp[x] = cur; cur += B0[x][2] + gap }
    const t_ = tolFrac(R)
    const res = gids.map((x) => ({ gid: x, expected_left: exp[x], got_left: B1[x][0], err_px: (B1[x][0] - exp[x]) * R.w, dy_px: (B1[x][1] - B0[x][1]) * R.h }))
    check('lefts follow equal-gap model, ends fixed, y unchanged (≤0.5px)', res.every((r) => Math.abs(r.err_px) <= TOL_PX + 0.1 && Math.abs(r.dy_px) <= TOL_PX + 0.1), res)
    compareAnchors('s6 (non-members)', m5, M.s6.manifest, {}, R, { ignore: gids })
  })

  await phase('P11 undo ×2 / redo ×2 with authoritative re-render', async () => {
    const sameGeom = (a, b) => {
      const A = boxesOf(a), B = boxesOf(b)
      return Object.keys(A).filter((k) => !B[k] || A[k].some((v, i) => Math.abs(v - B[k][i]) > 1e-6))
    }
    const panelsResp = await S1.api('/api/panels')
    const panelId = panelsResp.body.panels.find((q) => q.id.endsWith('Fig_flagship.pdf')).id
    /** 按一次键 → 等文档（自动保存 PUT）落到新的 overrides 并安静下来 */
    const press = async (key) => {
      const before = JSON.stringify(panelOf(lastDoc(rec))?.overrides)
      const t = Date.now()
      const r0 = rec.renderReqs.length
      await page.keyboard.press(key)
      for (let i = 0; i < 120; i++) {
        const d = rec.docs.filter((x) => x.at >= t).at(-1)
        if (d && JSON.stringify(panelOf(d.doc)?.overrides) !== before && Date.now() - d.at > 1200) break
        await sleep(250)
      }
      await sleep(1500)
      return { renders: rec.renderReqs.length - r0, doc: lastDoc(rec) }
    }
    /** 独立的权威重渲染：按文档里此刻的 overrides 直接问后端（HTTP），不信前端缓存 */
    const authority = async (doc, name) => {
      const patches = panelOf(doc).overrides
      const r = await S1.api('/api/engine/render', { id: panelId, patches })
      fs.writeFileSync(path.join(RUN, 'snap', `${name}.manifest.json`), JSON.stringify(r.body.manifest, null, 1))
      fs.writeFileSync(path.join(RUN, 'snap', `${name}.document.json`), JSON.stringify(doc, null, 1))
      report.snapshots[name] = { manifest_sha256: sha(JSON.stringify(r.body.manifest, null, 1)), overrides: patches, via: 'HTTP POST /api/engine/render (independent re-render of the document state)' }
      return r.body
    }
    await page.evaluate(() => document.activeElement && document.activeElement.blur())
    const u1 = await press('Meta+z')
    const u2 = await press('Meta+z')
    check('after 2 undos document overrides == s4 overrides (before align)', JSON.stringify(panelOf(u2.doc)?.overrides) === JSON.stringify(panelOf(M.s4.doc)?.overrides), { got: panelOf(u2.doc)?.overrides?.length, want: panelOf(M.s4.doc)?.overrides?.length })
    const a2 = await authority(u2.doc, 's7-undo2')
    let d = sameGeom(a2.manifest, M.s4.manifest)
    check('after 2 undos: authoritative re-render geometry == s4', d.length === 0, d)
    await page.screenshot({ path: path.join(RUN, 'snap', 's7-undo2.png') })
    const r1 = await press('Meta+Shift+z')
    const r2 = await press('Meta+Shift+z')
    check('after 2 redos document overrides == s6 overrides (after distribute)', JSON.stringify(panelOf(r2.doc)?.overrides) === JSON.stringify(panelOf(M.s6.doc)?.overrides))
    const b2 = await authority(r2.doc, 's8-redo2')
    d = sameGeom(b2.manifest, M.s6.manifest)
    check('after 2 redos: authoritative re-render geometry == s6', d.length === 0, d)
    await page.screenshot({ path: path.join(RUN, 'snap', 's8-redo2.png') })
    report.snapshots.undo_redo_frontend_render_requests = [u1.renders, u2.renders, r1.renders, r2.renders]
    M.r2 = { manifest: b2.manifest, svg: b2.svg, doc: r2.doc }
  })

  await phase('P12 save + quit app and worker', async () => {
    // 自动保存落盘：等最后一次 PUT 成功、且界面报「已保存」
    await page.waitForTimeout(3000)
    const lastPut = rec.autosaveStatus.at(-1)
    check('last autosave PUT succeeded', lastPut && lastPut.status === 200, lastPut)
    const auto = path.join(RUN, 'data', 'layouts', '_autosave')
    const files = fs.existsSync(auto) ? fs.readdirSync(auto).filter((n) => n.endsWith('.json')) : []
    report.saved_files = files
    const doc = lastDoc(rec)
    const disk = files.map((n) => JSON.parse(fs.readFileSync(path.join(auto, n), 'utf-8')))
    const match = disk.find((dd) => JSON.stringify(panelOf(dd)?.overrides) === JSON.stringify(panelOf(M.r2.doc)?.overrides))
    check('on-disk autosave document carries the final overrides', !!match, files)
    if (match) fs.writeFileSync(path.join(RUN, 'snap', 'saved-document.json'), JSON.stringify(match, null, 1))
    report.snapshots.saved_document_sha256 = match ? sha(JSON.stringify(match, null, 1)) : null
    await ctx.storageState({ path: path.join(RUN, 'storage-state.json') })
    await page.close()
    await ctx.close()
  })

  await phase('P12b quit app and worker', async () => {
    const code = await S1.stop()
    check('server exited cleanly after /api/shutdown', code === 0 || code === null ? !S1.killed : false, { code, killed: !!S1.killed })
    await sleep(1500)
    const left = processesMentioning(FIX)
    report.side_effects.processes_after_quit = left
    check('no worker / server process left referencing the fixture', left.length === 0, left)
  })

  await phase('P13 reopen (same data/config dirs) + authoritative re-render', async () => {
    S2 = new Server('S2')
    await S2.start()
    ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 }, locale: 'zh-CN', storageState: path.join(RUN, 'storage-state.json') })
    page = await ctx.newPage()
    const rec2 = { renderReqs: [], renders: [], docs: [], exportReqs: [], autosaveStatus: [] }
    Object.assign(rec, { renderReqs: rec2.renderReqs, renders: rec2.renders, docs: rec2.docs, exportReqs: rec2.exportReqs, autosaveStatus: rec2.autosaveStatus })
    wirePage(page, rec)
    const t = Date.now()
    await page.goto(S2.url)
    await page.waitForTimeout(5000)
    await page.screenshot({ path: path.join(RUN, 'snap', 'P13-reopen-landing.png') })
    // 回到上次的文档：画布上应当有这张图；双击进入图内编辑
    let svg = page.locator('[data-element-svg] svg')
    if ((await svg.count()) === 0) {
      const obj = page.locator('[data-object-id]').first()
      await obj.waitFor({ timeout: 60000 })
      await obj.dblclick()
    }
    await page.locator('[data-element-svg] svg').first().waitFor({ timeout: 180000 })
    check('no workdir confirmation asked again (decision remembered)', (await page.locator('[data-workdir-option]').count()) === 0)
    M.o1 = await snapshot(page, rec, 's9-reopened', t, { timeout: 180000, docTimeout: 3000 })
    R = (await ensureVisible(page)).R
    const A = boxesOf(M.o1.manifest), B = boxesOf(M.r2.manifest)
    const diff = Object.keys(B).filter((k) => !A[k] || A[k].some((v, i) => Math.abs(v - B[k][i]) > 1e-6))
    check('reopened geometry == last committed (s8) for every element', diff.length === 0, diff)
    check('reopened render request patches == saved overrides', JSON.stringify(M.o1.req && M.o1.req.patches) === JSON.stringify(panelOf(M.r2.doc)?.overrides), { req: M.o1.req && M.o1.req.patches && M.o1.req.patches.length })
    report.data_evidence.s9 = dataChecks(M.o1.svg, M.o1.manifest, 's9')
    const envst = await S2.api('/api/engine/environment')
    const pyRel = envst.body.project && envst.body.project.python
    const pyAbs = pyRel && (path.isAbsolute(pyRel) ? pyRel : path.join(PAPER, pyRel))
    check('after restart: still project venv A', pyAbs === TRUTH.env_A.executable, pyRel)
    report.env_evidence.environment_after_restart = envst.body
  })

  await phase('P14 re-edit after reopen (drag axes_1.xlabel)', async () => {
    const m = M.o1.manifest
    const el = byGid(m)['axes_1.xlabel']
    const p = toScreen(R, ...center(el.bbox))
    await page.mouse.click(toScreen(R, 0.5, 0.97).x, toScreen(R, 0.5, 0.97).y)
    await sleep(300)
    const d = { x: 30, y: -6 }
    const t = Date.now()
    await page.mouse.move(p.x, p.y)
    await page.mouse.down()
    for (let i = 1; i <= 15; i++) await page.mouse.move(p.x + (d.x * i) / 15, p.y + (d.y * i) / 15)
    await page.mouse.up()
    M.o2 = await snapshot(page, rec, 's10-reedit', t)
    compareAnchors('s10', m, M.o2.manifest, { 'axes_1.xlabel': [el.anchor[0] + d.x / R.w, el.anchor[1] + d.y / R.h] }, R)
  })

  await phase('P15 final export E1 (PDF) + independent reading', async () => {
    const e = await doExport(S2, 'E1')
    M.E1 = e
    exportChecks('E1', e, M.o2.manifest, TRUTH.y_correct, TRUTH.expected_provenance_text)
    displacementChecks('E1 vs Ecal', M.Ecal, e, M.s1.manifest, M.o2.manifest, ['axes_0.xlabel'])
  })

  await phase('P16 branch: continue snapshot after on-disk data change', async () => {
    const dataPath = path.join(PAPER, 'data', 'points.csv')
    const original = fs.readFileSync(dataPath)
    report.snapshot_branch = { original_sha256: sha(original) }
    const changed = 't,y\n' + TRUTH.t.map((t, i) => `${t},${TRUTH.y_changed[i]}\n`).join('')
    fs.writeFileSync(dataPath, changed)
    report.snapshot_branch.changed_sha256 = sha(Buffer.from(changed))
    report.snapshot_branch.changed_at = now()
    await sleep(4000) // 超过 watcher 轮询周期（2 s）+ 防抖，让产品有机会「注意到」
    const m = M.o2.manifest
    const el = byGid(m)['axes_0.ylabel']
    const p = toScreen(R, ...center(el.bbox))
    await page.mouse.click(toScreen(R, 0.5, 0.97).x, toScreen(R, 0.5, 0.97).y)
    await sleep(300)
    const d = { x: -8, y: 0 }
    const t = Date.now()
    await page.mouse.move(p.x, p.y)
    await page.mouse.down()
    for (let i = 1; i <= 10; i++) await page.mouse.move(p.x + (d.x * i) / 10, p.y)
    await page.mouse.up()
    M.k1 = await snapshot(page, rec, 's11-snapshot-edit', t)
    compareAnchors('s11', m, M.k1.manifest, { 'axes_0.ylabel': [el.anchor[0] + d.x / R.w, el.anchor[1]] }, R)
    report.data_evidence.s11 = dataChecks(M.k1.svg, M.k1.manifest, 's11 (snapshot keeps old data)')
    const e = await doExport(S2, 'E2')
    M.E2 = e
    exportChecks('E2', e, M.k1.manifest, TRUTH.y_correct, TRUTH.expected_provenance_text)
    // 产品是否记录 / 显示了「数据已变」：准备接口的 binding_check 与界面上的状态
    const panels = await S2.api('/api/panels')
    const panel = panels.body.panels.find((q) => q.id.endsWith('Fig_flagship.pdf'))
    const prep = await S2.api('/api/engine/preparation', { id: panel.id })
    let st = null
    for (let i = 0; i < 400; i++) {
      st = (await S2.api(`/api/engine/preparation/${prep.body.plan.plan_id}`)).body
      if (['ready', 'error', 'cancelled', 'static_source_available', 'needs_input'].includes(st.result.status)) break
      await sleep(250)
    }
    report.snapshot_branch.preparation_after_change = { status: st.result.status, binding_check: st.result.receipt && st.result.receipt.binding_check, existing_runtime: st.result.existing_runtime }
    report.snapshot_branch.ui_status = await page.evaluate(() => document.querySelector('[data-status-live]')?.textContent?.trim() ?? null)
  })

  await phase('P17 branch: explicit recompute (context menu → 重新构建)', async () => {
    const before = M.k1.manifest
    // 退出图内编辑回到画布（快速编辑条「返回画布」），右键面板对象
    const back = page.getByRole('button', { name: /返回画布/ }).first()
    if (await back.count()) await back.click()
    else await page.keyboard.press('Escape')
    await sleep(1000)
    const obj = page.locator('[data-canvas-stage] [data-object-id]').first()
    await obj.waitFor({ timeout: 15000 })
    const t = Date.now()
    await obj.click({ button: 'right' })
    await sleep(500)
    await page.screenshot({ path: path.join(RUN, 'snap', 'P17-context-menu.png') })
    const item = page.locator('[data-quick-item="rebuild"]').first()
    await item.waitFor({ timeout: 10000 })
    await item.click()
    let status = ''
    for (let i = 0; i < 240; i++) {
      status = await page.evaluate(() => document.querySelector('[data-status-live]')?.textContent?.trim() ?? '')
      if (/重新构建|重新渲染|无法重新构建/.test(status)) break
      await sleep(250)
    }
    report.recompute_branch = { ui_status: status }
    check('UI announces the semantic switch: "已按源脚本重新构建"', /已按源脚本重新构建/.test(status), status)
    await obj.dblclick()
    await page.locator('[data-element-svg] svg').first().waitFor({ timeout: 60000 })
    M.k2 = await snapshot(page, rec, 's12-recomputed', t, { docTimeout: 2000 })
    R = (await ensureVisible(page)).R
    const newProv = 'src ' + sha(fs.readFileSync(path.join(PAPER, 'data', 'points.csv'))).slice(0, 12) + ' env A'
    report.recompute_branch.expected_provenance = newProv
    report.data_evidence.s12 = dataChecks(M.k2.svg, M.k2.manifest, 's12 (recomputed on new data)', TRUTH.y_changed, newProv)
    const A0 = anchorsOf(before), A1 = anchorsOf(M.k2.manifest)
    const ov = (panelOf(M.k1.doc) || { overrides: [] }).overrides.filter((o) => /pos_frac|loc_frac/.test(o.prop)).map((o) => o.gid)
    const t_ = tolFrac(R)
    const drift = ov.filter((g) => !(A1[g] && near(A1[g][0], A0[g][0], t_.x) && near(A1[g][1], A0[g][1], t_.y)))
    check('all position-overridden elements keep their anchors across recompute', drift.length === 0, { overridden: ov, drift })
    const e = await doExport(S2, 'E3')
    M.E3 = e
    exportChecks('E3', e, M.k2.manifest, TRUTH.y_changed, newProv)
    fs.writeFileSync(path.join(PAPER, 'data', 'points.csv'), Buffer.from('t,y\n' + TRUTH.t.map((t_, i) => `${t_},${TRUTH.y_correct[i]}\n`).join('')))
    report.recompute_branch.data_restored_sha256 = sha(fs.readFileSync(path.join(PAPER, 'data', 'points.csv')))
  })

  await phase('P18 side effects (no installs, no out-of-grant writes)', async () => {
    const dp = path.join(PAPER, 'data', 'points.csv')
    if (sha(fs.readFileSync(dp)) !== pre.paper['data/points.csv']) {
      report.side_effects.data_restored_in_P18 = true
      fs.writeFileSync(dp, Buffer.from('t,y\n' + TRUTH.t.map((t_, i) => `${t_},${TRUTH.y_correct[i]}\n`).join('')))
    }
    const post = treeHashes(PAPER)
    const added = Object.keys(post).filter((k) => !(k in pre.paper))
    const removed = Object.keys(pre.paper).filter((k) => !(k in post))
    const changed = Object.keys(post).filter((k) => k in pre.paper && post[k] !== pre.paper[k])
    report.side_effects.paper_tree_diff = { added, removed, changed }
    // 白名单（明列、不静默）：项目注册表草稿 tavotto_registry.json 是 open_project 在「项目里没有注册表」时写的
    // 项目级元数据（app.py 启动打印「未找到注册表，已静态扫描生成草稿」），不是用户数据
    // tavottofile/export/ 与 tavottofile/versions/ 是文档化的项目内收纳目录（docs/rules/backend/layout-versions-and-documents.md）
    const WL = (a) => a === 'tavotto_registry.json' || a.startsWith('tavottofile/export/') || a.startsWith('tavottofile/versions/')
    report.side_effects.whitelisted_additions = added.filter(WL)
    const unexpected = added.filter((a) => !WL(a))
    check('project tree: nothing added/removed/changed beyond the whitelisted registry draft (data file restored)', unexpected.length === 0 && removed.length === 0 && changed.length === 0, { added, removed, changed })
    check('script bytes unchanged', post['scripts/entry.py'] === pre.paper['scripts/entry.py'])
    check('external data unchanged', sha(fs.readFileSync(TRUTH.external_abs)) === pre.external)
    const aSite = sitePackagesListing(path.join(PAPER, '.venv')), bSite = sitePackagesListing(path.join(FIX, 'envB'))
    check('env A site-packages listing unchanged (no installs)', JSON.stringify(aSite) === JSON.stringify(pre.venvA_site), aSite.filter((x) => !pre.venvA_site.includes(x)))
    check('env B site-packages listing unchanged', JSON.stringify(bSite) === JSON.stringify(pre.venvB_site))
    check('env A distributions unchanged', distList(path.join(PAPER, '.venv', 'bin', 'python')) === pre.venvA_dists)
    report.side_effects.export_dirs = [M.E0, M.E1, M.E2, M.E3].filter(Boolean).map((e) => e.job && e.job.export_dir)
  })

  await browser.close().catch(() => {})
  if (S2) await S2.stop()
  report.finished_at = now()
  saveReport()
}

main()
  .catch((e) => {
    console.error(e)
    report.fatal = String(e && e.stack ? e.stack : e)
    saveReport()
  })
  .finally(async () => {
    try { if (browser) await browser.close() } catch {}
    for (const s of [S1, S2]) if (s && s.proc && s.proc.exitCode === null) { try { await s.stop() } catch {} }
    process.exit(0)
  })
