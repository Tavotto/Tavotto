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
