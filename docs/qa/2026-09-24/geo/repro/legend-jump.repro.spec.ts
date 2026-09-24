import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

// QA 2026-09-24 GEO 复现：复制到 web/e2e/ 下运行；暴露已知缺陷时是红的，不进测试集。

/**
 * 几何参考尺的浏览器腿（QA 2026-09-24，GEO-01 / GEO-02）：**真 PanelView、真指针、真后端**。
 *
 * jsdom 那一半（`src/canvas/geometryReference.test.ts`）把 layout 当输入喂给拖动入口，
 * 量不到 PanelView 自己算的 layout、视图倍率与 DPR；这里补上那一段。
 *
 * 独立尺：屏幕上拖 Δs 个 CSS 像素，**权威重渲染之后**元素的墨迹框在视口里也应当平移 Δs——
 * T(T⁻¹(T(p)+Δs)) = T(p)+Δs，与视图倍率、面板位置、图幅都无关，所以这里不需要（也不调用）
 * 任何生产坐标换算；视图倍率只是被测条件（Ctrl+= / Ctrl+- 两档，外加 DPR 2）。
 *
 * 主语：`[data-element-svg] [id="<gid>"]` 这个节点（matplotlib 输出的 gid，稳定锚点，不认文案），
 * 时刻：拖动之前 vs **松手后权威 SVG 换上画布之后**（预览计时环多出一条 = 权威到了）。
 *
 * 预算：0.5 CSS px（规范 §0.2 的候选值）。先校准：本机 chromium 实测偏差打进日志，
 * 远小于预算才把它当门槛；超出就报失败，不放宽。
 */

const BUDGET_PX = 0.5

type Box = { x: number; y: number; w: number; h: number }

async function boxOf(page: Page, gid: string): Promise<Box | null> {
  return page.evaluate((id) => {
    const n = document.querySelector(`[data-element-svg] [id="${id}"]`)
    if (!n) return null
    const r = (n as SVGGraphicsElement).getBoundingClientRect()
    return { x: r.x, y: r.y, w: r.width, h: r.height }
  }, gid)
}

const timingsCount = (page: Page) =>
  page.evaluate(() => {
    const w = window as unknown as { __MM_PREVIEW_TIMINGS__?: unknown[] }
    return w.__MM_PREVIEW_TIMINGS__?.length ?? 0
  })

async function openFigure(page: Page, baseURL: string) {
  await page.goto(baseURL)
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 60_000 })
  // 首次渲染安顿下来
  await page.waitForTimeout(1500)
}

/** 真指针拖 Δs：一步步走（每步一次 pointermove），松手后等权威 SVG 换上来 */
async function dragAndSettle(page: Page, from: { x: number; y: number }, ds: [number, number]) {
  const before = await timingsCount(page)
  await page.mouse.move(from.x, from.y)
  await page.mouse.down()
  for (let i = 1; i <= 20; i++) {
    await page.mouse.move(from.x + (ds[0] * i) / 20, from.y + (ds[1] * i) / 20)
  }
  await page.mouse.up()
  await expect
    .poll(() => timingsCount(page), { timeout: 60_000, message: '权威 SVG 应当换上画布' })
    .toBeGreaterThan(before)
  await page.waitForTimeout(300)
}

async function zoomTo(page: Page, key: 'Control+=' | 'Control+-' | null) {
  if (!key) return
  const readout = page.locator('[data-zoom-readout]').first()
  const was = (await readout.textContent()) ?? ''
  await page.keyboard.press(key)
  await expect.poll(async () => (await readout.textContent()) ?? '').not.toBe(was)
  await page.waitForTimeout(600) // 视口动画走完
}

const center = (b: Box) => ({ x: b.x + b.w / 2, y: b.y + b.h / 2 })

test('图例整组 / 单独平移：权威重渲染后图例墨迹框应平移 Δs（已知缺陷：多走约 1 pt）', async ({ app, page }) => {
  const a = await app()
  await openFigure(page, a.baseURL)
  await zoomTo(page, 'Control+=')
  const l0 = (await boxOf(page, 'axes_0.legend'))!
  // 点图例框的内角而不是中心：中心落在图例条目文字（legend_text）上
  const ds: [number, number] = [0, 19] // 纯竖直拖：x 分量应当一点不变
  await dragAndSettle(page, { x: l0.x + 3, y: l0.y + 3 }, ds)
  const l1 = (await boxOf(page, 'axes_0.legend'))!
  const err = [l1.x - l0.x - ds[0], l1.y - l0.y - ds[1]]
  console.log(`[GEO legend repro] 误差 (${err.map((v) => v.toFixed(3))}) CSS px`)
  expect(Math.abs(err[0])).toBeLessThanOrEqual(BUDGET_PX)
  expect(Math.abs(err[1])).toBeLessThanOrEqual(BUDGET_PX)
})
