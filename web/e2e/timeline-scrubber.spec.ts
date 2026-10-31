import { expect, test } from './fixtures'

/**
 * 时间线页签顶上的版本竖线条（ADR 0101）的真浏览器闭环：
 *
 *   存三个命名节点 → 打开时间线 → 竖线条一个节点一根 → 鼠标划过出浮卡（只用存好的缩略图，
 *   不出图）→ 点下去打开那一行旁边的对比卡片 → 键盘 ←/回车同样打开。
 *
 * jsdom 那一侧（`timelineScrubber.test.ts`、`VersionDialog.test.tsx`）看不见指针几何：
 * 划到哪一根取决于真实布局，浮卡跟手也只有真浏览器排得出来。
 */

type Locator = import('@playwright/test').Locator

async function only(loc: Locator, timeout?: number): Promise<Locator> {
  await expect(loc).toHaveCount(1, timeout ? { timeout } : undefined)
  return loc
}

test(
  '版本竖线条：划过出浮卡、点下去开对比卡片、键盘也能用',
  { tag: '@feature:edit.timeline-scrubber' },
  async ({ app, page }) => {
    test.setTimeout(240_000)
    const a = await app()
    await page.goto(a.baseURL)

    await (await only(page.locator('[data-card="Fig1_kinetics.pdf"]'), 30_000)).dblclick()
    await (await only(page.locator('[data-context-back]'))).click()

    // 三个命名节点：命名不按内容去重，内容一样也各记一个
    for (const name of ['甲', '乙', '丙']) {
      await page.keyboard.press('ControlOrMeta+Alt+S')
      const quick = await only(page.locator('[data-timeline-quick-name-input]'))
      await quick.fill(name)
      await quick.press('Enter')
      await expect(quick).toHaveCount(0)
    }

    await (await only(page.locator('[data-timeline-button]'))).click()
    const drawer = await only(page.locator('[data-timeline-panel]'))
    const nodes = drawer.locator('[data-timeline-node]')
    await expect.poll(() => nodes.count(), { timeout: 30_000 }).toBeGreaterThanOrEqual(3)

    // 一个节点一根
    const scrubber = await only(drawer.locator('[data-timeline-scrubber]'))
    const ticks = scrubber.locator('[data-timeline-scrubber-tick]')
    await expect.poll(async () => ticks.count()).toBe(await nodes.count())

    // 划过：浮卡出现，说的是指着的那一根
    const box = (await scrubber.boundingBox())!
    await page.mouse.move(box.x + 2, box.y + box.height / 2)
    await page.mouse.move(box.x + box.width - 2, box.y + box.height / 2, { steps: 8 })
    const peek = await only(page.locator('[data-timeline-scrubber-peek]'))
    const last = await ticks.last().getAttribute('data-timeline-scrubber-tick')
    await expect(peek).toHaveAttribute('data-timeline-scrubber-peek', last!)

    // 点下去：那一行旁边的对比卡片
    await page.mouse.down()
    await page.mouse.up()
    await expect(await only(page.locator(`[data-timeline-card="${last}"]`))).toBeVisible()

    // 移开：浮卡收起
    await page.mouse.move(box.x + box.width / 2, box.y + 400)
    await expect(page.locator('[data-timeline-scrubber-peek]')).toHaveCount(0)

    // 键盘：聚焦后 Home 移到最早那一根，回车打开它的卡片
    await scrubber.focus()
    await page.keyboard.press('Home')
    const first = await ticks.first().getAttribute('data-timeline-scrubber-tick')
    await expect(scrubber).toHaveAttribute('aria-valuenow', '1')
    await page.keyboard.press('Enter')
    await expect(await only(page.locator(`[data-timeline-card="${first}"]`))).toBeVisible()
  },
)
