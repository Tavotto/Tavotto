import { expect, test } from './fixtures'

/**
 * 真浏览器里跑一遍「选中曲线沿真实路径、bbox 空白角不再误命中」。
 *
 * 为什么值得单独一条 e2e：jsdom 那两套用例喂的是手写的 manifest，验的是
 * 结构与算术；**只有真浏览器 + 真 matplotlib** 才能回答「引擎真的把
 * geometry 发过来了吗」「点在真曲线上真的选中它了吗」。这条用例把整条链
 * 走通：引擎算路径 → 响应带 geometry → 命中按路径 → 覆盖层画 path。
 *
 * 取点用 `SVGPathElement.getPointAtLength()` + `getScreenCTM()`——曲线上的
 * 精确一点只有浏览器算得出来，猜 bbox 中点在弯曲的曲线上会落空。
 */
test('图内曲线：沿真实路径选中，bbox 空白角不误命中', async ({ app, page }) => {
  const a = await app()

  // 引擎的 render 响应里必须真的带上 geometry（这一步断的是「后端有没有发」）
  let sawGeometry = false
  page.on('response', async (res) => {
    if (!res.url().includes('/api/engine/render')) return
    try {
      const body = await res.json()
      const els = body?.manifest?.elements ?? []
      if (els.some((e: { geometry?: unknown }) => e.geometry)) sawGeometry = true
    } catch {
      /* 不是 JSON 就跳过 */
    }
  })

  await page.goto(a.baseURL)
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  await expect(page.getByText('画布是空的')).toHaveCount(0)

  // 右栏与上下文工具条各有一个入口，取右栏那个
  await page.getByRole('button', { name: '编辑图内元素' }).first().click()
  const svgWrap = page.locator('[data-element-svg]').first()
  await expect(svgWrap.locator('svg')).toBeVisible({ timeout: 60_000 })
  await page.waitForTimeout(1500)
  expect(sawGeometry, 'manifest 里应当带上 geometry').toBe(true)

  /** 曲线上的一个精确点 + 它 bbox 的四个角（屏幕坐标） */
  const probe = await page.evaluate(() => {
    const svg = document.querySelector('[data-element-svg] svg')
    if (!svg) return null
    for (const g of svg.querySelectorAll('[id^="axes_"]')) {
      if (!/\.lines_\d+$/.test(g.id)) continue
      const path = g.querySelector('path') as SVGPathElement | null
      if (!path?.getTotalLength) continue
      const len = path.getTotalLength()
      if (len < 40) continue
      const ctm = path.getScreenCTM()
      if (!ctm) continue
      const at = (frac: number) => {
        const p = path.getPointAtLength(len * frac)
        return { x: p.x * ctm.a + p.y * ctm.c + ctm.e, y: p.x * ctm.b + p.y * ctm.d + ctm.f }
      }
      const r = path.getBoundingClientRect()
      if (r.width < 30 || r.height < 30) continue   // 扁平线没有「空白角」可言
      return { id: g.id, mid: at(0.5), rect: { x: r.x, y: r.y, w: r.width, h: r.height } }
    }
    return null
  })
  expect(probe, '图里应当有一条有起伏的曲线').not.toBeNull()

  /** 覆盖层里当前有几条沿路径的描示 / 几个带底色的矩形选中框 */
  const overlay = () =>
    page.evaluate(() => {
      const svg = document.querySelector('svg.pointer-events-none') as SVGSVGElement | null
      const paths = [...(svg?.querySelectorAll('path[d]') ?? [])].filter(
        (p) => (p.getAttribute('d') ?? '').startsWith('M'),
      ).length
      const rects = svg?.querySelectorAll('rect[fill-opacity]').length ?? 0
      return { paths, rects }
    })

  // 1) 点在曲线**本身**上 → 沿路径描示，没有带底色的矩形选中框
  await page.mouse.click(probe!.mid.x, probe!.mid.y)
  await page.waitForTimeout(300)
  const onCurve = await overlay()
  expect(onCurve.paths, '选中曲线应当画一条沿真实路径的 path').toBeGreaterThan(0)
  expect(onCurve.rects, '曲线不该再有带底色的矩形选中框').toBe(0)

  // 2) 点在曲线 bbox 的角上（离曲线很远的空白）→ 选中的不再是曲线
  //    四个角里挑一个离曲线最远的：曲线不一定从哪个角附近经过
  const corner = await page.evaluate((p) => {
    const svg = document.querySelector('[data-element-svg] svg')!
    const path = svg.querySelector(`[id="${p.id}"] path`) as SVGPathElement
    const ctm = path.getScreenCTM()!
    const len = path.getTotalLength()
    const pts: { x: number; y: number }[] = []
    for (let i = 0; i <= 200; i++) {
      const q = path.getPointAtLength((len * i) / 200)
      pts.push({ x: q.x * ctm.a + q.y * ctm.c + ctm.e, y: q.x * ctm.b + q.y * ctm.d + ctm.f })
    }
    const inset = 6
    const corners = [
      { x: p.rect.x + inset, y: p.rect.y + inset },
      { x: p.rect.x + p.rect.w - inset, y: p.rect.y + inset },
      { x: p.rect.x + inset, y: p.rect.y + p.rect.h - inset },
      { x: p.rect.x + p.rect.w - inset, y: p.rect.y + p.rect.h - inset },
    ]
    let best = corners[0]
    let bestD = -1
    for (const c of corners) {
      const d = Math.min(...pts.map((q) => Math.hypot(q.x - c.x, q.y - c.y)))
      if (d > bestD) {
        bestD = d
        best = c
      }
    }
    return { ...best, dist: bestD }
  }, probe!)
  expect(corner.dist, 'bbox 角到曲线应当有足够距离才算「空白」').toBeGreaterThan(20)

  await page.mouse.click(corner.x, corner.y)
  await page.waitForTimeout(300)
  const atCorner = await overlay()
  expect(
    atCorner.paths,
    `点 bbox 空白角（离曲线 ${Math.round(corner.dist)}px）不该还选中曲线`,
  ).toBe(0)
})
