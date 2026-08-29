import { expect, test } from './fixtures'

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

  // 松手：正好一次权威渲染
  await page.waitForTimeout(3000)
  expect(renders.length, `松手后应当只有一次定稿渲染，实际 ${renders.length} 次`).toBe(1)

  // 真实计时（预览计时环由 lib/previewTrace.ts 维护）
  const timings = await page.evaluate(() => {
    const w = window as unknown as { __MM_PREVIEW_TIMINGS__?: Record<string, number>[] }
    return w.__MM_PREVIEW_TIMINGS__ ?? []
  })
  const last = timings.at(-1)
  expect(last, '应当留下一条预览计时').toBeTruthy()
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
