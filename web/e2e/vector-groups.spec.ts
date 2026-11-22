import type { Page } from '@playwright/test'
import { expect, test, waitForDocumentReady } from './fixtures'

/**
 * 嵌套组、组内编辑与剪切蒙版（ADR 0191）：
 * ① 两层嵌套组：点任何一个成员选中整个外层组；双击一层层进到组里（组外的东西淡出），Esc 一层层退出；
 * ② 剪切蒙版：大矩形上盖一个小矩形，⌘7 建立后，大矩形只有蒙版范围内点得中、画面按蒙版裁；⌥⌘7 释放后恢复。
 * 判据：选区个数、组外淡出层的个数、点在蒙版内 / 外的命中结果，不是文档字段。
 */

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    try {
      const key = 'tavotto.ui'
      const saved = JSON.parse(localStorage.getItem(key) || '{}')
      localStorage.setItem(key, JSON.stringify({ ...saved, prefsVersion: 2, snapEnabled: false }))
    } catch {
      /* 存储不可用时照常跑 */
    }
  })
})

async function openCanvas(page: Page, baseURL: string) {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(baseURL)
  await expect(page.locator('[data-page-sheet]')).toBeVisible({ timeout: 30_000 })
  await waitForDocumentReady(page)
  await page.waitForTimeout(500)
  return (await page.locator('[data-page-sheet]').boundingBox())!
}

/** 画一个带填充的矩形（填充让它整块点得中）；坐标相对页面纸左上角 */
async function drawFilledRect(page: Page, sheet: { x: number; y: number }, x0: number, y0: number, x1: number, y1: number) {
  await page.keyboard.press('r')
  await page.mouse.move(sheet.x + x0, sheet.y + y0)
  await page.mouse.down()
  await page.mouse.move(sheet.x + x1, sheet.y + y1, { steps: 20 })
  await page.mouse.up()
  await page.waitForTimeout(250)
  await page.locator('[data-inspector-panel] [data-effect-add="fill"]').click()
}

/** 按住 Shift 点一下（加选） */
async function shiftClick(page: Page, x: number, y: number) {
  await page.keyboard.down('Shift')
  await page.mouse.click(x, y)
  await page.keyboard.up('Shift')
}

/** 屏幕上点一下，返回选区里有几个对象（0 = 没选中任何东西） */
async function clickAndCount(page: Page, x: number, y: number): Promise<number> {
  await page.mouse.click(x, y)
  await page.waitForTimeout(150)
  const multi = page.locator('[data-selection-count]')
  if (await multi.count()) return Number(await multi.first().getAttribute('data-selection-count'))
  return (await page.locator('[data-context-bar]').count()) ? 1 : 0
}

/** 屏幕上这一点最上层是不是画布对象（被裁掉的部分不接收指针，点在上面落到空白） */
const hitsObject = (page: Page, x: number, y: number) =>
  page.evaluate(([px, py]) => !!document.elementFromPoint(px, py)?.closest('[data-canvas-stage] [data-object-id]'), [x, y])

test.describe('嵌套组与剪切蒙版', () => {
  test(
    '两层嵌套组：点成员选中整组；双击一层层进组（组外淡出）、Esc 一层层退出',
    { tag: ['@feature:canvas.nested-groups-clip'] },
    async ({ app, page }) => {
      const a = await app()
      const sheet = await openCanvas(page, a.baseURL)
      const at = (x: number, y: number) => ({ x: sheet.x + x, y: sheet.y + y })

      // 三个互不重叠的矩形 A / B / C
      await drawFilledRect(page, sheet, 80, 100, 180, 180)
      await drawFilledRect(page, sheet, 220, 100, 320, 180)
      await drawFilledRect(page, sheet, 360, 100, 460, 180)
      await page.keyboard.press('v')
      const [A, B, C] = [at(130, 140), at(270, 140), at(410, 140)]

      // A + B 成内层组；内层组 + C 成外层组（⌘G）
      await page.mouse.click(A.x, A.y)
      await shiftClick(page, B.x, B.y)
      await page.keyboard.press('ControlOrMeta+g')
      await page.mouse.click(A.x, A.y)
      await shiftClick(page, C.x, C.y)
      await page.keyboard.press('ControlOrMeta+g')

      // 点任何一个成员 = 选中整个外层组（三个对象）
      await page.mouse.click(300, 700) // 先点空白清选区
      expect(await clickAndCount(page, A.x, A.y)).toBe(3)
      expect(await clickAndCount(page, C.x, C.y)).toBe(3)

      // 双击进外层组：C 与内层组都是它的直接成员，没有组外的东西；再双击 A 进内层组：C 在组外，淡出
      await expect(page.locator('[data-isolation-outside]')).toHaveCount(0)
      await page.mouse.dblclick(A.x, A.y)
      await expect(page.locator('[data-isolation-outside]')).toHaveCount(0)
      await page.mouse.dblclick(A.x, A.y)
      await expect(page.locator('[data-isolation-outside]')).toHaveCount(1) // C
      // 组内编辑里点 A 只选 A（点 B 只选 B），不再是整组
      expect(await clickAndCount(page, A.x, A.y)).toBe(1)

      // Esc 退一层：C 不再淡出；再退一层、再点 = 回到整组（3 个）
      await page.keyboard.press('Escape')
      await expect(page.locator('[data-isolation-outside]')).toHaveCount(0)
      await page.keyboard.press('Escape')
      await page.mouse.click(300, 700)
      expect(await clickAndCount(page, B.x, B.y)).toBe(3)
    },
  )

  test(
    '剪切蒙版：⌘7 后大矩形只在蒙版范围内点得中（画面按蒙版裁）；⌥⌘7 释放后恢复',
    { tag: ['@feature:canvas.nested-groups-clip'] },
    async ({ app, page }) => {
      const a = await app()
      const sheet = await openCanvas(page, a.baseURL)
      const at = (x: number, y: number) => ({ x: sheet.x + x, y: sheet.y + y })

      // 大矩形（内容）+ 盖在它左半边上的小矩形（蒙版）
      await drawFilledRect(page, sheet, 80, 80, 420, 300)
      await drawFilledRect(page, sheet, 200, 200, 80, 80) // 从内往左上拖：起点不落在前一个矩形的手柄上
      const inside = at(140, 140) // 蒙版内
      const outside = at(360, 260) // 大矩形内、蒙版外

      await page.keyboard.press('v')
      // 建立前：大矩形整块都接收指针
      expect(await hitsObject(page, outside.x, outside.y)).toBe(true)

      await page.keyboard.press('Escape')
      await page.keyboard.press('ControlOrMeta+a')
      await page.keyboard.press('ControlOrMeta+7')
      // 裁剪层出现，且真的带了裁剪路径
      const clip = page.locator('[data-clip-by]')
      await expect(clip).toHaveCount(1)
      await expect.poll(() => clip.evaluate((e) => getComputedStyle(e).clipPath)).not.toBe('none')

      // 建立后：蒙版外的那块不再接收指针（被裁掉了），蒙版内还在
      expect(await hitsObject(page, outside.x, outside.y)).toBe(false)
      expect(await hitsObject(page, inside.x, inside.y)).toBe(true)

      // 释放（⌥⌘7）：裁剪层没了，蒙版外那块又接收指针
      await page.keyboard.press('ControlOrMeta+Alt+7')
      await expect(page.locator('[data-clip-by]')).toHaveCount(0)
      expect(await hitsObject(page, outside.x, outside.y)).toBe(true)
    },
  )
})
