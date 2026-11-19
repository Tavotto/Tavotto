import type { Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, test } from './fixtures'

/**
 * 引线逐段拖的浏览器腿（ADR 0100 修订 2026-11-19）：**真 PanelView、真指针、真 matplotlib**。
 *
 * 用户来图的写法：`Line2D(transform=ax.transAxes)` 的直角引线（震中 → 两个拐点 → 标签）+ 白色描边。
 * 选中后每个顶点出一个方块手柄；按在一段上拖 = 那一段两端同走，相邻段伸缩。
 *
 * 独立尺：引线的 SVG 组（matplotlib 输出的 gid）在屏幕上的外框。拖第 0 段（从震中出发的那段横线，
 * 整条线最低的一段）往下 DY 像素：松手成图之后外框的下沿往下走 DY、上沿 / 左右不动；撤销回原处；
 * 保存重开 == 热态。另截选中、拖动中、落下三张图（浅 / 深各一套）给审阅页。
 */
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    try {
      const key = 'tavotto.ui'
      const saved = JSON.parse(localStorage.getItem(key) || '{}')
      localStorage.setItem(key, JSON.stringify({ ...saved, prefsVersion: 2, snapEnabled: false }))
    } catch {
      /* 存储不可用时照常跑 */
    }
  })
})

const DY = 18
const BUDGET_PX = 0.75
const GID = 'axes_0.lines_0'

function library(): string {
  const dir = path.join(mkdtempSync(path.join(os.tmpdir(), 'tavotto-e2e-lineseg-')), 'figures')
  mkdirSync(dir)
  writeFileSync(
    path.join(dir, 'fig_lead.py'),
    [
      'import matplotlib',
      'matplotlib.use("Agg")',
      'import matplotlib.patheffects as pe',
      'import matplotlib.pyplot as plt',
      'from matplotlib.lines import Line2D',
      '',
      '',
      'def main():',
      '    fig, ax = plt.subplots(figsize=(14 / 2.54, 8 / 2.54))',
      '    ax.set_xlim(0, 10)',
      '    ax.set_ylim(0, 10)',
      '    ax.scatter([2], [3], s=40, c="#C51E30", zorder=5)',
      '    pts = [(0.2, 0.3), (0.5, 0.3), (0.5, 0.75), (0.72, 0.75)]',
      '    ln = Line2D([p[0] for p in pts], [p[1] for p in pts], transform=ax.transAxes,',
      '                color="#C51E30", lw=1.35, linestyle=(0, (3.0, 1.8)))',
      '    ln.set_path_effects([pe.Stroke(linewidth=2.3, foreground="white"), pe.Normal()])',
      '    ax.add_line(ln)',
      '    ax.text(0.73, 0.75, "Kobe", transform=ax.transAxes, va="center", color="#C51E30")',
      '    fig.savefig("Fig_lead.pdf")',
      '',
    ].join('\n'),
    'utf-8',
  )
  writeFileSync(
    path.join(dir, 'tavotto_registry.json'),
    JSON.stringify({ scripts: { 'fig_lead.py': { entry: 'main', cost: 'light', stems: ['Fig_lead'] } } }),
    'utf-8',
  )
  const py = process.env.TAVOTTO_WORKER_PYTHON || (process.platform === 'win32' ? 'python' : 'python3')
  execFileSync(py, ['-c', 'import fig_lead; fig_lead.main()'], { cwd: dir, timeout: 120_000 })
  return dir
}

type Box = { x: number; y: number; w: number; h: number }
const boxOf = (page: Page, gid: string): Promise<Box | null> =>
  page.evaluate((id) => {
    const n = document.querySelector(`[data-element-svg] [id="${id}"]`)
    if (!n) return null
    const r = (n as SVGGraphicsElement).getBoundingClientRect()
    return { x: r.x, y: r.y, w: r.width, h: r.height }
  }, gid)

async function settleAfter(page: Page, action: () => Promise<void>) {
  const rendered = page.waitForResponse((r) => r.url().includes('/api/engine/render'), { timeout: 60_000 })
  await action()
  await rendered
  await page.waitForTimeout(600)
}

for (const scheme of ['light', 'dark'] as const) {
  test(`引线：选中出顶点手柄，按在一段上拖 = 那一段走、相邻段伸缩；撤销回原处、重做回落点（${scheme}）`, async ({
    app,
    page,
  }, testInfo) => {
    await page.emulateMedia({ colorScheme: scheme })
    const a = await app({ figures: library() })
    await page.goto(a.baseURL)
    await page.locator('[data-card="Fig_lead.pdf"]').focus({ timeout: 30_000 })
    await page.keyboard.press('Shift+Enter')
    await expect(page.locator('[data-object-id]')).toHaveCount(1, { timeout: 30_000 })
    await page.waitForTimeout(1500)
    const p = (await page.locator('[data-object-id]').boundingBox())!
    await page.mouse.click(p.x + p.width / 2, p.y + p.height / 2)
    await page.keyboard.press('Enter')
    await expect(page.locator('[data-element-svg] > svg')).toBeVisible({ timeout: 60_000 })
    await page.waitForTimeout(2000)

    const shot = async (name: string) => {
      const file = testInfo.outputPath(`${name}-${scheme}.png`)
      await page.locator('[data-element-svg]').screenshot({ path: file })
      await testInfo.attach(`${name}-${scheme}`, { path: file, contentType: 'image/png' })
    }

    const b0 = (await boxOf(page, GID))!
    expect(b0, 'SVG 里应当有引线的组').not.toBeNull()
    // 第 0 段：从震中出发的横线，在线框最下沿；按在它的中间
    const fig = (await page.locator('[data-element-svg] > svg').boundingBox())!
    const sx = b0.x + b0.w * 0.25
    const sy = b0.y + b0.h
    // 先点一下选中（点一下不拖 = 只选中），看手柄
    await page.mouse.click(sx, sy)
    await page.waitForTimeout(400)
    await expect(page.locator('[data-line-vertex]')).toHaveCount(4)
    await shot('lineseg-selected')

    await page.mouse.move(sx, sy)
    await page.mouse.down()
    for (let i = 1; i <= 8; i++) await page.mouse.move(sx, sy + (DY * i) / 8)
    await page.waitForTimeout(200)
    await expect(page.locator('[data-line-preview]')).toHaveCount(1)
    await shot('lineseg-dragging')
    await settleAfter(page, () => page.mouse.up())
    await shot('lineseg-dropped')

    const b1 = (await boxOf(page, GID))!
    const top = b1.y - b0.y
    const bottom = b1.y + b1.h - (b0.y + b0.h)
    console.log(
      `[line-seg e2e ${scheme}] 上沿 Δ=${top.toFixed(2)} 下沿 Δ=${bottom.toFixed(2)} 左 Δ=${(b1.x - b0.x).toFixed(2)} 宽 Δ=${(b1.w - b0.w).toFixed(2)} px（图宽 ${fig.width.toFixed(0)}）`,
    )
    expect(Math.abs(bottom - DY), '拖的那一段走了 DY').toBeLessThanOrEqual(BUDGET_PX)
    expect(Math.abs(top), '别的段的上沿不动').toBeLessThanOrEqual(BUDGET_PX)
    expect(Math.abs(b1.x - b0.x), '左沿不动').toBeLessThanOrEqual(BUDGET_PX)
    expect(Math.abs(b1.w - b0.w), '宽度不变').toBeLessThanOrEqual(BUDGET_PX)

    await page.keyboard.press('Control+z')
    await expect
      .poll(
        async () => {
          const b = (await boxOf(page, GID))!
          return Math.max(Math.abs(b.y - b0.y), Math.abs(b.h - b0.h))
        },
        { timeout: 60_000, message: '撤销一次回原处' },
      )
      .toBeLessThanOrEqual(BUDGET_PX)

    // 重做一次回到落下的位置
    await page.keyboard.press('Control+Shift+z')
    await expect
      .poll(
        async () => {
          const b = (await boxOf(page, GID))!
          return Math.max(Math.abs(b.y - b1.y), Math.abs(b.h - b1.h))
        },
        { timeout: 60_000, message: '重做一次回到落下的位置' },
      )
      .toBeLessThanOrEqual(BUDGET_PX)
  })
}
