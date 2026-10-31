import { copyFileSync, mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, test } from './fixtures'

/**
 * 真浏览器里跑「描边先于填充内部」（ADR 0086 修订，命中分层）。
 *
 * 为什么值得单独一条 e2e：jsdom 那两套用例喂的是手写 / 冻结的 manifest。**只有真浏览器 + 真
 * matplotlib** 才能回答：引擎真的把 `paint_order` / `fill_alpha` 发到前端了吗？命中层拿到的
 * 是不是同一份？点在 SVG 上真实画出来的线上，选中的是不是这条线？
 *
 * 夹具图（一张图里三个现象）：
 * - `lines_0`：一条曲线，下面垫一片半透明（alpha .15）的 fill_between 幕墙——点线选线，点幕墙空白选幕墙；
 * - `lines_1`：一条水平线，被一块**不透明**矩形（zorder 5）盖住中间一段——盖住的那段点下去选矩形，
 *   没盖住的那段选线；
 * - 若干短网格段（zorder 0.5）贴着曲线——不抢曲线上的点击。
 * 取点全用浏览器自己的 `getPointAtLength` / `getScreenCTM`，不用 manifest 的坐标（那是被测对象）。
 */

function writeProject(): string {
  const dir = path.join(mkdtempSync(path.join(os.tmpdir(), 'tavotto-strokefill-')), 'figures')
  mkdirSync(dir, { recursive: true })
  const script = [
    'import matplotlib',
    'matplotlib.use("Agg")',
    'import matplotlib.pyplot as plt',
    'import numpy as np',
    'from matplotlib.patches import Rectangle',
    'from pathlib import Path',
    '',
    '',
    'def main():',
    '    fig, ax = plt.subplots(figsize=(4, 3))',
    '    x = np.linspace(0, 10, 40)',
    '    y = 6 + 2 * np.sin(x / 2)',
    '    ax.plot(x, y, color="tab:blue", lw=1.8, zorder=6)',
    '    ax.plot([0, 10], [2, 2], color="tab:red", lw=1.8, zorder=3)',
    '    ax.fill_between(x, 0, y, color="tab:blue", alpha=0.15, zorder=2, lw=0)',
    '    ax.add_patch(Rectangle((3, 1), 3, 2, fc="white", ec="none", zorder=5))',
    '    for gx in np.linspace(0.5, 9.5, 12):',
    '        ax.plot([gx, gx + 0.3], [6 + 2 * np.sin(gx / 2) - 0.3] * 2, color="0.6", lw=0.4, ls="--", zorder=0.5)',
    '    ax.set_xlim(0, 10)',
    '    ax.set_ylim(0, 10)',
    '    fig.savefig(Path(__file__).with_name("Fig_strokefill.pdf"))',
    '    plt.close(fig)',
    '',
    '',
    'if __name__ == "__main__":',
    '    main()',
    '',
  ].join('\n')
  writeFileSync(path.join(dir, 'fig_strokefill.py'), script, 'utf-8')
  writeFileSync(
    path.join(dir, 'tavotto_registry.json'),
    JSON.stringify({
      version: 1,
      scripts: { 'fig_strokefill.py': { entry: 'main', cost: 'light', stems: ['Fig_strokefill'] } },
    }),
    'utf-8',
  )
  // 占位 PDF 只负责让卡片出现；画布上的图与命中用的 manifest 都来自引擎当场跑脚本
  copyFileSync(
    path.join(import.meta.dirname, '..', '..', 'examples', 'figures', 'Fig1_kinetics.pdf'),
    path.join(dir, 'Fig_strokefill.pdf'),
  )
  return dir
}

test('描边先于填充内部：半透明幕墙下的线可选，被不透明矩形盖住的线不抢', async ({ app, page }) => {
  const a = await app({ figures: writeProject() })
  await page.goto(a.baseURL)
  await page.getByText('Fig_strokefill.pdf').dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 60_000 })
  await page.waitForTimeout(1500)

  const shownGid = () =>
    page.evaluate(() => document.querySelector('[data-gid]')?.getAttribute('data-gid') ?? null)

  /** 从 SVG 上量：每条线沿线采样（屏幕坐标）、不透明矩形与幕墙的屏幕矩形 */
  const ink = await page.evaluate(() => {
    const svg = document.querySelector('[data-element-svg] svg')
    if (!svg) return null
    const sample = (g: Element, n: number) => {
      const p = g.querySelector('path') as SVGPathElement | null
      if (!p) return []
      const m = p.getScreenCTM()!
      const len = p.getTotalLength()
      return Array.from({ length: n + 1 }, (_, i) => {
        const q = p.getPointAtLength((len * i) / n)
        return { x: q.x * m.a + q.y * m.c + m.e, y: q.x * m.b + q.y * m.d + m.f }
      })
    }
    const rectOf = (g: Element | null) => {
      const r = g?.getBoundingClientRect()
      return r ? { x0: r.x, x1: r.x + r.width, y0: r.y, y1: r.y + r.height } : null
    }
    return {
      curve: sample(svg.querySelector('[id="axes_0.lines_0"]')!, 80),
      flat: sample(svg.querySelector('[id="axes_0.lines_1"]')!, 80),
      cover: rectOf(svg.querySelector('[id="axes_0.patches_0"]')),
      curtain: rectOf(svg.querySelector('[id="axes_0.fill_0"]')),
    }
  })
  expect(ink, 'SVG 里应当有曲线、水平线、矩形与幕墙').not.toBeNull()
  const { curve, flat, cover, curtain } = ink!
  expect(cover && curtain).toBeTruthy()

  // 1) 沿曲线取 12 个点点击：这条线画在半透明幕墙与网格段之上，应当 ≥ 11 次选中它自己
  //    （避开两端——贴着边框命中带的点是在切刻度，不是在验命中）
  const pts = curve.slice(10, 71).filter((_, i) => i % 5 === 0)
  let wins = 0
  for (const p of pts) {
    await page.mouse.click(p.x, p.y)
    await page.waitForTimeout(120)
    if ((await shownGid()) === 'axes_0.lines_0') wins++
  }
  expect(wins, `沿曲线 ${pts.length} 次点击里选中自己的次数`).toBeGreaterThanOrEqual(pts.length - 1)

  // 2) 水平线没被盖住的那段（矩形外）：选线
  const free = flat.find((p) => p.x > cover!.x1 + 20 && p.x < flat.at(-1)!.x - 30)!
  await page.mouse.click(free.x, free.y)
  await page.waitForTimeout(200)
  expect(await shownGid(), '矩形之外的水平线点中就是线').toBe('axes_0.lines_1')

  // 3) 水平线被不透明矩形盖住的那段：看到的是矩形，选中的是矩形（线不抢）
  const hidden = flat.find((p) => p.x > cover!.x0 + 15 && p.x < cover!.x1 - 15)!
  await page.mouse.click(hidden.x, hidden.y)
  await page.waitForTimeout(200)
  expect(await shownGid(), '被不透明矩形盖住的线不该抢走点击').toBe('axes_0.patches_0')

  // 4) 幕墙空白处（曲线下方、远离所有线）：选幕墙，不是子图
  const blank = {
    x: (curtain!.x0 + curtain!.x1) / 2 + 40,
    y: curtain!.y1 - 12,
  }
  // 自证：这一点离曲线、水平线都足够远，且在幕墙矩形之内
  const near = (line: { x: number; y: number }[]) =>
    Math.min(...line.map((q) => Math.hypot(q.x - blank.x, q.y - blank.y)))
  expect(near(curve), '空白点离曲线要足够远').toBeGreaterThan(20)
  expect(near(flat), '空白点离水平线要足够远').toBeGreaterThan(14)
  await page.mouse.click(blank.x, blank.y)
  await page.waitForTimeout(200)
  expect(await shownGid(), '幕墙空白处仍选中幕墙').toBe('axes_0.fill_0')
})
