import path from 'node:path'
import { expect, test } from './fixtures'

/**
 * 排版时间线（ADR 0101）的真浏览器闭环：
 *
 *   编辑 → 等自动节点 → 命名一个节点「投稿前」→ 继续改 → 在时间线预览「投稿前」
 *   （画布不动）→ 恢复 → 画布回到那一刻 → 时间线里出现「恢复前」节点 → 用它恢复回来。
 *
 * jsdom 那一侧（`VersionDialog.test.tsx`）看得见「调了哪个接口」，看不见三件只有
 * 真浏览器 + 真后端才回答得了的事：节点真的落了盘、拍的缩略图真的合成得出来并且
 * 挂得上（canvas 编码 / 不被 taint）、预览遮罩真的盖住了画布而没有改它。
 *
 * 自动节点的间隔用 `__TAVOTTO_TIMELINE_TIMING__` **注入**调小，产品默认值
 * （15 s 停顿 / 2 分钟间隔）一个字不改。
 */

const SHOTS = process.env.TAVOTTO_E2E_SHOTS

/** 画布上的文字对象（不含时间线预览里画的那一份） */
const canvasText = (page: import('@playwright/test').Page, text: string) =>
  page.locator('[data-canvas-stage]').getByText(text, { exact: true })

async function addText(page: import('@playwright/test').Page, text: string, x: number, y: number) {
  await page.getByRole('button', { name: '文字' }).click()
  await page.locator('[data-canvas-stage]').click({ position: { x, y } })
  await page.keyboard.type(text)
  await page.keyboard.press('Escape')
  await expect(canvasText(page, text)).toBeVisible()
}

test('排版时间线：自动节点 → 命名 → 预览不改排版 → 恢复 → 用「恢复前」恢复回来', async ({
  app,
  page,
}) => {
  test.setTimeout(240_000)
  await page.addInitScript(() => {
    ;(window as unknown as Record<string, unknown>).__TAVOTTO_TIMELINE_TIMING__ = {
      debounceMs: 300,
      minGapMs: 800,
    }
  })
  const a = await app()
  await page.goto(a.baseURL)

  // 面板上画布，回到排版
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  await page.getByRole('button', { name: /返回画布/ }).first().click()
  await addText(page, '甲版标注', 420, 240)

  // ── 顶栏时钟钮打开时间线；等第一个自动节点 ──────────────────────────
  await page.locator('[data-timeline-button]').click()
  const drawer = page.locator('[data-timeline-drawer]')
  await expect(drawer).toBeVisible()
  const nodes = drawer.locator('[data-timeline-node]')
  await expect.poll(() => nodes.count(), { timeout: 30_000 }).toBeGreaterThan(0)
  await expect(drawer.locator('[data-timeline-kind="auto"]').first()).toBeVisible()

  // ── 命名「投稿前」 ─────────────────────────────────────────────────
  await drawer.locator('[data-timeline-name-input]').fill('投稿前')
  await drawer.locator('[data-timeline-save-named]').click()
  const named = drawer.locator('[data-timeline-node][data-timeline-named]', { hasText: '投稿前' })
  await expect(named).toBeVisible()
  // 缩略图是拍那一刻在浏览器里合成、单独 PUT 上去的：真浏览器里它得挂得上
  await expect(named.locator('img[data-timeline-thumb]')).toBeVisible({ timeout: 30_000 })
  // 不只是「挂上了一张图」：图里真的画进了面板（不是一张白纸）。数深色像素——
  // 白底 + 文字几个像素远不到这个量，面板那条曲线与坐标轴才到得了
  const inked = await named.locator('img[data-timeline-thumb]').evaluate(async (img: HTMLImageElement) => {
    await img.decode().catch(() => {})
    if (!img.naturalWidth) return -1
    const c = document.createElement('canvas')
    c.width = img.naturalWidth
    c.height = img.naturalHeight
    const ctx = c.getContext('2d')!
    ctx.drawImage(img, 0, 0)
    const px = ctx.getImageData(0, 0, c.width, c.height).data
    let dark = 0
    for (let i = 0; i < px.length; i += 4) if (px[i] + px[i + 1] + px[i + 2] < 600) dark++
    return dark
  })
  expect(inked).toBeGreaterThan(200)
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, 'timeline-named.png') })

  // ── 继续改：再加一段文字，删掉「甲版标注」 ─────────────────────────
  await page.locator('[data-timeline-button]').click() // 收起抽屉，腾出画布
  await expect(drawer).toHaveCount(0)
  await addText(page, '乙版标注', 420, 320)
  await expect(canvasText(page, '甲版标注')).toBeVisible()

  // ── 预览「投稿前」：画布一个字都不动 ────────────────────────────────
  await page.keyboard.press('ControlOrMeta+Shift+H')
  await expect(drawer).toBeVisible()
  await named.locator('[data-timeline-row]').click()
  const preview = page.locator('[data-timeline-preview]')
  await expect(preview).toBeVisible()
  // 预览里是那一刻：有甲、没有乙
  await expect(preview.getByText('甲版标注', { exact: true })).toBeVisible()
  await expect(preview.getByText('乙版标注', { exact: true })).toHaveCount(0)
  // 底下的真画布原样：乙还在
  await expect(canvasText(page, '乙版标注')).toHaveCount(1)
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, 'timeline-preview.png') })

  // ── 恢复到这里 ──────────────────────────────────────────────────────
  await preview.locator('[data-timeline-preview-restore]').click()
  await expect(preview).toHaveCount(0)
  await expect(canvasText(page, '乙版标注')).toHaveCount(0)
  await expect(canvasText(page, '甲版标注')).toBeVisible()

  // ── 时间线里出现「恢复前」节点；用它恢复回来 ──────────────────────
  const before = drawer.locator('[data-timeline-node]', {
    has: page.locator('[data-timeline-moment="before_restore"]'),
  })
  await expect(before).toHaveCount(1, { timeout: 15_000 })
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, 'timeline-before-restore.png') })
  await before.locator('[data-timeline-row]').click()
  await expect(preview).toBeVisible()
  await expect(preview.getByText('乙版标注', { exact: true })).toBeVisible()
  await preview.locator('[data-timeline-preview-restore]').click()
  await expect(canvasText(page, '乙版标注')).toBeVisible()
  await expect(page.getByText(/已恢复到/).first()).toBeVisible()

  // 恢复是一条历史：⌘Z 一步退回
  await page.keyboard.press('Escape') // 焦点离开抽屉里的按钮，交还全局快捷键
  await page.locator('[data-canvas-stage]').click({ position: { x: 40, y: 40 } })
  await page.keyboard.press('ControlOrMeta+z')
  await expect(canvasText(page, '乙版标注')).toHaveCount(0)
})
