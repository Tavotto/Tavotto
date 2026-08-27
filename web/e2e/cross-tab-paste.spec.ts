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
  // 等待器要在触发改动**之前**挂上：autosave 防抖只有几百毫秒，事后再等就可能
  // 已经错过那次响应
  const autosaved = tabA.waitForResponse(
    (r) =>
      r.request().method() === 'PUT' &&
      r.url().includes('/api/autosave/') &&
      r.status() >= 200 &&
      r.status() < 300,
    { timeout: 30_000 },
  )
  await tabA.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  await expect(tabA.locator('[data-object-id]')).toHaveCount(1)
  await tabA.keyboard.press('ControlOrMeta+c')
  await expect(tabA.getByRole('status')).toHaveText(/已复制/)

  // **等标签 A 的自动保存真的落盘**，再开标签 B（#141）。
  //
  // 旧写法直接开标签 B 并断言「粘贴后一共 1 个对象」，那句话隐含的是「标签 B
  // 看不到标签 A 的改动」——那是**时序假设**，不是本用例自称要测的「跨标签
  // 粘贴可用」。autosave 是防抖的：落盘赶在标签 B 加载之前，标签 B 就带着 A 的
  // 面板起来，粘贴后是 2 个，用例误红；赶在之后就是 1 个，用例侥幸绿。同一份
  // 代码两种结果，判据的主语错位（同族：#133 / #136 / #138）。
  //
  // 同步点盯的是**后端真的收下了那次写**（`PUT /api/autosave/<id>` 回 2xx），
  // 不是顶栏那句「已自动保存」——后者是乐观的：`flushAutosave()` 排完盘就立刻
  // 把 dirty 翻成 false，PUT 还在路上。标签 B 的加载读的是磁盘那一份，所以判据
  // 必须落在磁盘上（Codex 在 PR #163 上指出，成立）。
  await autosaved

  // 标签 B：同一项目的另一个标签页，直接 ⌘V
  const tabB = await context.newPage()
  await tabB.goto(a.baseURL)
  await expect(tabB.getByRole('button', { name: /当前项目/ })).toBeVisible({
    timeout: 30_000,
  })
  // 基线：标签 B 加载出来的就是磁盘上那份（1 个 = 标签 A 刚放的面板）
  await expect(tabB.locator('[data-object-id]')).toHaveCount(1, { timeout: 30_000 })
  await tabB.bringToFront()
  await tabB.locator('[data-canvas-stage]').click({ position: { x: 300, y: 200 } })
  await tabB.keyboard.press('ControlOrMeta+v')
  await expect(tabB.getByRole('status')).toHaveText(/已粘贴 1 个对象/, { timeout: 10_000 })
  // 断言的是**这次粘贴多出来一个**，不是「一共只有一个」
  await expect(tabB.locator('[data-object-id]')).toHaveCount(2)

  await context.close()
})
