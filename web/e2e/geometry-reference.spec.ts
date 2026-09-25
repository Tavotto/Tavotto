import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

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
    // 快速编辑里图内编辑宿主是单例：恰好一个才量，不取「第一个匹配」
    const hosts = document.querySelectorAll('[data-element-svg]')
    if (hosts.length !== 1) throw new Error(`图内编辑宿主应恰有一个，实际 ${hosts.length}`)
    const n = hosts[0].querySelector(`[id="${id}"]`)
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
  // 素材卡认稳定锚点 data-card，不认文件名文案
  await page.locator('[data-card="Fig1_kinetics.pdf"]').dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-element-svg]')).toHaveCount(1, { timeout: 60_000 })
  await expect(page.locator('[data-element-svg] > svg')).toBeVisible({ timeout: 60_000 })
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

for (const [label, key] of [
  ['默认（适应画布）', null],
  ['放大一档', 'Control+='],
  ['缩小一档', 'Control+-'],
] as const) {
  test(`GEO-01 单选：${label} 下拖标题，权威重渲染后墨迹框平移 = Δs（≤ ${BUDGET_PX} CSS px）`, async ({ app, page }) => {
    const a = await app()
    await openFigure(page, a.baseURL)
    await zoomTo(page, key)
    // 读数节点里还挂着滚动数字的装饰位，只取第一个「N%」
    const zoom = /(\d+)%/.exec((await page.locator('[data-zoom-readout]').first().textContent()) ?? '')?.[1]

    const b0 = await boxOf(page, 'axes_0.title')
    expect(b0, '图里应当有 axes_0.title').not.toBeNull()
    const other0 = await boxOf(page, 'axes_0.ylabel')
    const ds: [number, number] = [37, 23]
    await dragAndSettle(page, center(b0!), ds)

    const b1 = (await boxOf(page, 'axes_0.title'))!
    const err = [b1.x - b0!.x - ds[0], b1.y - b0!.y - ds[1]]
    console.log(`[GEO-01 e2e] zoom=${zoom}% Δbox=(${(b1.x - b0!.x).toFixed(3)}, ${(b1.y - b0!.y).toFixed(3)}) 误差=(${err.map((v) => v.toFixed(3))}) 尺寸变化=(${(b1.w - b0!.w).toFixed(3)}, ${(b1.h - b0!.h).toFixed(3)})`)
    expect(Math.abs(err[0])).toBeLessThanOrEqual(BUDGET_PX)
    expect(Math.abs(err[1])).toBeLessThanOrEqual(BUDGET_PX)
    // 尺寸不变（锚点平移，不是被重排）
    expect(Math.abs(b1.w - b0!.w)).toBeLessThanOrEqual(BUDGET_PX)
    expect(Math.abs(b1.h - b0!.h)).toBeLessThanOrEqual(BUDGET_PX)
    // 非目标不动
    const other1 = (await boxOf(page, 'axes_0.ylabel'))!
    expect(Math.abs(other1.x - other0!.x)).toBeLessThanOrEqual(BUDGET_PX)
    expect(Math.abs(other1.y - other0!.y)).toBeLessThanOrEqual(BUDGET_PX)
  })
}

test.describe('DPR 2', () => {
  test.use({ deviceScaleFactor: 2 })
  test('GEO-01 单选：高 DPI 下拖标题，墨迹框平移 = Δs（DPR 不进 CSS 像素位移）', async ({ app, page }) => {
    const a = await app()
    await openFigure(page, a.baseURL)
    expect(await page.evaluate(() => window.devicePixelRatio)).toBe(2)
    const b0 = (await boxOf(page, 'axes_0.title'))!
    const ds: [number, number] = [-29, 31]
    await dragAndSettle(page, center(b0), ds)
    const b1 = (await boxOf(page, 'axes_0.title'))!
    const err = [b1.x - b0.x - ds[0], b1.y - b0.y - ds[1]]
    console.log(`[GEO-01 e2e] DPR=2 误差=(${err.map((v) => v.toFixed(3))})`)
    expect(Math.abs(err[0])).toBeLessThanOrEqual(BUDGET_PX)
    expect(Math.abs(err[1])).toBeLessThanOrEqual(BUDGET_PX)
  })
})

test('GEO-02 多选：标题 + y 轴标签一起拖，两者各平移 Δs 一次，未选中的图例不动', async ({ app, page }) => {
  const a = await app()
  await openFigure(page, a.baseURL)
  await zoomTo(page, 'Control+=')
  const tA = (await boxOf(page, 'axes_0.title'))!
  const yA = (await boxOf(page, 'axes_0.ylabel'))!
  // 点标题选中，⇧ 点 y 轴标签加选（PanelView 的加选语义）
  // （x 轴标签在这张示例图里压在面板下沿，中心点落在面板外，点不中；
  //   图例的整组平移见 docs/qa/2026-09-24/geo/repro/ 的图例横跳复现——那是已知缺陷，不进这条门禁）
  await page.mouse.click(center(tA).x, center(tA).y)
  await page.keyboard.down('Shift')
  await page.mouse.click(center(yA).x, center(yA).y)
  await page.keyboard.up('Shift')
  await page.waitForTimeout(300)

  // 第一拖只是把两者从「matplotlib 自动定位」换成「显式位置」，**不计入**这条门禁：y 轴标签的
  // 自动位置由刻度标签宽度现算，manifest 在 100 dpi Agg 上量文字、画布是矢量 SVG，两边差一截
  // 与平台字形度量有关的量（CI 实测 Linux +1.02 px / Windows −0.28 px / 本机 −0.17 px，标题恒 ≈ 0）
  // ——那是 #576 的首拖跳（同一根因，不是多选的缺陷），在这里只记下数字。
  const warm: [number, number] = [-33, 19]
  await dragAndSettle(page, center(tA), warm)
  const t0 = (await boxOf(page, 'axes_0.title'))!
  const y0 = (await boxOf(page, 'axes_0.ylabel'))!
  const l0 = (await boxOf(page, 'axes_0.legend'))!
  console.log(
    `[GEO-02 e2e] 首拖（#576，不计入）ylabel 误差=(${(y0.x - yA.x - warm[0]).toFixed(3)}, ${(y0.y - yA.y - warm[1]).toFixed(3)}) ` +
      `title 误差=(${(t0.x - tA.x - warm[0]).toFixed(3)}, ${(t0.y - tA.y - warm[1]).toFixed(3)})`,
  )

  // 计入门禁的这一拖：两者都已是显式位置，多选整组平移必须对每个成员恰好 Δs
  const ds: [number, number] = [29, -17]
  await dragAndSettle(page, center(t0), ds)

  const t1 = (await boxOf(page, 'axes_0.title'))!
  const y1 = (await boxOf(page, 'axes_0.ylabel'))!
  const l1 = (await boxOf(page, 'axes_0.legend'))!
  const errs = {
    title: [t1.x - t0.x - ds[0], t1.y - t0.y - ds[1]],
    ylabel: [y1.x - y0.x - ds[0], y1.y - y0.y - ds[1]],
  }
  console.log(`[GEO-02 e2e] 误差 ${JSON.stringify(errs)} legend Δ=(${(l1.x - l0.x).toFixed(3)}, ${(l1.y - l0.y).toFixed(3)})`)
  // 两个都动了（不是只有被拖的那个在动）且各自恰好 Δs（不是 0 也不是 2Δs）
  for (const [gid, e] of Object.entries(errs)) {
    expect(Math.abs(e[0]), gid).toBeLessThanOrEqual(BUDGET_PX)
    expect(Math.abs(e[1]), gid).toBeLessThanOrEqual(BUDGET_PX)
  }
  expect(Math.abs(l1.x - l0.x)).toBeLessThanOrEqual(BUDGET_PX)
  expect(Math.abs(l1.y - l0.y)).toBeLessThanOrEqual(BUDGET_PX)
})
