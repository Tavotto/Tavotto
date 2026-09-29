import { test, expect } from './fixtures'

/**
 * 会话状态以后端为准（#715 PR-B）：**空存储的浏览器**也回到上次的排版。
 *
 * 桌面版每次启动 sidecar 可能换端口，localStorage 按 origin 隔离——换了端口就是一份全新的
 * 空存储，改造前「上次打开的排版」记在那里，于是每次启动都落在一份空白排版上（#715）。
 * 这里用同一个服务上**新开的浏览器 context**（storage 全空，与换了 origin 的 WebView 同一处境）
 * 复现：第二个 context 必须恢复出第一个 context 里排的那份。改造前它是空白的。
 *
 * 同一服务、不同 context 也是「同一个磁盘槽位、不同的本机存储」——恰好把「文档在磁盘上」与
 * 「记得打开哪一份」这两件事拆开量：前者一直成立，#715 坏的是后者。
 */
test('新开一个空存储的浏览器 context：恢复上次的排版', async ({ app, browser }) => {
  const a = await app()

  const first = await browser.newContext()
  const tabA = await first.newPage()
  await tabA.goto(a.baseURL)
  // 定位一律认稳定的 `data-*`（素材卡 `data-card`、返回 `data-context-back`）：不认文案 / 角色名
  const card = (p: typeof tabA) => p.locator('[data-card="Fig1_kinetics.pdf"]')
  await expect(card(tabA)).toBeVisible({ timeout: 30_000 })
  // 等待器在触发改动**之前**挂上（防抖只有几百毫秒，事后再等可能已经错过）。
  // 判据落在后端真的收下了：自动保存 PUT 回 2xx；「上次开着的」那条 PUT 同样要落地——
  // 它在改造前根本不存在，所以只等一小会儿、等不到也往下走，让下面那条断言说话（在 main 上红）
  const ok = (r: { status(): number }) => r.status() >= 200 && r.status() < 300
  const autosaved = tabA.waitForResponse(
    (r) => r.request().method() === 'PUT' && r.url().includes('/api/autosave/') && ok(r),
    { timeout: 30_000 },
  )
  const remembered = tabA
    .waitForResponse(
      (r) => r.request().method() === 'PUT' && r.url().includes('/api/layout-session/last') && ok(r),
      { timeout: 15_000 },
    )
    .catch(() => null)
  await card(tabA).dblclick({ timeout: 30_000 })
  // 双击进快速编辑；回到画布排版再量（工作区模式按 documentId 存本机，见 cross-tab-paste）
  await tabA.locator('[data-context-back]').click()
  await expect(tabA.locator('[data-object-id]')).toHaveCount(1)
  await autosaved
  await remembered
  // 前提：第一个 context 的本机存储里确实记着（否则下面量的就不是「换了存储」）
  expect(
    await tabA.evaluate(() => localStorage.getItem('tavotto.currentDoc')),
  ).toBeTruthy()
  await first.close()

  // 第二个 context：storage 全空（与换了端口的桌面 WebView 同一处境）
  const second = await browser.newContext()
  expect((await second.storageState()).origins, '新 context 的本机存储必须是空的').toEqual([])
  const tabB = await second.newPage()
  await tabB.goto(a.baseURL)
  await expect(card(tabB)).toBeVisible({ timeout: 30_000 })
  await expect(tabB.locator('[data-object-id]')).toHaveCount(1, { timeout: 30_000 })
  await second.close()
})
