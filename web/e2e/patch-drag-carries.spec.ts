import { execFileSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

/**
 * 真浏览器 + 真 matplotlib：拖流程图里的框，框里的字与连着框的箭头跟着走
 * （2026-09-24 用户的流程图）。jsdom 那组用例（`canvas/patchCarryDrag.test.tsx`）
 * 喂的是手写 manifest；这里回答的是 jsdom 量不到的几件事：
 *
 * - 引擎真的给纯箭头注释（`annotate("", …)`）发了端点吗；
 * - 按下去真的命中框（而不是框里的字）、拖动途中字的 SVG 组真的跟手、单端跟随的
 *   箭头真的画出了虚线预览吗；
 * - 松手后**引擎重画出来的**字与箭头真的落在新位置（不弹回）吗；
 * - 按住 ⌘ / Ctrl 拖时真的只动框本身吗。
 *
 * 图库在这里现造（与 `element-path-selection.spec.ts` 的 marker 图同一条路：
 * 用 worker 那一侧的解释器跑出真图）。
 */
function flowchartLibrary(): string {
  const dir = path.join(mkdtempSync(path.join(os.tmpdir(), 'tavotto-e2e-flow-')), 'figures')
  mkdirSync(dir)
  writeFileSync(
    path.join(dir, 'fig_flow.py'),
    [
      'import matplotlib',
      'matplotlib.use("Agg")',
      'import matplotlib.pyplot as plt',
      'from matplotlib.patches import FancyBboxPatch',
      '',
      '',
      'def box(ax, x, y, w, h, text):',
      '    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=1.2",',
      '                                facecolor="#E8F2FA", edgecolor="#8BB7DA"))',
      '    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=9)',
      '',
      '',
      'def main():',
      '    fig = plt.figure(figsize=(5.0, 3.0))',
      '    ax = fig.add_axes([0.05, 0.05, 0.9, 0.9])',
      '    ax.set_xlim(0, 100)',
      '    ax.set_ylim(0, 100)',
      '    ax.axis("off")',
      '    box(ax, 6, 35, 30, 30, "A")      # patches_0 + texts_0',
      '    box(ax, 64, 35, 30, 30, "B")     # patches_1 + texts_1',
      '    ax.annotate("", xy=(64, 50), xytext=(36, 50),   # texts_2.arrow',
      '                arrowprops=dict(arrowstyle="-|>", shrinkA=2, shrinkB=2))',
      '    fig.savefig("Fig_flow.pdf")',
      '',
    ].join('\n'),
    'utf-8',
  )
  writeFileSync(
    path.join(dir, 'tavotto_registry.json'),
    JSON.stringify({
      scripts: { 'fig_flow.py': { entry: 'main', cost: 'light', stems: ['Fig_flow'] } },
    }),
    'utf-8',
  )
  const fallback = process.platform === 'win32' ? 'python' : 'python3'
  const py = process.env.TAVOTTO_WORKER_PYTHON || fallback
  execFileSync(py, ['-c', 'import fig_flow; fig_flow.main()'], { cwd: dir, timeout: 120_000 })
  return dir
}

interface El {
  gid: string
  bbox: [number, number, number, number]
  arrow_endpoints?: [number, number][]
}

/** 记住最近一次 `/api/engine/render` 响应里的 manifest（命中层认的就是它） */
function captureManifest(page: Page): () => El[] | null {
  let latest: El[] | null = null
  page.on('response', async (res) => {
    if (!res.url().includes('/api/engine/render')) return
    try {
      const body = (await res.json()) as { manifest?: { elements: El[] } }
      if (body?.manifest) latest = body.manifest.elements
    } catch {
      /* 不是 JSON 就跳过 */
    }
  })
  return () => latest
}

const el = (els: El[], gid: string) => {
  const e = els.find((x) => x.gid === gid)
  if (!e) throw new Error(`manifest 里没有 ${gid}`)
  return e
}

/** 框的屏幕矩形（从 matplotlib SVG 的 DOM 量，与命中层的尺子无关） */
const screenRect = (page: Page, gid: string) =>
  page.evaluate((id) => {
    const g = document.querySelector(`[data-element-svg] svg [id="${id}"]`)
    const r = g?.getBoundingClientRect()
    return r ? { x: r.x, y: r.y, w: r.width, h: r.height } : null
  }, gid)

const transformOf = (page: Page, gid: string) =>
  page.evaluate(
    (id) => document.querySelector(`[data-element-svg] svg [id="${id}"]`)?.getAttribute('transform') ?? null,
    gid,
  )

/** 命中层（= 整张图）在屏幕上的尺寸：manifest 的分数位移靠它换算 */
const layerSize = (page: Page) =>
  page.evaluate(() => {
    const r = document.querySelector('[data-authority]')?.getBoundingClientRect()
    return r ? { w: r.width, h: r.height } : null
  })

async function openFlow(page: Page, baseURL: string) {
  await page.goto(baseURL)
  await page.getByText('Fig_flow.pdf').dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 60_000 })
  await expect(page.locator('[data-authority="ready"]').first()).toBeVisible({ timeout: 60_000 })
  await page.waitForTimeout(1000)
}

/** 等下一次 render 响应落地、权威回到就绪 */
async function settle(page: Page, before: El[] | null, now: () => El[] | null) {
  await expect.poll(() => now() !== before, { timeout: 60_000 }).toBe(true)
  await expect(page.locator('[data-authority="ready"]').first()).toBeVisible({ timeout: 60_000 })
  await page.waitForTimeout(500)
}

test('拖框：框里的字与连着框的箭头那一端跟着走（真渲染落位、不弹回）', async ({ app, page }) => {
  const a = await app({ figures: flowchartLibrary() })
  const manifestOf = captureManifest(page)
  await openFlow(page, a.baseURL)

  const base = manifestOf()
  expect(base, '应当截到 render 响应里的 manifest').not.toBeNull()
  const arrow0 = el(base!, 'axes_0.texts_2.arrow').arrow_endpoints
  expect(arrow0, '纯箭头注释应当带端点（引擎侧 PR A）').toBeTruthy()

  // 按在框 A 里、字的左边（字在框正中，按在字上选中的是字）
  const box = await screenRect(page, 'axes_0.patches_0')
  expect(box).not.toBeNull()
  const from = { x: box!.x + box!.w * 0.15, y: box!.y + box!.h * 0.5 }
  const DY = 60
  await page.mouse.move(from.x, from.y)
  await page.mouse.down()
  for (let i = 1; i <= 12; i++) await page.mouse.move(from.x, from.y + (DY * i) / 12)
  await page.waitForTimeout(200)

  // 拖动途中：字的 SVG 组跟手，单端跟随的箭头画虚线；框 B 与它的字不动
  // （按住修饰键时预览归位写的是 translate(0,0)，所以要量出真有位移）
  const tf = (await transformOf(page, 'axes_0.texts_0')) ?? ''
  const ty = Number(/^translate\([^,]+,([^)]+)\)/.exec(tf)?.[1] ?? 0)
  expect(ty, `字的 SVG 组应当跟手下移（transform=${tf}）`).toBeGreaterThan(5)
  expect(await transformOf(page, 'axes_0.texts_1')).toBeNull()
  await expect(page.locator('[data-carried-arrow="axes_0.texts_2.arrow"]')).toHaveCount(1)

  const before = manifestOf()
  await page.mouse.up()
  await settle(page, before, manifestOf)

  const now = manifestOf()!
  const layer = (await layerSize(page))!
  const dfy = DY / layer.h
  const moved = (gid: string) => el(now, gid).bbox[1] - el(base!, gid).bbox[1]
  expect(moved('axes_0.patches_0')).toBeCloseTo(dfy, 2)
  expect(moved('axes_0.texts_0'), '框里的字应当跟着框走').toBeCloseTo(dfy, 2)
  expect(Math.abs(moved('axes_0.texts_1')), '别的框里的字不动').toBeLessThan(1e-3)
  expect(Math.abs(moved('axes_0.patches_1'))).toBeLessThan(1e-3)
  const [tail, head] = el(now, 'axes_0.texts_2.arrow').arrow_endpoints!
  expect(tail[1] - arrow0![0][1], '箭尾在框 A 上：跟着走').toBeCloseTo(dfy, 2)
  expect(Math.abs(head[1] - arrow0![1][1]), '箭头在框 B 上：不动').toBeLessThan(1e-3)
  // 引擎重画出来的箭头（patch 的 bbox）也在新位置——改的若是 patch 下一帧就弹回
  expect(el(now, 'axes_0.texts_2.arrow').bbox[3]).toBeGreaterThan(el(base!, 'axes_0.texts_2.arrow').bbox[3])
})

test('按住 ⌘ / Ctrl 拖框：只动框本身', async ({ app, page }) => {
  const a = await app({ figures: flowchartLibrary() })
  const manifestOf = captureManifest(page)
  await openFlow(page, a.baseURL)
  const base = manifestOf()!

  const box = await screenRect(page, 'axes_0.patches_1')
  const from = { x: box!.x + box!.w * 0.85, y: box!.y + box!.h * 0.5 }
  const mod = process.platform === 'darwin' ? 'Meta' : 'Control'
  await page.mouse.move(from.x, from.y)
  await page.mouse.down()
  await page.keyboard.down(mod)
  for (let i = 1; i <= 12; i++) await page.mouse.move(from.x, from.y - (50 * i) / 12)
  await page.waitForTimeout(200)
  await expect(page.locator('[data-carried-arrow]')).toHaveCount(0)
  const before = manifestOf()
  await page.mouse.up()
  await page.keyboard.up(mod)
  await settle(page, before, manifestOf)

  const now = manifestOf()!
  const moved = (gid: string) => el(now, gid).bbox[1] - el(base, gid).bbox[1]
  expect(moved('axes_0.patches_1')).toBeLessThan(-0.05)
  expect(Math.abs(moved('axes_0.texts_1')), '按住修饰键：框里的字留在原地').toBeLessThan(1e-3)
  const head0 = el(base, 'axes_0.texts_2.arrow').arrow_endpoints![1]
  const head1 = el(now, 'axes_0.texts_2.arrow').arrow_endpoints![1]
  expect(Math.abs(head1[1] - head0[1])).toBeLessThan(1e-3)
})

test('直接拖纯箭头注释：整根平移，引擎重画后不弹回', async ({ app, page }) => {
  const a = await app({ figures: flowchartLibrary() })
  const manifestOf = captureManifest(page)
  await openFlow(page, a.baseURL)
  const base = manifestOf()!

  // 按在箭头杆的正中（引擎发的端点 → 屏幕坐标，与 SVG 上量的箭头组中心应当吻合）
  const r = (await screenRect(page, 'axes_0.texts_2.arrow'))!
  const mid = { x: r.x + r.w / 2, y: r.y + r.h / 2 }
  const DY = -40
  await page.mouse.move(mid.x, mid.y)
  await page.mouse.down()
  for (let i = 1; i <= 12; i++) await page.mouse.move(mid.x, mid.y + (DY * i) / 12)
  const before = manifestOf()
  await page.mouse.up()
  await settle(page, before, manifestOf)

  const now = manifestOf()!
  const dfy = DY / (await layerSize(page))!.h
  const [t0, h0] = el(base, 'axes_0.texts_2.arrow').arrow_endpoints!
  const [t1, h1] = el(now, 'axes_0.texts_2.arrow').arrow_endpoints!
  expect(t1[1] - t0[1]).toBeCloseTo(dfy, 2)
  expect(h1[1] - h0[1]).toBeCloseTo(dfy, 2)
  // 画出来的那支（patch 的 bbox，draw 时按注释锚点重定位）也上移了——没弹回
  const b0 = el(base, 'axes_0.texts_2.arrow').bbox
  const b1 = el(now, 'axes_0.texts_2.arrow').bbox
  expect(b1[1] - b0[1]).toBeCloseTo(dfy, 2)
  // 框不受影响
  expect(Math.abs(el(now, 'axes_0.patches_0').bbox[1] - el(base, 'axes_0.patches_0').bbox[1])).toBeLessThan(1e-3)
})
