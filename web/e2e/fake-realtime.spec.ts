import { execSync } from 'node:child_process'
import { cpSync, mkdtempSync, rmSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import type { Page, Route } from '@playwright/test'
import { expect, freePort, test } from './fixtures'

const REPO_ROOT = path.resolve(import.meta.dirname, '..', '..')

/**
 * 假实时交互：**真浏览器里**跑一遍，量的是真实帧代价与真实等待。
 *
 * jsdom 测得到「拖动期间发了几次请求」「历史压了几条」，但量不到浏览器里
 * 一帧要多久——jsdom 没有布局也没有渲染。这条用例做两件 jsdom 做不到的事：
 *
 *   1. 用真实指针事件拖一个图内元素，确认拖动期间 `/api/engine/render`
 *      **一次都没发**，而 SVG 已经跟着手动了；
 *   2. 从 `window.__MM_PREVIEW_TIMINGS__`（预览计时环）读出首帧耗时与
 *      commit→权威 的真实毫秒数，打进日志——性能声明必须有出处。
 */
test('拖图内元素：预览跟手、拖动期间零后端、松手一次定稿', async ({ app, page }) => {
  const a = await app()
  // 用 request 事件而不是 route 匹配：glob 漏掉查询串会让「零请求」白白通过
  const renders: string[] = []
  page.on('request', (req) => {
    if (req.url().includes('/api/engine/render')) renders.push(req.url())
  })

  await page.goto(a.baseURL)
  // Prompt 09 起，双击素材卡 = 打开这张图（快速编辑工作区），**当场就在图内
  // 编辑态**——不再需要先「加入画布」再点一次「编辑图内元素」。
  // 进图内编辑态 → 画布上换成内联 SVG
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  const svgWrap = page.locator('[data-element-svg]').first()
  await expect(svgWrap.locator('svg')).toBeVisible({ timeout: 60_000 })

  // 等首次渲染安顿下来，再开始数请求
  await page.waitForTimeout(1500)
  renders.length = 0

  // 找一个可拖的图内元素（manifest 里 draggable + anchor 的那类：标题/轴标签/图例）
  const target = await page.evaluate(() => {
    const svg = document.querySelector('[data-element-svg] svg')
    if (!svg) return null
    for (const id of ['axes_0.title', 'axes_0.xlabel', 'axes_0.ylabel']) {
      const n = svg.querySelector(`[id="${id}"]`)
      if (n) {
        const r = (n as SVGGraphicsElement).getBoundingClientRect()
        if (r.width > 2 && r.height > 2) {
          return { id, x: r.x + r.width / 2, y: r.y + r.height / 2, transform: n.getAttribute('transform') }
        }
      }
    }
    return null
  })
  expect(target, '图里应当至少有一个可拖的文字元素').not.toBeNull()

  // 真实指针拖动：一步步走，模拟用户的一串 pointermove
  await page.mouse.move(target!.x, target!.y)
  await page.mouse.down()
  for (let i = 1; i <= 40; i++) {
    await page.mouse.move(target!.x + i * 1.5, target!.y + i * 0.8)
  }

  // 拖动期间：SVG 已经跟着动了，后端一次都没被惊动
  const during = await page.evaluate((id) => {
    const n = document.querySelector(`[data-element-svg] [id="${id}"]`)
    return n?.getAttribute('transform') ?? null
  }, target!.id)
  expect(during, '拖动中 SVG 上应当挂着预览位移').toMatch(/^translate\(/)
  // 原有 transform 不能被盖掉
  if (target!.transform) expect(during).toContain(target!.transform)
  expect(renders, `拖动期间不该有任何 /api/engine/render，实际 ${renders.length} 次`).toHaveLength(0)

  await page.mouse.up()

  // 松手：正好一次权威渲染。两半分开量（issue #321 B 表）：
  //   * 「至少一次」交给轮询——它等的是 pointerup → commit → 请求发出这一段，
  //     原先的固定 3000ms 是零余量判据（同机冷启动引擎往返实测过 10 071ms），
  //     方向是假红；
  //   * 「不多于一次」仍用固定窗，但它不再承担首次到达：首个请求到了之后再
  //     等 1000ms，这个窗抓的是「同一次松手发了两次」，两次会是紧挨着的。
  await expect
    .poll(() => renders.length, { timeout: 30_000, message: '松手后应当发出定稿渲染' })
    .toBeGreaterThanOrEqual(1)
  await page.waitForTimeout(1000)
  expect(renders.length, `松手后应当只有一次定稿渲染，实际 ${renders.length} 次`).toBe(1)

  // 真实计时（预览计时环由 lib/previewTrace.ts 维护）。计时环那一条是权威 SVG
  // **换上画布之后**才写的（svgPreviewStore.reattachPreview → traceAuthority），
  // 请求发出 ≠ 响应到达，所以这里也轮询，不拿上面那 1000ms 顺带赌响应时间。
  const readTimings = () =>
    page.evaluate(() => {
      const w = window as unknown as { __MM_PREVIEW_TIMINGS__?: Record<string, number>[] }
      return w.__MM_PREVIEW_TIMINGS__ ?? []
    })
  await expect
    .poll(async () => (await readTimings()).length, {
      timeout: 60_000,
      message: '权威 SVG 换上画布后应当留下一条预览计时',
    })
    .toBeGreaterThan(0)
  const timings = await readTimings()
  const last = timings.at(-1)
  expect(last, '应当留下一条预览计时').toBeTruthy()
  // 权威渲染到了之后仍然只有那一次：定稿渲染的回包不许再触发第二次渲染
  expect(renders.length, `权威渲染到达后仍应只有一次定稿渲染，实际 ${renders.length} 次`).toBe(1)
  console.log(
    `[e2e 假实时] 首帧 ${last!.preview_first_frame}ms · ` +
      `${last!.preview_frame_count}/${last!.preview_move_count} 帧（rAF 合并）· ` +
      `commit→权威 ${last!.commit_to_authority_ms}ms`,
  )
  expect(last!.preview_frame_count as number).toBeGreaterThan(0)
  // 落地帧数永远不多于 pointermove 次数。**这里不断言「一定合并了」**：
  // Playwright 的 mouse.move 是一次一等，每一步都赶得上自己那一帧，合并率
  // 天然是 0。合并机制本身由 svgPreviewStore.test 的「100 次 move → 1 帧」看护，
  // 真实用户的连续拖动才会出现一帧内多个 move
  expect(last!.preview_move_count as number).toBeGreaterThanOrEqual(
    last!.preview_frame_count as number,
  )
})

/* -------------------------------------------------------------------------- */
/*  QA 2026-09-24 §2（STATE-01 / 02 / 05）：逐帧、取消入口、乱序回包              */
/* -------------------------------------------------------------------------- */
//
// 下面三条补的是上一条量不到的维度：
//   * 逐帧：不是「拖完看一眼」，而是每一帧的 transform 与元素屏幕位置（rAF 采样），
//     一直采到权威 SVG 换上来之后——「松手先弹回原位」只活在那几帧里；
//   * 几何真值由测试侧独立算：预览期间屏幕位置 = 起点 + 鼠标位移（视口 CSS 像素），
//     不调任何生产坐标换算；
//   * 文档一侧从网络上看：拖动中 0 次 `/api/engine/render`、0 次自动保存 PUT；
//     松手后那次渲染与随后的自动保存里恰好一条该元素的 override，且没有预览 transform。

type QaTarget = { id: string; x: number; y: number; transform: string | null }

async function openKineticsTitle(page: Page, baseURL: string): Promise<QaTarget> {
  await page.goto(baseURL)
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 60_000 })
  // 首次渲染与打开文档那次自动保存（1s 防抖）都安顿下来，再开始数
  await page.waitForTimeout(2500)
  const t = await locateGid(page, 'axes_0.title')
  expect(t, '样例图应当有标题').not.toBeNull()
  return t!
}

const locateGid = (page: Page, id: string) =>
  page.evaluate((gid) => {
    const n = document.querySelector(`[data-element-svg] [id="${gid}"]`) as SVGGraphicsElement | null
    if (!n) return null
    const r = n.getBoundingClientRect()
    return { id: gid, x: r.x + r.width / 2, y: r.y + r.height / 2, transform: n.getAttribute('transform') }
  }, id)

/** 请求计数：渲染与自动保存各一份（带 body），`reset()` 之后重新数 */
function netLog(page: Page) {
  const log = { renders: [] as string[], autosaves: [] as string[] }
  page.on('request', (req) => {
    const u = req.url()
    if (u.includes('/api/engine/render')) log.renders.push(req.postData() ?? '')
    if (u.includes('/api/autosave/') && req.method() === 'PUT') log.autosaves.push(req.postData() ?? '')
  })
  return {
    log,
    reset: () => {
      log.renders.length = 0
      log.autosaves.length = 0
    },
  }
}

/** 某元素 override 在一份 JSON 文本里出现了几条（渲染请求体 / 自动保存体通用） */
function overridesOf(json: string, gid: string, prop: string): unknown[] {
  const found: unknown[] = []
  const walk = (v: unknown) => {
    if (Array.isArray(v)) v.forEach(walk)
    else if (v && typeof v === 'object') {
      const o = v as Record<string, unknown>
      if (o.gid === gid && o.prop === prop && 'value' in o) found.push(o.value)
      Object.values(o).forEach(walk)
    }
  }
  walk(JSON.parse(json))
  return found
}

test('STATE-01 逐帧：预览从 base 现算、拖动中零渲染零保存、松手到权威全程不弹回', async ({
  app,
  page,
}) => {
  const a = await app()
  const net = netLog(page)
  const t = await openKineticsTitle(page, a.baseURL)
  net.reset()

  // rAF 采样器：每一帧记下 transform 与屏幕中心；`mark` 记松手那一刻的帧号
  await page.evaluate((gid) => {
    const w = window as unknown as Record<string, unknown>
    const frames: { tf: string | null; x: number | null; y: number | null }[] = []
    w.__qaFrames = frames
    w.__qaStop = false
    const loop = () => {
      const n = document.querySelector(`[data-element-svg] [id="${gid}"]`) as SVGGraphicsElement | null
      const r = n?.getBoundingClientRect()
      frames.push({
        tf: n?.getAttribute('transform') ?? null,
        x: r ? r.x + r.width / 2 : null,
        y: r ? r.y + r.height / 2 : null,
      })
      if (!w.__qaStop) requestAnimationFrame(loop)
    }
    requestAnimationFrame(loop)
  }, t.id)

  const STEP: [number, number] = [1.5, 0.8]
  const N = 40
  await page.mouse.move(t.x, t.y)
  await page.mouse.down()
  for (let i = 1; i <= N; i++) await page.mouse.move(t.x + i * STEP[0], t.y + i * STEP[1])
  await page.waitForTimeout(200) // 让最后一个 move 落到一帧上
  expect(net.log.renders, '拖动中不该有 /api/engine/render').toHaveLength(0)
  expect(net.log.autosaves, '拖动中文档不该被提交（自动保存 PUT 为 0）').toHaveLength(0)
  const mark = await page.evaluate(() => (window as unknown as { __qaFrames: unknown[] }).__qaFrames.length)
  await page.mouse.up()

  await expect.poll(() => net.log.renders.length, { timeout: 30_000 }).toBeGreaterThanOrEqual(1)
  await expect
    .poll(
      () =>
        page.evaluate(
          () => ((window as unknown as { __MM_PREVIEW_TIMINGS__?: unknown[] }).__MM_PREVIEW_TIMINGS__ ?? []).length,
        ),
      { timeout: 60_000, message: '权威 SVG 应当换上画布' },
    )
    .toBeGreaterThan(0)
  await page.waitForTimeout(1500) // 权威上屏之后再多采一秒半，同时等自动保存防抖
  const frames = await page.evaluate(() => {
    const w = window as unknown as { __qaFrames: { tf: string | null; x: number | null; y: number | null }[]; __qaStop: boolean }
    w.__qaStop = true
    return w.__qaFrames
  })

  // ---- 拖动段：格式与「从 base 现算」 ----
  const suffix = t.transform ? ` ${t.transform}` : ''
  const re = /^translate\(([-\d.e]+),([-\d.e]+)\)(.*)$/
  const dragFrames = frames.slice(0, mark).filter((f) => f.tf !== t.transform)
  expect(dragFrames.length, '拖动中应当采到带预览位移的帧').toBeGreaterThan(5)
  const ratios: number[] = []
  for (const f of dragFrames) {
    const m = re.exec(f.tf ?? '')
    expect(m, `预览帧必须是 translate(…) <原始>：${f.tf}`).not.toBeNull()
    expect(m![3], '原始 transform 必须原样保留在 translate 之后').toBe(suffix)
    // 屏幕位移与 SVG 单位位移之比恒定 = 从 base 现算（字符串累加会让这个比值一路涨）
    const dxScreen = f.x! - t.x
    if (Math.abs(dxScreen) > 3) ratios.push(Number(m![1]) / dxScreen)
    // 屏幕上始终沿着鼠标那条直线走（独立模型：屏幕位移 ∥ 鼠标位移）
    expect(Math.abs((f.y! - t.y) * STEP[0] - (f.x! - t.x) * STEP[1])).toBeLessThan(0.75)
  }
  expect(Math.max(...ratios) - Math.min(...ratios)).toBeLessThan(1e-3 * Math.abs(ratios[0]))
  const release = { x: t.x + N * STEP[0], y: t.y + N * STEP[1] }
  const lastDrag = frames[mark - 1]
  expect(Math.abs(lastDrag.x! - release.x), '松手前一帧：元素应当正好跟到鼠标位移').toBeLessThan(0.5)
  expect(Math.abs(lastDrag.y! - release.y)).toBeLessThan(0.5)

  // ---- 松手段：从松手到权威换上之后一秒半，每一帧都停在松手的位置 ----
  const after = frames.slice(mark).filter((f) => f.x != null)
  expect(after.length).toBeGreaterThan(10)
  const worst = Math.max(...after.map((f) => Math.hypot(f.x! - release.x, f.y! - release.y)))
  console.log(`[QA STATE-01] 拖动帧 ${dragFrames.length} · 松手后帧 ${after.length} · 松手后最大偏离 ${worst.toFixed(3)} CSS px`)
  // 弹回原位的幅度是整段位移（~68px）；2px 是「不是弹回」的判据，不是精度预算（精度预算未校准，只记录）
  expect(worst, '松手后某一帧离开了松手位置（弹回 / 跳位）').toBeLessThan(2)
  expect(after.some((f) => f.tf === t.transform), '权威 SVG 应当已经换上（预览 transform 收工）').toBe(true)

  // ---- 文档一侧：一次定稿、一条 override、预览不进持久化 ----
  expect(net.log.renders).toHaveLength(1)
  expect(overridesOf(net.log.renders[0], t.id, 'pos_frac')).toHaveLength(1)
  expect(net.log.autosaves.length, '松手后应当自动保存一次').toBeGreaterThanOrEqual(1)
  const saved = net.log.autosaves.at(-1)!
  expect(overridesOf(saved, t.id, 'pos_frac')).toEqual(overridesOf(net.log.renders[0], t.id, 'pos_frac'))
  expect(saved.includes('translate('), '预览 transform 进了持久化文档').toBe(false)
})

test('STATE-02 取消入口：pointercancel / lostpointercapture 还原 DOM 且零提交；画布外松手照常定稿、不再跟指针', async ({
  app,
  page,
}) => {
  const a = await app()
  const net = netLog(page)
  const t = await openKineticsTitle(page, a.baseURL)

  for (const kind of ['pointercancel', 'lostpointercapture'] as const) {
    net.reset()
    await page.mouse.move(t.x, t.y)
    await page.mouse.down()
    for (let i = 1; i <= 20; i++) await page.mouse.move(t.x + i * 2.5, t.y + i * 1.25)
    await expect.poll(() => locateGid(page, t.id).then((g) => g?.transform)).toMatch(/^translate\(/)
    await page.evaluate((k) => window.dispatchEvent(new PointerEvent(k, { bubbles: true })), kind)
    // 系统作废之后手还在动、然后才松开：两者都不许让这次拖动复活
    await page.mouse.move(t.x + 80, t.y + 40)
    await page.mouse.up()
    await page.waitForTimeout(1800) // 覆盖自动保存的 1s 防抖
    const g = await locateGid(page, t.id)
    expect(g!.transform, `${kind}：DOM 必须还原到 matplotlib 的原样`).toBe(t.transform)
    expect(Math.hypot(g!.x - t.x, g!.y - t.y), `${kind}：元素应当回到原位`).toBeLessThan(0.5)
    expect(net.log.renders, `${kind}：不该发渲染`).toHaveLength(0)
    expect(net.log.autosaves, `${kind}：不该提交文档`).toHaveLength(0)
  }

  // 取消之后紧接着正常拖一次：一次定稿、一条 override
  net.reset()
  await page.mouse.move(t.x, t.y)
  await page.mouse.down()
  for (let i = 1; i <= 20; i++) await page.mouse.move(t.x + i * 2, t.y)
  await page.mouse.up()
  await expect.poll(() => net.log.renders.length, { timeout: 30_000 }).toBeGreaterThanOrEqual(1)
  await page.waitForTimeout(1500)
  expect(net.log.renders).toHaveLength(1)
  expect(overridesOf(net.log.renders[0], t.id, 'pos_frac')).toHaveLength(1)

  // 移出画布（视口内、左侧栏上方）松开：按结束合同定稿，松开之后不再跟着指针走
  const t2 = (await locateGid(page, t.id))!
  net.reset()
  await page.mouse.move(t2.x, t2.y)
  await page.mouse.down()
  for (let i = 1; i <= 20; i++) await page.mouse.move(t2.x + ((8 - t2.x) * i) / 20, t2.y)
  await page.mouse.up()
  await expect.poll(() => net.log.renders.length, { timeout: 30_000 }).toBeGreaterThanOrEqual(1)
  await page.waitForTimeout(1500)
  const settled = await locateGid(page, t.id)
  await page.mouse.move(t2.x + 150, t2.y + 60)
  await page.mouse.move(t2.x + 200, t2.y + 90)
  await page.waitForTimeout(500)
  const later = await locateGid(page, t.id)
  expect(later?.transform ?? null, '松开之后元素不许继续跟着指针').toBe(settled?.transform ?? null)
  expect(net.log.renders, '松开之后的移动不许再触发渲染').toHaveLength(1)
})

test('STATE-05 乱序回包：旧变体晚于新变体返回，不把画布拽回旧版', async ({ app, page }) => {
  const a = await app()
  const net = netLog(page)
  const t = await openKineticsTitle(page, a.baseURL)

  // 扣住下一次渲染（V1 = 拖动标题）；之后的请求照常放行
  const held: Route[] = []
  let holdNext = 0
  await page.route('**/api/engine/render**', async (route) => {
    if (holdNext > 0) {
      holdNext--
      held.push(route)
      return
    }
    await route.continue()
  })
  net.reset()
  holdNext = 1
  await page.mouse.move(t.x, t.y)
  await page.mouse.down()
  for (let i = 1; i <= 20; i++) await page.mouse.move(t.x + i * 3, t.y)
  await page.mouse.up()
  await expect.poll(() => held.length, { timeout: 15_000 }).toBe(1)

  // V2 = 在 V1 之上把标题隐藏（Delete 写 visible:false，不读几何），它先回来
  await page.keyboard.press('Delete')
  await expect.poll(() => net.log.renders.length, { timeout: 15_000 }).toBe(2)
  const v2Body = net.log.renders[1]
  expect(overridesOf(v2Body, t.id, 'visible')).toEqual([false])
  await expect
    .poll(() => locateGid(page, t.id), { timeout: 60_000, message: 'V2 上屏后标题应当不再画出' })
    .toBeNull()

  // V1 姗姗来迟：可以入库，但画布停在 V2
  await held[0].continue()
  await page.waitForTimeout(3000)
  expect(await locateGid(page, t.id), '晚到的 V1 把画布拽回了旧变体').toBeNull()
  expect(net.log.renders, 'V1 到达不应再引出新的渲染').toHaveLength(2)
})

/** 标题中心在整张图 SVG 里的相对位置（与视口 / 缩放无关，重开前后可比） */
const relTitle = (page: Page) =>
  page.evaluate(() => {
    const n = document.querySelector('[data-element-svg] [id="axes_0.title"]') as SVGGraphicsElement | null
    const svg = document.querySelector('[data-element-svg] svg') as SVGGraphicsElement | null
    if (!n || !svg) return null
    const r = n.getBoundingClientRect()
    const s = svg.getBoundingClientRect()
    return { rx: (r.x + r.width / 2 - s.x) / s.width, ry: (r.y + r.height / 2 - s.y) / s.height }
  })

test('STATE-08 保存重开：提交→撤销→重做→自动保存→整个后端（含 worker）退出→同端口同数据目录重开，文档与画面都是最后提交那一版', async ({
  app,
  page,
}) => {
  test.setTimeout(420_000)
  const root = mkdtempSync(path.join(os.tmpdir(), 'tavotto-e2e-state08-'))
  const figures = path.join(root, 'figures')
  cpSync(path.join(REPO_ROOT, 'examples', 'figures'), figures, { recursive: true })
  const env = { TAVOTTO_DATA_DIR: path.join(root, 'data'), TAVOTTO_CONFIG_DIR: path.join(root, 'config') }
  const port = await freePort()
  const net = netLog(page)
  const saveUrls: string[] = []
  page.on('request', (r) => {
    if (r.url().includes('/api/autosave/') && r.method() === 'PUT') saveUrls.push(r.url())
  })

  const a1 = await app({ figures, port, env })
  const t = await openKineticsTitle(page, a1.baseURL)
  const rel0 = await relTitle(page)
  await page.mouse.move(t.x, t.y)
  await page.mouse.down()
  for (let i = 1; i <= 20; i++) await page.mouse.move(t.x + i * 2.5, t.y + i)
  await page.mouse.up()
  await expect.poll(() => relTitle(page).then((r) => r && r.rx - rel0!.rx), { timeout: 60_000 }).toBeGreaterThan(0.05)
  await page.waitForTimeout(1500)
  const relCommitted = (await relTitle(page))!
  await page.keyboard.press('Control+z')
  await expect.poll(() => relTitle(page).then((r) => r && Math.abs(r.rx - rel0!.rx)), { timeout: 60_000 }).toBeLessThan(1e-3)
  await page.keyboard.press('Control+Shift+z')
  await expect.poll(() => relTitle(page).then((r) => r && Math.abs(r.rx - relCommitted.rx)), { timeout: 60_000 }).toBeLessThan(1e-3)
  await page.waitForTimeout(2000) // 自动保存 1s 防抖
  const saved = net.log.autosaves.at(-1)!
  const committed = overridesOf(saved, t.id, 'pos_frac')
  expect(committed, '最后一次自动保存里恰好一条标题位置 override').toHaveLength(1)
  expect(saved.includes('translate('), '预览 transform 进了持久化文档').toBe(false)
  const docId = decodeURIComponent(saveUrls.at(-1)!.split('/api/autosave/')[1].split('?')[0])

  // 整个后端退出（worker 是它的子进程）
  await fetch(`${a1.baseURL}/api/shutdown`, { method: 'POST' }).catch(() => {})
  for (let i = 0; i < 60 && a1.proc.exitCode === null; i++) await new Promise((r) => setTimeout(r, 250))
  expect(a1.proc.exitCode, '后端应当已经退出').not.toBeNull()
  // 产品判「端口空闲」用的是不带 SO_REUSEADDR 的 bind：上一个实例的 TIME_WAIT 没过完就会顺延到
  // 下一个端口（换了源 = 本机记的「上次文档」读不到）。同一种 bind 等到它真空出来再重开。
  const t0 = Date.now()
  for (;;) {
    try {
      execSync(`python3 -c "import socket;s=socket.socket();s.bind(('127.0.0.1',${port}))"`, { stdio: 'ignore' })
      break
    } catch {
      expect(Date.now() - t0, '端口 150s 内都没空出来').toBeLessThan(150_000)
      await new Promise((r) => setTimeout(r, 1000))
    }
  }

  net.reset()
  const a2 = await app({ figures, port, env })
  expect(a2.baseURL).toBe(a1.baseURL)
  await page.goto(a2.baseURL)
  // 重开后第一次权威渲染带的就是最后提交那一份 patches
  await expect.poll(() => net.log.renders.length, { timeout: 60_000 }).toBeGreaterThanOrEqual(1)
  expect(overridesOf(net.log.renders[0], t.id, 'pos_frac')).toEqual(committed)
  // 磁盘上那份文档逐字节回得来：身份、绑定、override 顺序都在
  const reread = await (await page.request.get(`${a2.baseURL}/api/autosave/${encodeURIComponent(docId)}`)).json()
  const panels = (reread.canvases as { objects: { type: string }[] }[]).flatMap((c) => c.objects).filter((o) => o.type === 'panel')
  expect(panels).toHaveLength(1)
  expect(JSON.parse(saved).canvases[0].objects).toEqual(reread.canvases[0].objects)

  // 画面：重开后进图内编辑，标题停在最后提交的位置（相对整张图，独立于视口）
  const panel = page.locator('[data-object-id]').first()
  await expect(panel).toBeVisible({ timeout: 60_000 })
  await panel.click()
  await page.keyboard.press('Enter')
  await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 60_000 })
  await expect
    .poll(() => relTitle(page).then((r) => r && Math.hypot(r.rx - relCommitted.rx, r.ry - relCommitted.ry)), {
      timeout: 60_000,
      message: '重开后标题应当停在最后提交的位置',
    })
    .toBeLessThan(1e-3)
  rmSync(root, { recursive: true, force: true })
})
