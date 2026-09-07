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
 *
 * 定位一律走稳定锚点：`[data-dialog="export"]` = 导出对话框、`[data-dialog-close]`
 * = 它的关闭按钮。以前这里写 `[role=dialog]` 与 `[aria-label=关闭]`——前者在这个
 * 应用里有五个产出点、`querySelector` 拿的是排在最前的那个，后者是本地化文案
 * （issue #307）。锚点登记在 `web/AGENTS.md`。
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
      const d = document.querySelector('[data-dialog="export"]') as HTMLElement | null
      if (!d) continue
      const r = d.getBoundingClientRect()
      const cs = getComputedStyle(d)
      out.push({
        dx: Math.abs(r.x + r.width / 2 - innerWidth / 2),
        dy: Math.abs(r.y + r.height / 2 - innerHeight / 2),
        state: d.dataset.state,
        anim: cs.animationName,
        op: getComputedStyle(document.querySelector('[data-dialog="export"]')!.parentElement!).opacity,
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
    const close = document.querySelector('[data-dialog="export"] [data-dialog-close]') as HTMLElement
    close.click()
    const seen: { state?: string; anim: string }[] = []
    for (let i = 0; i < 10; i++) {
      await new Promise((r) => requestAnimationFrame(r))
      const d = document.querySelector('[data-dialog="export"]') as HTMLElement | null
      if (d) seen.push({ state: d.dataset.state, anim: getComputedStyle(d).animationName })
    }
    return { seen, goneAfter: !document.querySelector('[data-dialog="export"]') }
  })
  console.log(`[动效] 弹窗退场：留存 ${exit.seen.length} 帧，动画=${exit.seen[0]?.anim}，state=${exit.seen[0]?.state}，最终卸载=${exit.goneAfter}`)
  expect(exit.seen.length, '退场时节点应当被保活播完，而不是瞬间消失').toBeGreaterThan(0)
  expect(exit.seen[0].state).toBe('closed')
  expect(exit.seen[0].anim).toBe('pop-out')

  // ---- toast
  const toast = await page.evaluate(async () => {
    window.dispatchEvent(new CustomEvent('tavotto:autosave-error', { detail: {} }))
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
    const dialog = () => document.querySelector('[data-dialog="export"]') as HTMLElement | null
    const btn = [...document.querySelectorAll('button')].find(
      (b) => b.textContent?.trim() === '导出',
    ) as HTMLButtonElement
    btn.click()
    await new Promise((rr) => requestAnimationFrame(rr))
    const d = dialog()!
    const dur = getComputedStyle(d).animationDuration
    ;(document.querySelector('[data-dialog="export"] [data-dialog-close]') as HTMLElement).click()
    // **主判据在这一刻取样：点完关闭、只让出微任务，一帧都不让。**
    //
    // 两条互相制约的时限，取样点必须落在中间：
    //   ① React 的离散更新走**微任务**刷新——`close.click()` 返回时 DOM 还没换成
    //      `data-state=closed`，直接读只能读到进场那条 `pop-in`（实测就是这么红的）；
    //   ② 让出一帧就晚了——关掉动效后退场只有 0.01ms，早已播完，而没有 fill-mode
    //      的 CSS 动画一播完就从 `getAnimations()` 里消失，拿到空数组。
    // 微任务**不推进动画时间轴**（`document.timeline` 只在帧边界前进），所以在这里
    // 等多少个微任务都不会让 0.01ms 的退场动画播完：既等到了 React 换类名，又没
    // 让动画有机会结束。`getAnimations()` 按规范自己会先刷一次样式，类名一换就建得出。
    const seen = new Map<string, number>()
    for (let i = 0; i < 20; i++) {
      await Promise.resolve()
      const el = dialog()
      if (!el) break
      for (const anim of el.getAnimations()) {
        seen.set(
          (anim as unknown as { animationName?: string }).animationName ?? '',
          Number(anim.effect?.getComputedTiming().activeDuration ?? NaN),
        )
      }
    }
    const exitAnims = [...seen].map(([name, activeDuration]) => ({ name, activeDuration }))
    let frames = 0
    for (let i = 0; i < 10; i++) {
      await new Promise((rr) => requestAnimationFrame(rr))
      if (dialog()) frames++
    }
    return { dur, frames, exitAnims }
  })
  console.log(
    `[动效] reduced-motion：animation-duration=${r.dur}，` +
      `退场动画=${JSON.stringify(r.exitAnims)}，退场留存 ${r.frames} 帧`,
  )
  // ── 主判据：退场动画的**有效时长**（issue #308）────────────────────────
  // 它与下面两条判的是同一件事，区别在量程：开着动效读到 90（`--duration-exit`），
  // 关掉读到 0.01，九千倍的间隔，且**完全不受 runner 的调度抖动影响**——
  // 它读的是动画自己的时间轴，不是「浏览器几帧之内做完了什么」。
  //
  // **排在最前面是有意的。** 变异时（停掉 index.css 的 reduced-motion override）
  // 三条会一起红，而 expect 一抛后面就不执行了：主判据排在后面的话，它在变异下
  // 一次都执行不到，「变异后用例红了」于是变成对它的假确认（同族：「正向用例
  // 被前置校验截断」，下面那段注释里记着上一轮踩到的现场）。
  //
  // **`length > 0` 这条不是凑数的**：探针晚一步元素就卸载了，`getAnimations()`
  // 返回空数组，`Math.max(...[])` 是 -Infinity、`.every()` 是 true——判据会静默
  // 变成恒真。
  //
  // 两条反证（2026-09-08，本机 chromium，都用退出码判）：
  //   ① **杀得死真缺陷**：停掉 `web/src/index.css` 的 reduced-motion 全局 override
  //      并真正重新构建（构建退 0、包内产物指纹 c793cc19→fd199c7f、产物里那条
  //      `prefers-reduced-motion:reduce){*,:before,:after{` 规则计数 1→0），本行读到
  //      `[{"name":"pop-out","activeDuration":90}]`，`Expected: < 1 / Received: 90`，
  //      **报错行就是本行**（主判据排在最前，没被前面的断言截断）。还原后重新构建，
  //      指纹与规则计数原样回来，读回 0.01。开着 90 / 关掉 0.01 = 九千倍。
  //   ② **防恒真那条自己也验过**：把取样人为挪到 4 帧之后（元素已卸载），日志打印
  //      `退场动画=[]`，本段第一行当场红（`Expected: > 0 / Received: 0`）。
  expect(r.exitAnims.length, '关闭那一刻应当取到退场动画（空数组会让下面两条恒真）')
    .toBeGreaterThan(0)
  expect(
    r.exitAnims.map((a) => a.name),
    '取到的应当是退场动画 pop-out',
  ).toContain('pop-out')
  expect(
    Math.max(...r.exitAnims.map((a) => a.activeDuration)),
    `退场动画的有效时长应当被压到 0（实得 ${JSON.stringify(r.exitAnims)}）`,
  ).toBeLessThan(1)
  // index.css 的全局 override 把时长压到 0.01ms —— 动画不再有可感知的时长
  expect(parseFloat(r.dur)).toBeLessThan(0.001)
  // 这一行判别的是「有没有在播」，两档之间隔着一条缝：
  //   **不播** = Radix 收到 animationend 再走 React 卸载的固有开销，量到的其实是
  //   runner 的调度节奏——本机 chromium 1–2 帧，CI runner 3 帧、抖得到 4；
  //   **在播** = pop-out 那 90ms，6 帧起（上一条用例实测 7 帧）。
  // 阈值取 5 是**落在缝里**，不是「放宽到绿为止」。原来写 `<= 3`，贴着固有开销的
  // 上沿、余量为零：它量的是调度抖动，不是产品播没播动画。2026-09-07 的 CI 日志，
  // 三棵树、五条腿打印的都是 3——无关判据 PR 的合并组（run 34081435324）posix / windows
  // 各 3，本分支 PR 头 07b7f283（run 34079798346）posix / windows 各 3，本分支合并组
  // （run 34083038739）的 posix 腿也是 3；**同一棵树**的 windows 腿却打印 4，初次与重试
  // 都 4，于是 `<= 3` 把整队无关的 PR 挡在合并队列外。判据的主语没错，是量程贴了边。
  //
  // 变异反证（2026-09-07，本机 chromium）。先把 `web/src/index.css` 里那段
  // `@media (prefers-reduced-motion: reduce)` 的全局 override 停掉再重新构建，并确认
  // **构建自己退 0**、包内 `src/tavotto/web` 指纹真的变了、产物里那条 `*,:before,:after`
  // 规则计数 1→0；不确认这三样就可能在拿旧产物跑，绿是假的。然后两步，缺一步都不算数：
  //   ① **量数字**（不看红绿）：跑原样的用例，6 轮打印 7/7/6/6/7/6，落进「在播」那一档；
  //      还原态 8 轮是 2/2/2/1/1/2/2/1。两个总体分得开，阈值 5 落在中间。
  //   ② **让这一行独自判决**：只看「变异后用例红了」会自欺——上一行的时长断言先抛，
  //      本行在变异下**一次都执行不到**（实测报错行就是时长断言那行）。把它换成等行数的
  //      空操作再跑，3 轮才全部红在本行：`Expected: <= 5` / `Received: 6、7、6`。
  //      这一族叫「正向用例被前置校验截断」，前置先抛时后面那条什么都没证明。
  // 还原后重新构建，指纹与规则计数原样回来，用例三轮全绿。放宽之后判据仍然杀得死真缺陷。
  //
  // 与上面几行不重复，都要留：时长断言看 CSS override 生没生效，
  // 这一条看**有没有别的东西**（例如写死 setTimeout 的保活）在关掉动效后还留着浮层，
  // 那种缺陷时长断言看不见。
  //
  // 2026-09-08 加 `getAnimations` 主判据时复核过这条的量程有没有被挪动：主判据的
  // 取样只让出微任务、不跨帧，所以帧数的起点没变。同一轮实测，变异下打印 8，
  // 还原后打印 1–2——两档仍然分得开，阈值 5 仍在缝里。
  expect(r.frames, '关掉动效后不该还有可感知的保活期').toBeLessThanOrEqual(5)
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
    const drawer = () => document.querySelector('[data-left-drawer]') as HTMLElement | null
    const widthOf = (el: HTMLElement) => +el.getBoundingClientRect().width.toFixed(1)

    // **采样起点是「宽度动画的第一帧已经提交」，不是「点完之后的第一帧」**（#133）。
    //
    // 旧写法先进 rAF 循环再点，然后固定采 20 帧。那实际量的是「浏览器在 20 帧
    // （约 320ms）内有没有把动画首帧提交出来」——退场只有 120ms，动画起步被推迟
    // 时 8 帧全读到 300，`最后一帧 < 第一帧` 于是 300 < 300 直接红。判据的主语
    // 错位：它自称测「先播完退场再卸载」，实际在赌起步速度（同族：#138 / #141）。
    // CI 上 `retries: 1` 一直在吞它，只有本地 0 重试才偶尔看得见。
    const opened = drawer()!
    const startWidth = widthOf(opened)
    btn.click()
    let warmup = 0
    while (opened.isConnected && widthOf(opened) === startWidth && warmup++ < 180) {
      await new Promise((r) => requestAnimationFrame(r))
    }

    for (let i = 0; i < 20; i++) {
      const el = drawer()
      if (!el) break
      const inner = el.firstElementChild as HTMLElement
      rows.push({
        outer: widthOf(el),
        inner: +inner.getBoundingClientRect().width.toFixed(1),
        anim: getComputedStyle(el).animationName,
        state: el.dataset.state,
      })
      await new Promise((r) => requestAnimationFrame(r))
    }
    return { rows, gone: !document.querySelector('[data-left-drawer]'), startWidth, warmup }
  })
  const after = await grab()

  const outers = closing.rows.map((r) => r.outer)
  const inners = new Set(closing.rows.map((r) => r.inner))
  console.log(
    `[动效] 收起：${closing.rows.length} 帧 · 动画=${closing.rows[0]?.anim} · state=${closing.rows[0]?.state} · ` +
      `起始宽 ${closing.startWidth} · 等首帧 ${closing.warmup} 帧 · ` +
      `外层宽 ${outers[0]}→${outers.at(-1)} · 内层宽 ${[...inners].join('/')} · 最终卸载=${closing.gone} · ` +
      `主线程 ${(((after.TaskDuration - before.TaskDuration) * 1000) / closing.rows.length).toFixed(2)}ms/帧`,
  )
  expect(
    closing.warmup,
    `宽度动画的首帧始终没提交（等了 ${closing.warmup} 帧，宽度一直是 ${closing.startWidth}）`,
  ).toBeLessThan(180)
  expect(closing.rows.length, '应当先播退场再卸载').toBeGreaterThan(2)
  expect(closing.rows[0].state).toBe('closed')
  expect(closing.rows[0].anim).toBe('drawer-out')
  // **量的是「退场期间缩过没有」，不是「最后一帧比第一帧小」。**
  // 动画播完那一刻元素会弹回基准宽度（`drawer-out` 没有 fill-mode），React 再过
  // 一帧才把它卸掉——所以最后一帧读到 300 还是读到 21 完全看采样落在弹回之前还是
  // 之后，是抛硬币。实测 20 轮里两轮红，日志形如 `外层宽 265.8→300`。
  // 「缩过」这条判据与弹回无关，也仍然抓得住 #133 的原始现场（全程 300 → 红）。
  expect(
    Math.min(...outers),
    `退场期间外层宽度应当真的缩过（起始 ${closing.startWidth}，逐帧 ${outers.join('/')}）`,
  ).toBeLessThan(closing.startWidth)
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
