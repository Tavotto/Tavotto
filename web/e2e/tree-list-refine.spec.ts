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
  const textWidth = () =>
    page.evaluate((gid) => {
      const spans = document.querySelectorAll(`[data-el="${CSS.escape(gid)}"] [data-tree-label]`)
      if (spans.length !== 1) throw new Error(`标签钩子应恰好 1 个，实际 ${spans.length}`)
      const span = spans[0]
      const r = document.createRange()
      r.selectNodeContents(span)
      return r.getBoundingClientRect().width
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
    await page.locator(`[data-el="${target}"]`).click()
    await expect(page.locator(`[data-el="${target}"]`)).toHaveAttribute('aria-selected', 'true')
    await page.mouse.move(5, 5)
    await page.waitForTimeout(300)
    const after = await textWidth()
    expect(Math.abs(after - before), `${reducedMotion}：选中前 ${before}px / 选中后 ${after}px`).toBeLessThanOrEqual(0.6)
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
