/**
 * 树与列表精修（UI 2.0 PR7，G1–G11）的真浏览器判据——jsdom 排不了版，这些只有这里量得了：
 *
 *   G1 焦点环内凹：键盘落在树行上，outline-offset 是负的，环不出行框；
 *   G2 树键位：Home/End、→ 进首子、← 回父；
 *   G3 选中加粗不改变文本排版宽度（截断位置不动）；
 *   G4 聚类行计数与元素行 ⋯ 同中心线；
 *   G5 元素树与图层树列间距同值；
 *   G9 分栏手柄：双击复位、拖动中 Esc 回滚、键盘 Home/End、a11y 值域、拖拽态。
 *
 * zh-CN 的 chromium / webkit 与 en-US 的 chromium-en 都跑；选择器一律稳定的 data-* / role。
 */
import type { Page } from '@playwright/test'
import { expect, openElementsTab, test } from './fixtures'

const CARD = '[data-card="Fig1_kinetics.pdf"]'
test.use({ viewport: { width: 1440, height: 900 } })

async function openFigureTree(page: Page, baseURL: string) {
  await page.goto(baseURL)
  await page.locator(CARD).dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-element-svg] > svg')).toBeVisible({ timeout: 120_000 })
  await openElementsTab(page)
  await expect(page.locator('[data-tree="elements"]')).toHaveCount(1)
  await expect(page.locator('[data-tree="elements"] [data-el]').first()).toBeVisible()
}

/** 稳定钩子：`data-tree`（树）· `data-el` / `data-layer`（行）· `data-tree-label`（名字）· `data-tree-count`（计数槽）· `data-row-menu-trigger`（⋯） */
const rows = (page: Page) => page.locator('[data-tree="elements"] [data-el]')
const active = (page: Page) => page.evaluate(() => (document.activeElement as HTMLElement | null)?.dataset.el ?? null)

test('元素树：键位（Home/End/→进首子/←回父）、内凹焦点环、列间距、计数与 ⋯ 对齐', async ({ app, page }) => {
  const a = await app()
  await openFigureTree(page, a.baseURL)

  const list = await rows(page).evaluateAll((els) => els.map((e) => (e as HTMLElement).dataset.el!))
  expect(list.length).toBeGreaterThan(6)

  // Home / End（焦点漫游即选中，所以先把焦点放进树）
  await rows(page).nth(2).focus()
  await page.keyboard.press('End')
  expect(await active(page)).toBe(list.at(-1))
  await page.keyboard.press('Home')
  expect(await active(page)).toBe(list[0])

  // →：已展开的第一行（整张图）进首子；←：子行回父
  const first = rows(page).first()
  await expect(first).toHaveAttribute('aria-expanded', 'true')
  await page.keyboard.press('ArrowRight')
  const child = (await active(page))!
  expect(child).toBe(list[1])
  const childLevel = Number(await page.locator(`[data-el="${child}"]`).getAttribute('aria-level'))
  expect(childLevel).toBe(Number(await first.getAttribute('aria-level')) + 1)
  // 子行（子图）本身也是展开的：第一下 ← 先收起它，第二下才回父
  const childRow = page.locator(`[data-el="${child}"]`)
  await expect(childRow).toHaveAttribute('aria-expanded', 'true')
  await page.keyboard.press('ArrowLeft')
  await expect(childRow).toHaveAttribute('aria-expanded', 'false')
  expect(await active(page)).toBe(child)
  await page.keyboard.press('ArrowLeft')
  expect(await active(page)).toBe(list[0])
  // 根上 ← 先收起，再 ← 不动
  await page.keyboard.press('ArrowLeft')
  await expect(first).toHaveAttribute('aria-expanded', 'false')
  await page.keyboard.press('ArrowLeft')
  expect(await active(page)).toBe(list[0])
  await page.keyboard.press('ArrowRight')
  await expect(first).toHaveAttribute('aria-expanded', 'true')
  await childRow.focus()
  await page.keyboard.press('ArrowRight')
  await expect(childRow).toHaveAttribute('aria-expanded', 'true')
  await expect(rows(page).first()).toBeVisible()

  // G1：键盘焦点环内凹——outline-offset 为负，且整圈落在行框之内
  await page.keyboard.press('ArrowDown')
  const ring = await page.evaluate(() => {
    const el = document.activeElement as HTMLElement
    const cs = getComputedStyle(el)
    return { offset: parseFloat(cs.outlineOffset), width: parseFloat(cs.outlineWidth), style: cs.outlineStyle }
  })
  expect(ring.style).toBe('solid')
  expect(ring.width).toBe(2)
  expect(ring.offset, '焦点环必须内凹（offset < 0），不能外扩去盖邻行').toBeLessThan(0)

  // G4：聚类行计数（TreeCount 的 28px 槽）与元素行 ⋯ 同一条中心线
  // 每条量值前先断言目标恰好一个：选择器漂了要红在这里，不能量错对象或空转
  const clusterRow = page.locator('[data-tree="elements"] [data-el*="#"]').first()
  const countSlot = clusterRow.locator('[data-tree-count]')
  await expect(countSlot).toHaveCount(1)
  const moreBtn = page.locator('[data-tree="elements"] [data-el]:not([data-el*="#"])').first().locator('[data-row-menu-trigger]')
  await expect(moreBtn).toHaveCount(1)
  const mid = (loc: typeof countSlot) =>
    loc.evaluate((el) => {
      const r = el.getBoundingClientRect()
      return { x: r.x + r.width / 2, w: r.width }
    })
  const centers = { count: await mid(countSlot), more: await mid(moreBtn) }
  expect(centers.count && centers.more).toBeTruthy()
  expect(centers.count!.w).toBeCloseTo(28, 0)
  expect(Math.abs(centers.count!.x - centers.more!.x), '计数与 ⋯ 的中心线应重合').toBeLessThanOrEqual(1)

  // G3：选中加粗不改变文本排版宽度（取最长的那行名字）
  const target = await page.evaluate(() => {
    const spans = [...document.querySelectorAll<HTMLElement>('[data-tree="elements"] [data-el] [data-tree-label]')]
    const longest = spans.sort((x, y) => (y.textContent?.length ?? 0) - (x.textContent?.length ?? 0))[0]
    return (longest.closest('li') as HTMLElement).dataset.el!
  })
  // 抽屉收到下限（280），最长的名字才一定被截断——不截断的行本来就不需要锁
  const handle = page.locator('[data-resize-handle="left-drawer"]')
  await expect(handle).toHaveCount(1)
  await handle.focus()
  await page.keyboard.press('Home')
  await expect(handle).toHaveAttribute('aria-valuenow', '280')
  await page.waitForTimeout(400)
  const textWidth = () =>
    page.evaluate((gid) => {
      const spans = document.querySelectorAll<HTMLElement>(`[data-el="${CSS.escape(gid)}"] [data-tree-label]`)
      if (spans.length !== 1) throw new Error(`标签钩子应恰好 1 个，实际 ${spans.length}`)
      const span = spans[0]
      const r = document.createRange()
      r.selectNodeContents(span)
      return { w: r.getBoundingClientRect().width, clipped: span.scrollWidth > span.clientWidth }
    }, target)
  // 先取消选中再量；默认动效与 prefers-reduced-motion 各量一遍（后者全局给所有属性 0.01ms 过渡，
  // 同一帧里改字重量到的是起点值——锁一度因此悄悄失效）
  for (const reducedMotion of ['no-preference', 'reduce'] as const) {
    await page.emulateMedia({ reducedMotion })
    await page.locator(`[data-el="${target}"]`).focus()
    await page.keyboard.press('Escape')
    await expect(page.locator(`[data-el="${target}"]`)).toHaveAttribute('aria-selected', 'false')
    await page.mouse.move(5, 5)
    await page.waitForTimeout(300)
    const before = await textWidth()
    expect(before.clipped, '抽屉收到下限后最长的名字应当被截断，否则这条量不到东西').toBe(true)
    await page.locator(`[data-el="${target}"]`).click()
    await expect(page.locator(`[data-el="${target}"]`)).toHaveAttribute('aria-selected', 'true')
    await page.mouse.move(5, 5)
    await page.waitForTimeout(300)
    const after = await textWidth()
    expect(after.clipped).toBe(true)
    expect(Math.abs(after.w - before.w), `${reducedMotion}：选中前 ${before.w}px / 选中后 ${after.w}px`).toBeLessThanOrEqual(0.6)
  }

  // G5：图层树的列间距与元素树相同
  const gapElement = await rows(page).first().evaluate((el) => getComputedStyle(el).columnGap)
  await page.locator('[data-rail="layers"]').click()
  const layerRows = page.locator('[data-tree="layers"] [data-layer]')
  await expect(page.locator('[data-tree="layers"]')).toHaveCount(1)
  await expect(layerRows.first()).toBeVisible()
  const gapLayer = await layerRows.first().evaluate((el) => getComputedStyle(el).columnGap)
  expect(gapLayer).toBe(gapElement)
})

test('分栏手柄：双击复位、拖动中 Esc 回滚、键盘步进与 Home/End、a11y 值域、拖拽态', async ({ app, page }) => {
  const a = await app()
  await page.goto(a.baseURL)
  // 左抽屉：随便开一个页签
  const rail = page.locator('[data-rail="assets"]')
  if ((await rail.getAttribute('aria-expanded')) !== 'true') await rail.click()
  const handle = page.locator('[data-resize-handle="left-drawer"]')
  await expect(handle).toHaveCount(1)
  await expect(handle).toBeVisible()
  const drawer = page.locator('[data-left-drawer]')
  const widthOf = async () => Math.round((await drawer.boundingBox())!.width)
  await expect.poll(widthOf).toBe(300)

  // a11y：值域齐全
  await expect(handle).toHaveAttribute('aria-valuenow', '300')
  await expect(handle).toHaveAttribute('aria-valuemax', '360')
  await expect(handle).toHaveAttribute('aria-orientation', 'vertical')

  // 键盘：End / Home / 步进
  await handle.focus()
  await page.keyboard.press('End')
  await expect(handle).toHaveAttribute('aria-valuenow', '360')
  await expect.poll(widthOf).toBe(360)
  await page.keyboard.press('Home')
  await expect(handle).toHaveAttribute('aria-valuenow', '280')
  await page.keyboard.press('ArrowRight')
  await expect(handle).toHaveAttribute('aria-valuenow', '296')

  // 双击复位
  await handle.dblclick()
  await expect(handle).toHaveAttribute('aria-valuenow', '300')
  await expect.poll(widthOf).toBe(300)

  // 拖动：变宽；拖拽态；拖动中 Esc 回滚
  const box = (await handle.boundingBox())!
  const y = box.y + 200
  const x = box.x + box.width / 2
  await page.mouse.move(x, y)
  await page.mouse.down()
  await page.mouse.move(x + 40, y, { steps: 6 })
  await expect(handle).toHaveAttribute('data-dragging', 'true')
  await expect(handle).toHaveAttribute('aria-valuenow', '340')
  // 拖拽态的发丝线是 2px accent，指针离开 8px 命中条也保持
  const lineW = await handle.locator('[data-drawer-resize-line]').evaluate((el) => parseFloat(getComputedStyle(el).width))
  expect(lineW).toBe(2)
  await page.keyboard.press('Escape')
  await expect(handle).toHaveAttribute('aria-valuenow', '300')
  await expect(handle).not.toHaveAttribute('data-dragging', /.*/)
  // 回滚后继续动鼠标 / 松手都不再改宽
  await page.mouse.move(x + 60, y, { steps: 3 })
  await page.mouse.up()
  await expect(handle).toHaveAttribute('aria-valuenow', '300')

  // 正常拖拽 + 松手：宽度保留
  await page.mouse.move(x, y)
  await page.mouse.down()
  await page.mouse.move(x + 30, y, { steps: 5 })
  await page.mouse.up()
  await expect(handle).toHaveAttribute('aria-valuenow', '330')
  await expect.poll(widthOf).toBe(330)
})

test('拖分栏手柄时按 Esc：宽度复原，且选中对象的上下文栏不被一并关掉', async ({ app, page }) => {
  const a = await app()
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(a.baseURL)
  const card = page.locator(CARD)
  await expect(card).toHaveCount(1)
  await card.dblclick({ timeout: 30_000 })
  // 稳定钩子：`data-context-back`（工作区上下文栏的「返回画布」）；所有目标先断言恰好 1 个再操作
  const back = page.locator('[data-context-back]')
  await expect(back).toHaveCount(1)
  await back.click()
  const panel = page.locator('[data-object-id]')
  await expect(panel).toHaveCount(1)
  await expect(panel).toBeVisible()
  await panel.click()
  const bar = page.locator('[data-context-bar]')
  await expect(bar).toHaveCount(1)
  await expect(bar).toBeVisible()

  const rail = page.locator('[data-rail="assets"]')
  await expect(rail).toHaveCount(1)
  if ((await rail.getAttribute('aria-expanded')) !== 'true') await rail.click()
  const handle = page.locator('[data-resize-handle="left-drawer"]')
  await expect(handle).toHaveCount(1)
  await expect(handle).toHaveAttribute('aria-valuenow', '300')
  await panel.click() // 点抽屉按钮可能改了选区；重新选中，保证工具条在
  await expect(bar).toBeVisible()

  const box = (await handle.boundingBox())!
  const x = box.x + box.width / 2
  const y = box.y + 300
  await page.mouse.move(x, y)
  await page.mouse.down()
  await page.mouse.move(x + 40, y, { steps: 6 })
  await expect(handle).toHaveAttribute('aria-valuenow', '340')
  await page.keyboard.press('Escape')
  await expect(handle).toHaveAttribute('aria-valuenow', '300')
  await page.mouse.up()
  await expect(handle).toHaveAttribute('aria-valuenow', '300')
  // 这一下 Esc 只属于拖动：选区还在，上下文栏仍可见
  await expect(bar).toBeVisible()
})

test('展开 / 折叠：子树高度过渡（收起期间 inert、播完卸载）；reduced-motion 直接切', async ({ app, page }) => {
  const a = await app()
  await openFigureTree(page, a.baseURL)
  const row = page.locator('[data-tree="elements"] [data-el="axes_0"]')
  await expect(row).toHaveCount(1)
  await expect(row).toHaveAttribute('aria-expanded', 'true')
  const branch = row.locator('xpath=following-sibling::li[@data-tree-branch][1]')
  await expect(branch).toHaveCount(1)
  const full = (await branch.boundingBox())!.height
  expect(full).toBeGreaterThan(60)

  // 过渡中点测量，与帧率无关：按键**之前**在页面里装 MutationObserver，branch 一翻到目标状态
  // （收起 = closed、展开 = open）就在同一个微任务里 getAnimations() → 暂停 → 定位到 50% → 量高度 / 状态 /
  // inert / aria-hidden → 放行继续播。逐帧 rAF 采样在慢机器上（Windows WebKit 500 ms 只渲 4 帧）会把
  // 90 ms 的退场整个漏在两帧之间——这里不看帧，只看动画本身。
  type Probe = { h: number; state: string | null; inert: boolean; ariaHidden: boolean; names: string[]; progress: number[] }
  const arm = (want: 'open' | 'closed') =>
    page.evaluate((want) => {
      const w = window as unknown as { __probe?: Promise<Probe | null> }
      const root = document.querySelector('[data-tree="elements"]')!
      const row = root.querySelector('[data-el="axes_0"]')!
      w.__probe = new Promise<Probe | null>((resolve) => {
        const mo = new MutationObserver(() => {
          const b = row.nextElementSibling as HTMLElement | null
          if (!b || !b.hasAttribute('data-tree-branch') || b.getAttribute('data-tree-branch') !== want) return
          const anims = b.getAnimations()
          if (anims.length === 0) return // 样式还没解析出动画：等下一批变更
          const progress: number[] = []
          for (const a of anims) {
            a.pause()
            const t = a.effect!.getComputedTiming()
            a.currentTime = Number(t.endTime) / 2
            progress.push(Number(t.duration))
          }
          resolve({
            h: b.getBoundingClientRect().height,
            state: b.getAttribute('data-tree-branch'),
            inert: b.hasAttribute('inert'),
            ariaHidden: b.getAttribute('aria-hidden') === 'true',
            names: anims.map((a) => (a as CSSAnimation).animationName ?? ''),
            progress,
          })
          for (const a of anims) a.play()
          mo.disconnect()
        })
        mo.observe(root, { subtree: true, childList: true, attributes: true })
        // 保底：2 s 内没出现动画就交 null，断言里会带着这个现象失败（不是挂到超时）
        setTimeout(() => {
          mo.disconnect()
          resolve(null)
        }, 2000)
      })
    }, want)
  const probed = () => page.evaluate(() => (window as unknown as { __probe: Promise<Probe | null> }).__probe)

  await page.emulateMedia({ reducedMotion: 'no-preference' })
  await row.focus()
  await arm('closed')
  await page.keyboard.press('ArrowLeft')
  const mid = await probed()
  expect(mid, '收起时 branch 上要有 CSS 动画').not.toBeNull()
  expect(mid!.names.some((n) => n.includes('reveal-out')), `收起播的是退场动画：${JSON.stringify(mid)}`).toBe(true)
  expect(mid!.state).toBe('closed')
  expect(mid!.inert, '收起期间容器 inert').toBe(true)
  expect(mid!.ariaHidden, '收起期间容器 aria-hidden').toBe(true)
  expect(mid!.h > 0 && mid!.h < full, `收起要有中间高度（full=${full}）：${JSON.stringify(mid)}`).toBe(true)
  await expect(branch, '播完卸载').toHaveCount(0)

  await arm('open')
  await page.keyboard.press('ArrowRight')
  const up = await probed()
  expect(up, '展开时 branch 上要有 CSS 动画').not.toBeNull()
  expect(up!.names.some((n) => n.includes('reveal-in')), `展开播的是进场动画：${JSON.stringify(up)}`).toBe(true)
  expect(up!.inert, '展开的容器不 inert').toBe(false)
  expect(up!.h > 0 && up!.h < full * 0.98, `展开要有中间高度（full=${full}）：${JSON.stringify(up)}`).toBe(true)
  await expect.poll(async () => Math.abs(((await branch.boundingBox())?.height ?? 0) - full), { message: '展开后回到完整高度' }).toBeLessThanOrEqual(1)
  await expect(branch).not.toHaveAttribute('inert', /.*/)
  await expect(branch).toHaveAttribute('data-tree-branch', 'open')

  // reduced-motion：直接切，不留退场帧——任何一批变更里都不许看到 closed 的保活 branch
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await row.focus()
  await page.evaluate(() => {
    const w = window as unknown as { __closedSeen?: boolean }
    const root = document.querySelector('[data-tree="elements"]')!
    const row = root.querySelector('[data-el="axes_0"]')!
    w.__closedSeen = false
    const mo = new MutationObserver(() => {
      const b = row.nextElementSibling
      if (b?.hasAttribute('data-tree-branch') && b.getAttribute('data-tree-branch') === 'closed') w.__closedSeen = true
    })
    mo.observe(root, { subtree: true, childList: true, attributes: true })
  })
  await page.keyboard.press('ArrowLeft')
  await expect(branch).toHaveCount(0)
  await page.waitForTimeout(150) // 比退场时长（90 ms）长：真有保活帧此时早已出现过
  expect(await page.evaluate(() => (window as unknown as { __closedSeen: boolean }).__closedSeen), 'reduce 下不留退场帧').toBe(false)
})
