import type { Page } from '@playwright/test'
import { expect, test, waitForDocumentReady } from './fixtures'

/**
 * 素材卡 / 属性栏底部（2026-10-16，UI 2.0 §07 / §08 / §09）。语言无关写法（chromium 与 chromium-en 各走一遍）。
 *
 * - 素材卡只有一个「＋」，在缩略图右上角（不压图里的文字），悬停才出现；
 * - 选中不同素材时抽屉里下方的东西不动（页脚与接入说明块已去掉）；
 * - 属性栏：选中面板后「改图里的内容」下面没有常驻死空白；层级行带「第 n 层」；「换一张图」在 ⋯ 里。
 */

async function openAssets(page: Page, baseURL: string) {
  await page.goto(baseURL)
  await waitForDocumentReady(page)
  const rail = page.locator('[data-rail="assets"]')
  if ((await rail.getAttribute('aria-expanded')) !== 'true') await rail.click()
  await expect(page.locator('li[data-card]').first()).toBeVisible({ timeout: 30_000 })
}

test('素材卡：只有一个「＋」，在缩略图右上角，悬停才出现、不压预览中部', async ({ app, page }) => {
  const a = await app()
  await openAssets(page, a.baseURL)
  const card = page.locator('li[data-card]').first()
  const add = card.locator('[data-card-add]')
  await expect(add).toHaveCount(1)
  await expect(card.locator('[data-card-actions]')).toHaveCount(0)

  // 不悬停：透明
  await page.mouse.move(2, 2)
  await expect.poll(() => add.evaluate((el) => getComputedStyle(el).opacity)).toBe('0')
  await card.hover()
  await expect.poll(() => add.evaluate((el) => getComputedStyle(el).opacity)).toBe('1')

  const preview = (await card.locator('img').first().evaluateHandle((img) => img.parentElement!)).asElement()!
  const p = (await preview.boundingBox())!
  const b = (await add.boundingBox())!
  // 24px 的圆
  expect(Math.round(b.width)).toBe(24)
  expect(Math.round(b.height)).toBe(24)
  // 在缩略图里面，且落在右上角那一块（右 1/3 宽、上 1/2 高之内）——不是原来的右下角，也不压中部
  expect(b.x).toBeGreaterThanOrEqual(p.x)
  expect(b.y).toBeGreaterThanOrEqual(p.y)
  expect(b.x + b.width).toBeLessThanOrEqual(p.x + p.width)
  expect(b.x).toBeGreaterThan(p.x + p.width * 0.6)
  expect(b.y + b.height).toBeLessThan(p.y + p.height * 0.5)
})

test('素材卡：选中不同的卡，抽屉里其它东西的位置不动，没有页脚', async ({ app, page }) => {
  const a = await app()
  await openAssets(page, a.baseURL)
  const cards = page.locator('li[data-card]')
  expect(await cards.count()).toBeGreaterThanOrEqual(2)
  const probe = () =>
    page.evaluate(() => {
      const rect = (el: Element | null) => {
        if (!el) return null
        const r = el.getBoundingClientRect()
        return { x: r.x, y: r.y, w: r.width, h: r.height }
      }
      return {
        cards: [...document.querySelectorAll('li[data-card]')].map((c) => rect(c)),
        scripts: rect(document.querySelector('#asset-scripts-section')),
        footer: document.querySelectorAll('[data-asset-footer]').length,
        notice: document.querySelectorAll('[data-capability-notice]').length,
      }
    })
  // 先等抽屉安顿（素材扫描 / 状态芯片到达之后才是静止布局，否则量到的是加载中的那一帧）
  await page.waitForTimeout(1500)
  const base = await probe()
  expect(base.footer).toBe(0)
  for (let i = 0; i < Math.min(3, await cards.count()); i++) {
    await cards.nth(i).click()
    await page.waitForTimeout(250)
    expect(await probe(), `选中第 ${i} 张后布局变了`).toEqual(base)
  }
})

test('素材卡的两个动作有可聚焦的真按钮：Tab 序列里到得了，标签正确，点击与快捷键同效', async ({ app, page }) => {
  const a = await app()
  await openAssets(page, a.baseURL)
  const add = page.locator('[data-asset-add]')
  const open = page.locator('[data-asset-open]')
  await expect(add).toBeDisabled()
  await expect(open).toBeDisabled()
  const card = page.locator('li[data-card]').first()
  await card.focus() // 焦点落在卡上 = 选中它
  await expect(add).toBeEnabled()
  // 从卡片往回 Shift+Tab，应当能走到这两颗钮（它们在 listbox 之前的标题行里）
  const reached = new Set<string>()
  for (let i = 0; i < 12 && reached.size < 2; i++) {
    await page.keyboard.press('Shift+Tab')
    const hit = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null
      if (el?.hasAttribute('data-asset-add')) return 'add'
      if (el?.hasAttribute('data-asset-open')) return 'open'
      return null
    })
    if (hit) reached.add(hit)
  }
  expect([...reached].sort()).toEqual(['add', 'open'])
  await expect(add).toHaveAttribute('aria-label', /./)
  expect(await add.getAttribute('aria-label')).not.toBe(await open.getAttribute('aria-label'))
  // 点击 = Shift+Enter 的效果：这张图进了排版
  await add.click()
  await expect(page.locator('[data-object-id]')).toHaveCount(1, { timeout: 30_000 })
})

test('素材卡：右键菜单有全部动作，双击 = 编辑原图', async ({ app, page }) => {
  const a = await app()
  await openAssets(page, a.baseURL)
  const card = page.locator('li[data-card]').first()
  await card.click({ button: 'right' })
  const menu = page.getByRole('menu')
  await expect(menu).toBeVisible()
  expect(await menu.getByRole('menuitem').count()).toBeGreaterThanOrEqual(2)
  await page.keyboard.press('Escape')
  await expect(menu).toHaveCount(0)
  await card.dblclick()
  await expect(page.locator('[data-workspace-context-bar]')).toHaveAttribute('data-workspace-mode', 'fast_edit', {
    timeout: 60_000,
  })
})

test('属性栏：层级带值、「换一张图」在 ⋯ 里、「改图里的内容」下没有死空白', async ({ app, page }) => {
  const a = await app()
  await openAssets(page, a.baseURL)
  const card = page.locator('li[data-card]').first()
  await card.focus()
  await page.keyboard.press('Shift+Enter')
  const obj = page.locator('[data-object-id]').first()
  await expect(obj).toBeVisible({ timeout: 30_000 })
  await page.waitForTimeout(800)
  await obj.click()

  const insp = page.locator('[data-inspector-panel]')
  await expect(insp).toBeVisible({ timeout: 30_000 })
  // 层级行：带「n / m」的值（中英都含两个数字）
  const zrow = insp.locator('[data-fold="zorder"]')
  await expect(zrow.locator('[data-summary-value]')).toHaveText(/\d+\D+\d+/)
  // 死空白：进图内编辑按钮到下一节标题之间 ≤ 20px（原先 ≈ 38px 的常驻空白）
  const gap = await insp.evaluate((root) => {
    const status = root.querySelector('[data-panel-engine-status]')
    if (!status) return null
    const sec = status.closest('section')!
    const btn = sec.querySelector('button')!
    const next = sec.nextElementSibling!
    return next.getBoundingClientRect().top - btn.getBoundingClientRect().bottom
  })
  expect(gap, '找不到进图内编辑那一节').not.toBeNull()
  expect(gap!).toBeLessThanOrEqual(20)

  // 「换一张图」不在属性表里，在 ⋯ 菜单里（键盘可达）
  await expect(insp.locator('[data-fold="replace"]')).toHaveCount(0)
  const more = insp.locator('[data-identity-more]')
  await more.focus()
  await page.keyboard.press('Enter')
  const item = page.locator('[data-replace-picture]')
  await expect(item).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(item).toHaveCount(0)

})

test('素材抽屉的搜索框按图 + 脚本合计算：示例库 3 张图 + 3 个脚本 = 6，显示', async ({ app, page }) => {
  const a = await app()
  await openAssets(page, a.baseURL)
  // 同一个框过滤图与脚本两个区；合计 ≥ 6 才摆出来（阈值的少于 6 隐藏在 vitest 里钉）
  expect(await page.locator('li[data-card]').count()).toBe(3)
  await expect(page.locator('[data-left-drawer] input')).toHaveCount(1)
})
