import { expect, test, type RunningApp } from './fixtures'
import type { Page } from '@playwright/test'

/**
 * 导航一组（审计 T01 / T05 / T50）里**只有真布局引擎回答得了**的三条验收：
 *
 *   T50「所有快捷键说明完整可读」—— jsdom 量不出「这一行有没有被切掉」，
 *        `components/ShortcutHelp.test.tsx` 只能守住「没挂截断类」；
 *   T05「长名称不挤走菜单与关闭按钮」—— 挤没挤走是像素问题；
 *   T01「每次都能指出当前对象和修改范围」—— 一条上下文栏，一个返回入口，
 *        并且它不能把画布撑破。
 *
 * 断言全部打在结构与几何上，不比截图。
 */

async function openFigure(page: Page, a: RunningApp) {
  await page.goto(a.baseURL)
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 60_000 })
}

/** 展开元素树里所有收起的分组（分组头点行即展开，元素行点行首小三角） */
async function expandTree(page: Page) {
  await page.locator('[role="treeitem"]').first().waitFor({ timeout: 30_000 })
  for (let i = 0; i < 20; i++) {
    const rows = page.locator('[role="treeitem"][aria-expanded="false"]')
    if (!(await rows.count())) break
    const row = rows.first()
    const chevron = row.getByRole('button', { name: '展开', exact: true })
    try {
      if (await chevron.count()) await chevron.first().click({ timeout: 4000 })
      else await row.click({ timeout: 4000 })
    } catch {
      break
    }
    await page.waitForTimeout(120)
  }
}

/** 元素的可见文字有没有被自己的盒子切掉（换行撑高不算切，溢出才算） */
async function clipped(page: Page, selector: string): Promise<string[]> {
  return page.evaluate((sel) => {
    const out: string[] = []
    for (const el of Array.from(document.querySelectorAll(sel))) {
      const e = el as HTMLElement
      if (e.scrollWidth > e.clientWidth + 1 || e.scrollHeight > e.clientHeight + 1) {
        out.push(`${(e.textContent ?? '').slice(0, 40)} sw=${e.scrollWidth}/cw=${e.clientWidth} sh=${e.scrollHeight}/ch=${e.clientHeight}`)
      }
    }
    return out
  }, selector)
}

test('T50：快捷键说明一条都没有被切掉，且搜索能收窄', async ({ app, page }) => {
  const a = await app()
  await page.goto(a.baseURL)
  await page.locator('[data-canvas-stage]').waitFor({ timeout: 60_000 })

  await page.keyboard.press('?')
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()

  const rows = page.locator('[data-shortcut-row]')
  const total = await rows.count()
  expect(total).toBeGreaterThan(15)

  // 说明那一列：整句都在盒子里（换行是允许的，切掉不行）
  expect(await clipped(page, '[data-shortcut-row] > span:last-child')).toEqual([])
  // 键位那一列同理——它是定宽的，最长的组合键也得放得下
  expect(await clipped(page, '[data-shortcut-row] > span:first-child')).toEqual([])
  // 对话框自己不横向溢出
  const overflow = await page.evaluate(() => {
    const d = document.querySelector('[role="dialog"]') as HTMLElement
    return d.scrollWidth > d.clientWidth + 1
  })
  expect(overflow).toBe(false)

  await dialog.getByRole('textbox').fill('撤销')
  await expect(rows).toHaveCount(1)
  await expect(page.locator('[data-shortcut-group]')).toHaveCount(1)
})

test('T05：长画布名不挤走行尾的菜单按钮', async ({ app, page }) => {
  const a = await app()
  await page.goto(a.baseURL)
  await page.locator('[data-canvas-stage]').waitFor({ timeout: 60_000 })

  // 左轨「结构」旁边那一栏是画布列表：先切过去
  await page.getByRole('navigation').getByRole('button', { name: '画布' }).click()
  const list = page.getByRole('list', { name: '画布列表' })
  await expect(list).toBeVisible()

  const long = '超长画布名'.repeat(12)
  const row = list.locator('li').first()
  await row.getByRole('button', { name: /^打开画布/ }).dblclick()
  await row.getByRole('textbox').fill(long)
  await row.getByRole('textbox').press('Enter')

  const menuBtn = row.getByRole('button', { name: /的操作$/ })
  const rowBox = await row.boundingBox()
  const btnBox = await menuBtn.boundingBox()
  expect(rowBox).not.toBeNull()
  expect(btnBox).not.toBeNull()
  // 菜单按钮整颗都还在行里
  expect(btnBox!.x + btnBox!.width).toBeLessThanOrEqual(rowBox!.x + rowBox!.width + 1)
  expect(btnBox!.width).toBeGreaterThan(10)
  // 缩略图也没被挤扁
  const thumb = await row.locator('[data-canvas-thumb]').boundingBox()
  expect(thumb!.width).toBeGreaterThan(40)
  // 列表整体不横向溢出
  expect(
    await page.evaluate(() => {
      const ul = document.querySelector('ul[aria-label]') as HTMLElement
      return ul.scrollWidth > ul.clientWidth + 1
    }),
  ).toBe(false)
})

test('T01：一条上下文栏、一个返回入口，回来时画布不意外移动', async ({ app, page }) => {
  const a = await app()
  await openFigure(page, a)

  const bar = page.locator('[data-workspace-context-bar]')
  await expect(bar).toHaveCount(1)
  await expect(bar).toBeVisible()

  // 整屏可见的「返回画布」只有这一个（此前顶栏徽章、浮动条、右栏各有一个）
  await expect(page.getByRole('button', { name: /返回画布/ })).toHaveCount(1)

  // 上下文栏放得进画布，不横向溢出
  const stage = (await page.locator('[data-canvas-stage]').boundingBox())!
  const box = (await bar.boundingBox())!
  expect(box.x).toBeGreaterThanOrEqual(stage.x - 1)
  expect(box.x + box.width).toBeLessThanOrEqual(stage.x + stage.width + 1)

  // 面包屑：图名在，选中一个元素后第二级说出那个对象，且不出现内部标识
  await expect(bar).toContainText('Fig1_kinetics')
  await expandTree(page)
  await page.getByRole('treeitem', { name: /^标题/ }).first().click()
  await expect(bar.locator('[data-context-object]')).toBeVisible()
  await expect(bar).not.toContainText('axes_0')

  /* ---- 验收：切换模式时画布不意外移动 ---- */
  const world = page.locator('[data-world-transform]')
  await page.getByRole('button', { name: /返回画布/ }).click()
  await expect(bar).toHaveCount(0)
  await page.waitForTimeout(400) // 视口补间 180ms：不等就量在半路上

  // **先把视口摆成「不是把那张图居中」的样子**。不摆的话这条判据恒等成立：
  // 退化实现（回来就把面板挪到视口中央）量到的与「面板本来就在中央」的那一片
  // 逐位相同——第一版就是这么放走变异的。
  const centered = await world.evaluate((e) => getComputedStyle(e).transform)
  await page.mouse.move(stage.x + stage.width / 2, stage.y + stage.height / 2)
  await page.mouse.wheel(220, 170)
  await page.waitForTimeout(400)
  const layoutView = await world.evaluate((e) => getComputedStyle(e).transform)
  expect(layoutView).not.toBe(centered) // 先证明尺子是活的：平移真的发生了

  // 再进一次快速编辑：那一屏按图自己的图幅框住，视口必然变。
  // 回排版时焦点救援把左栏交给了「结构」，先切回素材（`lib/focusRescue.ts`）
  await page.getByRole('navigation').getByRole('button', { name: '素材', exact: true }).click()
  await page.getByText('Fig1_kinetics.pdf').dblclick()
  await expect(bar).toHaveCount(1)
  await page.waitForTimeout(400)
  expect(await world.evaluate((e) => getComputedStyle(e).transform)).not.toBe(layoutView)

  // 回来：还是进去之前那一片，一个像素都不差
  await page.getByRole('button', { name: /返回画布/ }).click()
  await expect(bar).toHaveCount(0)
  await page.waitForTimeout(400)
  expect(await world.evaluate((e) => getComputedStyle(e).transform)).toBe(layoutView)
})
