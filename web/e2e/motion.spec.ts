import { expect, test } from './fixtures'

/**
 * 动效：**真浏览器里**确认它们真的在播。
 *
 * jsdom 不跑动画、不算样式，所以「动效静默失灵」在单测里是全绿的：token 改错名、
 * Tailwind 的 `--animate-*` 没编译出工具类、Radix 的退场保活被条件渲染破坏——
 * 每一条都只有真浏览器看得见。
 *
 * 这条用例抓到过一次真的：给 Dialog 的关键帧补 `translate(-50%,-50%)` 时，
 * Tailwind v4 的 `-translate-x-1/2` 编译成**独立的 `translate` 属性**，两者叠加，
 * 播放期间弹窗偏出去 250px。单测怎么写都照不到。
 */
test('弹窗 / 菜单 / toast 的进出场都在播，且弹窗播放期间保持居中', async ({ app, page }) => {
  const a = await app()
  await page.goto(a.baseURL)
  await page.waitForTimeout(1500)

  // ---- 弹窗：播放期间必须始终居中（关键帧里没带 translate(-50%,-50%) 就会偏半个身位）
  const dlg = await page.evaluate(async () => {
    const btn = [...document.querySelectorAll('button')].find(
      (b) => b.textContent?.trim() === '导出',
    ) as HTMLButtonElement
    btn.click()
    const out: { dx: number; dy: number; state?: string; anim: string; op: string }[] = []
    for (let i = 0; i < 10; i++) {
      await new Promise((r) => requestAnimationFrame(r))
      const d = document.querySelector('[role=dialog]') as HTMLElement | null
      if (!d) continue
      const r = d.getBoundingClientRect()
      const cs = getComputedStyle(d)
      out.push({
        dx: Math.abs(r.x + r.width / 2 - innerWidth / 2),
        dy: Math.abs(r.y + r.height / 2 - innerHeight / 2),
        state: d.dataset.state,
        anim: cs.animationName,
        op: getComputedStyle(document.querySelector('[role=dialog]')!.parentElement!).opacity,
      })
    }
    return out
  })
  console.log(`[动效] 弹窗进场 ${dlg.length} 帧，动画=${dlg[0]?.anim}，最大偏心 ${Math.max(...dlg.map((s) => Math.max(s.dx, s.dy))).toFixed(1)}px`)
  expect(dlg.length, '应当采到帧').toBeGreaterThan(2)
  expect(dlg[0].anim, '进场应当在播 pop-in').toBe('pop-in')
  for (const s of dlg) {
    expect(s.dx, `播放中偏离水平中心 ${s.dx}px`).toBeLessThan(3)
    expect(s.dy, `播放中偏离垂直中心 ${s.dy}px`).toBeLessThan(3)
  }

  // ---- 弹窗退场：Radix Presence 应当把节点留到动画播完
  const exit = await page.evaluate(async () => {
    const close = document.querySelector('[role=dialog] [aria-label=关闭]') as HTMLElement
    close.click()
    const seen: { state?: string; anim: string }[] = []
    for (let i = 0; i < 10; i++) {
      await new Promise((r) => requestAnimationFrame(r))
      const d = document.querySelector('[role=dialog]') as HTMLElement | null
      if (d) seen.push({ state: d.dataset.state, anim: getComputedStyle(d).animationName })
    }
    return { seen, goneAfter: !document.querySelector('[role=dialog]') }
  })
  console.log(`[动效] 弹窗退场：留存 ${exit.seen.length} 帧，动画=${exit.seen[0]?.anim}，state=${exit.seen[0]?.state}，最终卸载=${exit.goneAfter}`)
  expect(exit.seen.length, '退场时节点应当被保活播完，而不是瞬间消失').toBeGreaterThan(0)
  expect(exit.seen[0].state).toBe('closed')
  expect(exit.seen[0].anim).toBe('pop-out')

  // ---- toast
  const toast = await page.evaluate(async () => {
    window.dispatchEvent(new CustomEvent('magplot:autosave-error', { detail: {} }))
    await new Promise((r) => requestAnimationFrame(r))
    await new Promise((r) => requestAnimationFrame(r))
    const t = document.querySelector('[data-state][class*=rise]') as HTMLElement | null
    return t ? { state: t.dataset.state, anim: getComputedStyle(t).animationName } : null
  })
  console.log(`[动效] toast：${JSON.stringify(toast)}`)
  expect(toast?.anim).toBe('rise-in')

  // ---- 菜单：从触发器那个角展开
  // Radix 的菜单认的是 pointerdown，evaluate 里的 .click() 打不开它
  await page.getByRole('button', { name: '更多' }).click()
  const menu = await page.evaluate(async () => {
    for (let i = 0; i < 3; i++) await new Promise((r) => requestAnimationFrame(r))
    const m = document.querySelector('[role=menu]') as HTMLElement | null
    if (!m) return { found: false }
    const cs = getComputedStyle(m)
    return { found: true, anim: cs.animationName, origin: cs.transformOrigin }
  })
  console.log(`[动效] 菜单：${JSON.stringify(menu)}`)
  expect(menu.found).toBe(true)
  expect(menu.anim).toBe('pop-in')
  // 展开原点应当被 Radix 换算成触发器那个角，而不是默认的 50% 50%
  expect(menu.origin).not.toMatch(/^50% 50%/)

  // ---- 交叉淡出的工具类真的编译出来了（token → utility 这一跳容易静默失灵）
  const cf = await page.evaluate(() => {
    const el = document.createElement('div')
    el.className = 'animate-crossfade-out'
    document.body.appendChild(el)
    const cs = getComputedStyle(el)
    const out = { name: cs.animationName, dur: cs.animationDuration, fill: cs.animationFillMode, timing: cs.animationTimingFunction }
    el.remove()
    return out
  })
  console.log(`[动效] 交叉淡出工具类：${JSON.stringify(cf)}`)
  expect(cf.name).toBe('fade-out')
  expect(cf.fill).toBe('forwards')
  expect(cf.timing).toBe('linear')
})

test('prefers-reduced-motion：动画一帧都不播，浮层立刻消失', async ({ app, page }) => {
  const a = await app()
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto(a.baseURL)
  await page.waitForTimeout(1500)

  const r = await page.evaluate(async () => {
    const btn = [...document.querySelectorAll('button')].find(
      (b) => b.textContent?.trim() === '导出',
    ) as HTMLButtonElement
    btn.click()
    await new Promise((rr) => requestAnimationFrame(rr))
    const d = document.querySelector('[role=dialog]') as HTMLElement
    const dur = getComputedStyle(d).animationDuration
    ;(document.querySelector('[role=dialog] [aria-label=关闭]') as HTMLElement).click()
    let frames = 0
    for (let i = 0; i < 10; i++) {
      await new Promise((rr) => requestAnimationFrame(rr))
      if (document.querySelector('[role=dialog]')) frames++
    }
    return { dur, frames }
  })
  console.log(`[动效] reduced-motion：animation-duration=${r.dur}，退场留存 ${r.frames} 帧`)
  // index.css 的全局 override 把时长压到 0.01ms —— 动画不再有可感知的时长
  expect(parseFloat(r.dur)).toBeLessThan(0.001)
  // 2 帧是 Radix 收到 animationend 再走 React 卸载的固有开销，不是「在播动画」；
  // 真播的话是 pop-out 的 90ms ≈ 6 帧起（上一条用例实测 7 帧）
  expect(r.frames, '关掉动效后不该还有可感知的保活期').toBeLessThanOrEqual(3)
})

/**
 * 侧边抽屉的开合。三件事只有真浏览器验得了：
 *   ① 收起时先播完退场再卸载（条件渲染一破坏就只剩「瞬间消失」）；
 *   ② **内容层宽度全程不变**——动的是外层 width，内容包在定宽内层里靠
 *      overflow 裁掉，不然那 180ms 里文字会跟着挤来挤去；
 *   ③ 宽度把手仍然拖得动——它现在整条在抽屉内侧，正是因为外层要 overflow:hidden。
 */
test('抽屉开合：先播完再卸载、内容不挤、把手仍可拖', async ({ app, page }) => {
  const a = await app()
  // 同文件里有一条会开 reduced-motion，显式复位，免得受用例顺序影响
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  await page.goto(a.baseURL)
  await page.waitForTimeout(1500)

  const rail = page.getByRole('button', { name: '素材', exact: true })
  if ((await rail.getAttribute('aria-expanded')) !== 'true') await rail.click()
  await expect(page.locator('[data-left-drawer]')).toBeVisible()
  await page.waitForTimeout(400)

  const cdp = await page.context().newCDPSession(page)
  await cdp.send('Performance.enable')
  const grab = async () => {
    const { metrics } = await cdp.send('Performance.getMetrics')
    return Object.fromEntries(metrics.map((x) => [x.name, x.value])) as Record<string, number>
  }

  // 收起：应当先播 drawer-out 再卸载；内容层宽度全程不变（不挤）
  const before = await grab()
  const closing = await page.evaluate(async () => {
    const btn = [...document.querySelectorAll('button')].find(
      (b) => b.getAttribute('aria-label') === '素材',
    ) as HTMLElement
    const rows: { outer: number; inner: number; anim: string; state?: string }[] = []
    // **先进 rAF 循环再点**：退场只有 120ms，先点后进循环的话，一次慢帧
    // （GC、前一个 CDP 调用）就足以让第一帧采样落在动画结束之后，
    // 量到的全是收尾宽度——这种用例平时绿、偶尔红，最难查
    for (let i = 0; i < 20; i++) {
      if (i === 0) btn.click()
      await new Promise((r) => requestAnimationFrame(r))
      const el = document.querySelector('[data-left-drawer]') as HTMLElement | null
      if (!el) break
      const inner = el.firstElementChild as HTMLElement
      rows.push({
        outer: +el.getBoundingClientRect().width.toFixed(1),
        inner: +inner.getBoundingClientRect().width.toFixed(1),
        anim: getComputedStyle(el).animationName,
        state: el.dataset.state,
      })
    }
    return { rows, gone: !document.querySelector('[data-left-drawer]') }
  })
  const after = await grab()

  const outers = closing.rows.map((r) => r.outer)
  const inners = new Set(closing.rows.map((r) => r.inner))
  console.log(
    `[动效] 收起：${closing.rows.length} 帧 · 动画=${closing.rows[0]?.anim} · state=${closing.rows[0]?.state} · ` +
      `外层宽 ${outers[0]}→${outers.at(-1)} · 内层宽 ${[...inners].join('/')} · 最终卸载=${closing.gone} · ` +
      `主线程 ${(((after.TaskDuration - before.TaskDuration) * 1000) / closing.rows.length).toFixed(2)}ms/帧`,
  )
  expect(closing.rows.length, '应当先播退场再卸载').toBeGreaterThan(2)
  expect(closing.rows[0].state).toBe('closed')
  expect(closing.rows[0].anim).toBe('drawer-out')
  expect(outers.at(-1)!).toBeLessThan(outers[0])
  expect(inners.size, '内容层宽度全程不变（不跟着挤）').toBe(1)
  expect(closing.gone).toBe(true)

  // 展开：drawer-in
  const opening = await page.evaluate(async () => {
    const btn = [...document.querySelectorAll('button')].find(
      (b) => b.getAttribute('aria-label') === '素材',
    ) as HTMLElement
    const rows: { outer: number; anim: string }[] = []
    for (let i = 0; i < 8; i++) {
      if (i === 0) btn.click()
      await new Promise((r) => requestAnimationFrame(r))
      const el = document.querySelector('[data-left-drawer]') as HTMLElement | null
      if (el) rows.push({ outer: +el.getBoundingClientRect().width.toFixed(1), anim: getComputedStyle(el).animationName })
    }
    return rows
  })
  console.log(`[动效] 展开：动画=${opening[0]?.anim} · 宽 ${opening.map((r) => r.outer).join('→')}`)
  expect(opening[0].anim).toBe('drawer-in')

  // 宽度把手仍然可拖（它现在整条在抽屉内侧，被 overflow-hidden 剪掉就没用了）
  await page.waitForTimeout(400)
  const handle = page.getByRole('separator', { name: /调整侧栏宽度/ })
  const box = (await handle.boundingBox())!
  const w0 = (await page.locator('[data-left-drawer]').boundingBox())!.width
  await page.mouse.move(box.x + box.width / 2, box.y + 200)
  await page.mouse.down()
  await page.mouse.move(box.x + box.width / 2 + 60, box.y + 200, { steps: 8 })
  await page.mouse.up()
  await page.waitForTimeout(200)
  const w1 = (await page.locator('[data-left-drawer]').boundingBox())!.width
  console.log(`[动效] 把手拖动：${w0} → ${w1}`)
  expect(w1, '把手被 overflow-hidden 剪掉的话这里拖不动').toBeGreaterThan(w0 + 30)
})

/**
 * 列表重排的 FLIP。重排是在 drop 那一刻整排换位的（拖动中只有一条落点提示线），
 * 不给动效就是「啪」地跳一下，看不出是哪一个被挪走了。
 *
 * jsdom 里既没有布局也没有 Web Animations API，位移算得对不对由
 * src/lib/useFlip.test 用桩看护；**动画到底有没有真的播**只有这里验得了。
 */
test('画布标签重排：每个标签从原位滑到新位', async ({ app, page }) => {
  const a = await app()
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  await page.goto(a.baseURL)
  await page.waitForTimeout(1500)

  await page.getByRole('button', { name: '新建画布' }).click()
  await page.waitForTimeout(400)
  const tabs = page.getByRole('tab')
  expect(await tabs.count()).toBeGreaterThanOrEqual(2)

  // 记下每次 animate 的起始位移
  await page.evaluate(() => {
    const w = window as unknown as { __flip__: { dx: number; dy: number }[] }
    w.__flip__ = []
    const orig = Element.prototype.animate
    Element.prototype.animate = function (this: Element, frames: unknown, opts: unknown) {
      const f = (frames as { translate?: string; transform?: string }[])?.[0]
      const m = String(f?.translate ?? f?.transform ?? '').match(/(-?[\d.]+)px[ ,]+\s*(-?[\d.]+)px/)
      if (m) w.__flip__.push({ dx: Number(m[1]), dy: Number(m[2]) })
      return orig.call(this, frames as Keyframe[], opts as KeyframeAnimationOptions)
    } as typeof Element.prototype.animate
  })

  const namesBefore = await tabs.allInnerTexts()
  await tabs.nth(1).dragTo(tabs.nth(0))
  await page.waitForTimeout(400)
  const namesAfter = await tabs.allInnerTexts()
  console.log(`[动效] 标签顺序 ${JSON.stringify(namesBefore)} → ${JSON.stringify(namesAfter)}`)

  const flips = await page.evaluate(
    () => (window as unknown as { __flip__: { dx: number; dy: number }[] }).__flip__,
  )
  console.log(`[动效] 标签重排触发 ${flips.length} 段 FLIP：${JSON.stringify(flips)}`)
  expect(flips.length, '两个标签都该从原位滑过来').toBeGreaterThanOrEqual(2)
  // 标签是横排：位移必须发生在 x 上
  expect(flips.every((f) => Math.abs(f.dx) > 1)).toBe(true)
})
