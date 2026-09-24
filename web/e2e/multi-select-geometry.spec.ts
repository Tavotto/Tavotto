import { expect, openElementsTab, test } from './fixtures'
import { copyFileSync, mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import type { Page } from '@playwright/test'

/**
 * 图内**混合多选**整组拖动的几何闭环（QA 2026-09-24 flagship 节 / ACCEPT-1 的浏览器代表）。
 *
 * 判据的主语：真 matplotlib 画出来、**权威渲染回来的 manifest** 里每个成员的锚点——不是预览 DOM，
 * 也不是前端自己算出来要写的 override。期望值由**测试侧独立的坐标尺**给出：图内分数坐标
 * （top-origin）↔ 屏幕 CSS px 取自 `[data-element-svg] svg` 根节点的 getBoundingClientRect
 * （SVG viewBox 就是整张 figure；先验长宽比一致），拖动 Δs 的期望 = anchor + Δs / (宽, 高)。
 * 不调用任何生产坐标换算函数。
 *
 * 选区跨两个子图、含同名标题（两个都叫 "Signal"）、图例、注释文字与 figure 级文字——
 * 每个可动成员恰好动一次、组内相对间距不变、没选中的元素一个都不动；撤销 / 重做后画面上的
 * 元素回到 / 回到拖后的位置（量的是 SVG 里那个 gid 节点的屏幕框，不是 store）。
 */

const SCRIPT = [
  'import matplotlib',
  'matplotlib.use("Agg")',
  'import matplotlib.pyplot as plt',
  'from pathlib import Path',
  '',
  '',
  'def main():',
  '    fig, (ax1, ax3) = plt.subplots(1, 2, figsize=(7.0, 3.2))',
  '    ax1.plot([0, 1, 2, 3], [0, 9, 2, 10], "o-", label="points")',
  '    ax1.set_title("Signal")',
  '    ax1.set_xlabel("t / s")',
  '    ax1.annotate("peak", xy=(1, 9), xytext=(2.2, 6.0), arrowprops=dict(arrowstyle="->"))',
  '    ax1.legend(loc="upper left")',
  '    ax3.bar([0, 1, 2, 3], [1, 3, 5, 7], color="tab:gray")',
  '    ax3.set_title("Signal")',
  '    ax3.set_xlabel("t / s")',
  '    fig.suptitle("QA geometry")',
  '    fig.text(0.02, 0.03, "note")',
  '    fig.subplots_adjust(left=0.08, right=0.97, bottom=0.18, top=0.82, wspace=0.45)',
  '    fig.savefig(Path(__file__).with_name("Fig_geo.pdf"))',
  '    plt.close(fig)',
  '',
  '',
  'if __name__ == "__main__":',
  '    main()',
  '',
].join('\n')

function writeProject(): string {
  const dir = path.join(mkdtempSync(path.join(os.tmpdir(), 'tavotto-geo-')), 'figures')
  mkdirSync(dir, { recursive: true })
  writeFileSync(path.join(dir, 'fig_geo.py'), SCRIPT, 'utf-8')
  writeFileSync(
    path.join(dir, 'tavotto_registry.json'),
    JSON.stringify({
      version: 1,
      scripts: { 'fig_geo.py': { entry: 'main', cost: 'light', stems: ['Fig_geo'] } },
    }),
    'utf-8',
  )
  // 素材卡片占位（与 twin-axes-pick 同一招）：画布与 manifest 都来自引擎当场跑脚本
  copyFileSync(
    path.join(import.meta.dirname, '..', '..', 'examples', 'figures', 'Fig1_kinetics.pdf'),
    path.join(dir, 'Fig_geo.pdf'),
  )
  return dir
}

interface ManifestEl {
  gid: string
  anchor?: [number, number]
  bbox: [number, number, number, number]
}
interface Rendered {
  at: number
  manifest: { elements: ManifestEl[] }
}

/** 独立坐标尺：SVG 根节点在屏幕上的框 */
async function svgRect(page: Page) {
  const r = await page.evaluate(() => {
    const svg = document.querySelector('[data-element-svg] svg')
    if (!svg) return null
    const b = svg.getBoundingClientRect()
    const vb = (svg.getAttribute('viewBox') ?? '').split(/\s+/).map(Number)
    return { x: b.left, y: b.top, w: b.width, h: b.height, vb }
  })
  expect(r, '画布上应当有图内 SVG').not.toBeNull()
  // 尺子自检：viewBox 长宽比 == 屏幕框长宽比（否则 SVG 不是等比铺满，分数坐标就不成立）
  expect(Math.abs(r!.vb[2] / r!.vb[3] / (r!.w / r!.h) - 1)).toBeLessThan(0.005)
  return r!
}

/** SVG 里某个 gid 节点此刻的屏幕框中心（用户看见的位置） */
const nodeCenter = (page: Page, gid: string) =>
  page.evaluate((id) => {
    const n = document.querySelector(`[data-element-svg] svg [id="${id}"]`)
    if (!n) return null
    const r = (n as SVGGraphicsElement).getBoundingClientRect()
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 }
  }, gid)

test('图内混合多选整组拖动：锚点 = 独立模型，撤销 / 重做回到对应位置', async ({ app, page }) => {
  const a = await app({ figures: writeProject() })
  const rendered: Rendered[] = []
  const renderReqs: number[] = []
  page.on('request', (req) => {
    if (req.url().includes('/api/engine/render')) renderReqs.push(Date.now())
  })
  page.on('response', async (res) => {
    if (!res.url().includes('/api/engine/render') || res.status() !== 200) return
    try {
      const body = await res.json()
      if (body?.manifest) rendered.push({ at: Date.now(), manifest: body.manifest })
    } catch {
      /* 非 JSON：不是一次成功渲染 */
    }
  })

  await page.goto(a.baseURL)
  await page.getByText('Fig_geo.pdf').dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 60_000 })
  await expect.poll(() => rendered.length, { timeout: 60_000 }).toBeGreaterThan(0)
  await expect(page.locator('[data-authority="ready"]').first()).toBeVisible({ timeout: 60_000 })
  await page.waitForTimeout(1500)

  // 选区证据要读元素树：幂等地打开左栏「图内元素」（toggle 按钮，见 fixtures.openElementsTab）
  await openElementsTab(page)
  await page.waitForTimeout(500)
  const before = rendered.at(-1)!.manifest
  const g = Object.fromEntries(before.elements.map((e) => [e.gid, e]))
  const R = await svgRect(page)
  const at = (fx: number, fy: number) => ({ x: R.x + fx * R.w, y: R.y + fy * R.h })
  const mid = (b: number[]) => [b[0] + b[2] / 2, b[1] + b[3] / 2] as const

  const members = ['axes_0.title', 'axes_1.title', 'axes_0.legend', 'axes_0.texts_0', 'fig.texts_1']
  for (const m of members) expect(g[m]?.anchor, `${m} 应当是带锚点的可拖元素`).toBeTruthy()
  const clickAt: Record<string, readonly [number, number]> = {
    'axes_0.title': mid(g['axes_0.title'].bbox),
    'axes_1.title': mid(g['axes_1.title'].bbox),
    // 图例框的左侧（图例句柄处）：中间是图例项文字，点中的是它而不是图例
    'axes_0.legend': [
      g['axes_0.legend'].bbox[0] + g['axes_0.legend'].bbox[2] * 0.15,
      g['axes_0.legend'].bbox[1] + g['axes_0.legend'].bbox[3] / 2,
    ],
    // 注释的 bbox 含箭头；点文字本身（锚点在文字左下基线处）
    'axes_0.texts_0': [g['axes_0.texts_0'].anchor![0] + 0.015, g['axes_0.texts_0'].anchor![1] - 0.012],
    'fig.texts_1': mid(g['fig.texts_1'].bbox),
  }
  for (const [i, m] of members.entries()) {
    const p = at(...clickAt[m])
    if (i) await page.keyboard.down('Shift')
    await page.mouse.click(p.x, p.y)
    if (i) await page.keyboard.up('Shift')
    await page.waitForTimeout(250)
  }
  // 选区的证据取元素树（稳定锚点 data-el + aria-selected），不取属性页文案
  const selected = await page.evaluate(() =>
    [...document.querySelectorAll('[data-el][aria-selected="true"]')].map((n) => n.getAttribute('data-el')),
  )
  expect(new Set(selected), '五个成员应当恰好都在选区里').toEqual(new Set(members))

  const shown0 = Object.fromEntries(
    await Promise.all(members.map(async (m) => [m, await nodeCenter(page, m)] as const)),
  )

  // 拖第一个成员：整组平移
  const d = { x: 30, y: -15 }
  const start = at(...clickAt['axes_0.title'])
  const t0 = Date.now()
  await page.mouse.move(start.x, start.y)
  await page.mouse.down()
  for (let i = 1; i <= 15; i++) await page.mouse.move(start.x + (d.x * i) / 15, start.y + (d.y * i) / 15)
  expect(renderReqs.filter((t) => t >= t0), '拖动途中不发后端渲染').toHaveLength(0)
  await page.mouse.up()
  await expect
    .poll(() => rendered.filter((r) => r.at >= t0).length, { timeout: 60_000, message: '松手后应当有一次权威渲染' })
    .toBeGreaterThan(0)
  await page.waitForTimeout(1000)
  expect(renderReqs.filter((t) => t >= t0), '一次松手只发一次定稿渲染').toHaveLength(1)
  const after = rendered.at(-1)!.manifest
  const A = Object.fromEntries(after.elements.map((e) => [e.gid, e]))

  // 容差：0.5 CSS px（换成分数）+ override 的 round4 量化
  const tol = { x: 0.5 / R.w + 1e-4, y: 0.5 / R.h + 1e-4 }
  for (const m of members) {
    const want = [g[m].anchor![0] + d.x / R.w, g[m].anchor![1] + d.y / R.h]
    const got = A[m].anchor!
    expect(Math.abs(got[0] - want[0]), `${m} x 锚点（px 误差 ${(got[0] - want[0]) * R.w}）`).toBeLessThanOrEqual(tol.x)
    expect(Math.abs(got[1] - want[1]), `${m} y 锚点（px 误差 ${(got[1] - want[1]) * R.h}）`).toBeLessThanOrEqual(tol.y)
  }
  // 没选中的可拖元素一个都不动（含同一子图里的 xlabel、另一张子图的 xlabel、suptitle）
  for (const e of before.elements) {
    if (!e.anchor || members.includes(e.gid)) continue
    const got = A[e.gid]?.anchor
    expect(got, `${e.gid} 应当还在`).toBeTruthy()
    expect(Math.abs(got![0] - e.anchor[0]), `${e.gid} 不该动（x）`).toBeLessThanOrEqual(tol.x)
    expect(Math.abs(got![1] - e.anchor[1]), `${e.gid} 不该动（y）`).toBeLessThanOrEqual(tol.y)
  }

  // 撤销：画面上每个成员回到拖前的位置；重做：回到拖后的位置（量 SVG 节点的屏幕框）
  const shown1 = Object.fromEntries(
    await Promise.all(members.map(async (m) => [m, await nodeCenter(page, m)] as const)),
  )
  const near = (p: { x: number; y: number } | null, q: { x: number; y: number } | null) =>
    !!p && !!q && Math.abs(p.x - q.x) <= 0.75 && Math.abs(p.y - q.y) <= 0.75
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
  await page.keyboard.press('ControlOrMeta+z')
  for (const m of members) {
    await expect
      .poll(async () => near(await nodeCenter(page, m), shown0[m]), { timeout: 30_000, message: `撤销后 ${m} 回到原位` })
      .toBe(true)
  }
  await page.keyboard.press('ControlOrMeta+Shift+z')
  for (const m of members) {
    await expect
      .poll(async () => near(await nodeCenter(page, m), shown1[m]), { timeout: 30_000, message: `重做后 ${m} 回到拖后位置` })
      .toBe(true)
  }
})
