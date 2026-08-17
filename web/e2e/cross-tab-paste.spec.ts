import { test, expect } from './fixtures'

/**
 * 跨标签页复制粘贴：对象剪贴板走系统剪贴板（JSON + 魔数），
 * 「复制的素材无法跨标签页粘贴」这类回归只有真开两个标签页才能拦住。
 */
test('同一项目开两个标签页：标签 A 复制面板，标签 B 粘贴', async ({ app, browser }) => {
  const a = await app()

  const context = await browser.newContext()
  await context.grantPermissions(['clipboard-read', 'clipboard-write'], {
    origin: a.baseURL,
  })

  // 标签 A：放一个面板上画布并复制
  const tabA = await context.newPage()
  await tabA.goto(a.baseURL)
  await expect(tabA.getByRole('button', { name: /当前项目/ })).toBeVisible({
    timeout: 30_000,
  })
  await tabA.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  await expect(tabA.locator('[data-object-id]')).toHaveCount(1)
  await tabA.keyboard.press('ControlOrMeta+c')
  await expect(tabA.getByRole('status')).toHaveText(/已复制/)

  // 标签 B：同一项目的另一个标签页，直接 ⌘V
  const tabB = await context.newPage()
  await tabB.goto(a.baseURL)
  await expect(tabB.getByRole('button', { name: /当前项目/ })).toBeVisible({
    timeout: 30_000,
  })
  await tabB.bringToFront()
  await tabB.locator('[data-canvas-stage]').click({ position: { x: 300, y: 200 } })
  await tabB.keyboard.press('ControlOrMeta+v')
  await expect(tabB.getByRole('status')).toHaveText(/已粘贴 1 个对象/, { timeout: 10_000 })
  await expect(tabB.locator('[data-object-id]')).toHaveCount(1)

  await context.close()
})
