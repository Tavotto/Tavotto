import { expect, test } from './fixtures'

/**
 * 顶栏在窄窗口下元素两两不重叠（#677 集成时 600 宽实测：保存状态与时间线的时钟 / 书签钮
 * 叠在一起，左段内容溢出到中间的工具组上；ADR 0101 §8）。
 *
 * 量的是**画出来的盒子**：顶栏里所有可见的按钮与保存状态，两两求交。jsdom 量不到几何，
 * 只能在真浏览器里量。要有内容（保存状态只在有内容时出现）。
 */
async function overlaps(page: import('@playwright/test').Page) {
  return page.evaluate(() => {
    // 量的集合是「顶栏里所有可见的按钮 + 保存状态」：按元素类型**枚举全体**，不是去定位
    // 某一个（那才需要 data-* 钩子）；可达名只用来在失败信息里说是哪两颗撞了
    const header = document.querySelector('[data-topbar]')!
    const els = [...header.querySelectorAll<HTMLElement>('button, [data-save-state]')].filter((el) => {
      const r = el.getBoundingClientRect()
      const cs = getComputedStyle(el)
      return r.width > 0.5 && r.height > 0.5 && cs.visibility !== 'hidden' && !el.closest('[aria-hidden="true"]')
    })
    // 嵌在另一个被量元素里面的不算两件（例如按钮里的状态）
    const top = els.filter((el) => !els.some((o) => o !== el && o.contains(el)))
    const name = (el: HTMLElement) =>
      el.getAttribute('aria-label') || el.getAttribute('data-save-state') !== null ? `${el.getAttribute('aria-label') ?? 'save-state'}` : (el.textContent ?? '').trim().slice(0, 12)
    const boxes = top.map((el) => ({ n: name(el), r: el.getBoundingClientRect() }))
    const hits: string[] = []
    for (let i = 0; i < boxes.length; i++)
      for (let j = i + 1; j < boxes.length; j++) {
        const a = boxes[i].r
        const b = boxes[j].r
        const w = Math.min(a.right, b.right) - Math.max(a.left, b.left)
        const h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top)
        if (w > 0.5 && h > 0.5) hits.push(`${boxes[i].n} ∩ ${boxes[j].n} (${w.toFixed(1)}px)`)
      }
    const off = boxes.filter((b) => b.r.left < -0.5 || b.r.right > innerWidth + 0.5).map((b) => b.n)
    return { count: boxes.length, hits, off }
  })
}

for (const width of [600, 780, 920, 1024]) {
  test(`顶栏 ${width} 宽：按钮与保存状态两两不重叠、不出视口`, async ({ app, page }) => {
    await page.setViewportSize({ width, height: 700 })
    const a = await app()
    await page.goto(a.baseURL)
    // 有内容才有保存状态：放一张图上画布，回到排版（定位认 data-* 钩子，先断言恰好一个）
    await expect(page.locator('[data-topbar]')).toHaveCount(1)
    const card = page.locator('[data-card="Fig1_kinetics.pdf"]')
    await expect(card).toHaveCount(1, { timeout: 30_000 })
    await card.dblclick()
    const back = page.locator('[data-context-back]')
    await expect(back).toHaveCount(1)
    await back.click()
    await expect(page.locator('[data-topbar] [data-save-state]')).toHaveCount(1)
    await expect(page.locator('[data-topbar] [data-timeline-button]')).toHaveCount(1)
    await expect(page.locator('[data-topbar] [data-timeline-button]')).toBeVisible()
    // 书签钮已并入时间线抽屉（2026-10-01）：顶栏不再有它
    await expect(page.locator('[data-topbar] [data-timeline-name-button]')).toHaveCount(0)
    const m = await overlaps(page)
    expect(m.count, JSON.stringify(m)).toBeGreaterThan(8)
    expect(m.hits, JSON.stringify(m)).toEqual([])
    expect(m.off, JSON.stringify(m)).toEqual([])
  })
}
