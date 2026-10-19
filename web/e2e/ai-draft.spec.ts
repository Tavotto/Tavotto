import { chmodSync, mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import type { Page } from '@playwright/test'
import { expect, selectCanvasFigure, test, waitForFigureEdit, type RunningApp } from './fixtures'

/**
 * 助手草稿与提交可靠性的黄金路径（ADR 0132，真浏览器 + 真后端）。
 *
 * 两条用例：
 *   1. 打字 → 切页签 → 切回 → 刷新，草稿都在（右栏切页签会卸载助手面板）；
 *   2. 发送请求没到后端（路由层掐掉）= 「已发出未确认」：没有普通重试、只有「查询结果」；刷新后仍是这个态；
 *      查到后端没收到才用**同一个 client_request_id** 重发——后端历史里这条指令恰好一条（没有双跑）。
 *
 * 要一个「可用」的改图助手才能发：往 PATH 里放一个假的 `codex`（`--version` / `login status` 答话，
 * 其余睡一秒就退出），后端的能力探测会把它当成已安装且已登录。POSIX only。
 * 定位一律走 `data-*`，中英两个 locale 都跑。
 */
test.skip(process.platform === 'win32', '假 CLI 是 shell 脚本，只在 posix-e2e 腿执行')

/** `editThenFail`：执行时先改一下 cwd（图库目录）里的脚本再以非零退出——「失败了，但脚本已经被改过」 */
function fakeCodexDir(editThenFail = false): string {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'tavotto-fake-codex-'))
  mkdirSync(dir, { recursive: true })
  const bin = path.join(dir, 'codex')
  const run = editThenFail ? 'for f in *.py; do echo "# edited" >> "$f"; done; exit 1' : 'sleep 1'
  writeFileSync(
    bin,
    `#!/bin/sh\ncase "$1" in\n  --version) echo "codex-cli 0.0.1" ;;\n  login) echo "Logged in" ;;\n  *) ${run} ;;\nesac\n`,
  )
  chmodSync(bin, 0o755)
  return dir
}

async function startWithAgent(
  app: (o?: { env?: Record<string, string> }) => Promise<RunningApp>,
  editThenFail = false,
) {
  return app({ env: { PATH: `${fakeCodexDir(editThenFail)}${path.delimiter}${process.env.PATH ?? ''}` } })
}

/**
 * 打开 Fig1 并切到右栏的助手页签；等输入框可用（能力探测完成、面板有了目标）。
 * 刷新之后工作区会把这张图恢复在画布上（但不在图内编辑态、也没有选中）：那时点一下画布上的图选中它。
 */
async function openAssistant(page: Page, baseURL: string, opts: { reloaded?: boolean } = {}) {
  if (opts.reloaded) {
    // 选中会把右栏自动切到「属性」，所以先选图、再切助手页签
    await selectCanvasFigure(page)
    await page.locator('[data-inspector-tab="assistant"]').click()
  } else {
    await page.goto(baseURL)
    await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
    await waitForFigureEdit(page)
    await page.locator('[data-inspector-tab="assistant"]').click()
  }
  await expect(page.locator('[data-ai-input]')).toBeEnabled({ timeout: 30_000 })
}

test('草稿：打字后切页签、刷新都还在', async ({ app, page }) => {
  const a = await startWithAgent(app)
  await openAssistant(page, a.baseURL)
  const input = page.locator('[data-ai-input]')
  await input.fill('把图例移到右上角')

  // 切到别的页签（助手面板卸载）再切回
  await page.locator('[data-inspector-tab="properties"]').click()
  await expect(page.locator('[data-ai-input]')).toHaveCount(0)
  await page.locator('[data-inspector-tab="assistant"]').click()
  await expect(page.locator('[data-ai-input]')).toHaveValue('把图例移到右上角')
  await expect(page.locator('[data-ai-draft-note="restored"]')).toBeVisible()

  // 刷新（pagehide 会 flush）：重新打开同一张图，草稿恢复
  await page.reload()
  await openAssistant(page, a.baseURL, { reloaded: true })
  await expect(page.locator('[data-ai-input]')).toHaveValue('把图例移到右上角')
  await expect(page.locator('[data-ai-draft-note="restored"]')).toBeVisible()
})

test('草稿存后端：换 origin（等价于桌面端口变化）后草稿仍在，且浏览器存储里没有草稿', async ({ app, page, browser }) => {
  const a = await startWithAgent(app)
  await openAssistant(page, a.baseURL)
  await page.locator('[data-ai-input]').fill('换端口也不能丢的指令')
  // 切走页签（面板卸载 → keepalive 冲刷）；等后端真的存上
  await page.locator('[data-inspector-tab="properties"]').click()
  await expect
    .poll(async () =>
      page.evaluate(async () => {
        const r = await fetch('/api/ai/drafts?file=' + encodeURIComponent('Fig1_kinetics.pdf'))
        return (await r.json()).draft?.text ?? null
      }),
    )
    .toBe('换端口也不能丢的指令')
  // 草稿不在任何浏览器存储里（存储是按 origin 的，端口一变就是空的——这条路径不能依赖它）
  const stored = await page.evaluate(() => JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage }))
  expect(stored).not.toContain('换端口也不能丢的指令')

  // 另一个 origin（localhost ≠ 127.0.0.1）打开同一个后端：全新的浏览器存储
  const ctx2 = await browser.newContext()
  try {
    const p2 = await ctx2.newPage()
    await openAssistant(p2, `http://localhost:${a.port}`)
    await expect(p2.locator('[data-ai-input]')).toHaveValue('换端口也不能丢的指令')
    await expect(p2.locator('[data-ai-draft-note="restored"]')).toBeVisible()
  } finally {
    await ctx2.close()
  }
})

test('未确认：请求没到后端 → 只有「查询结果」；查到没收到才用同一个 id 重发，后端恰好一条', async ({ app, page }) => {
  const a = await startWithAgent(app)
  const prompt = '把线条加粗一点'
  const runRids: string[] = []
  let blocking = true
  // 请求带 ?pj=<项目> 查询串，glob 的 `run` 后面要放行它
  await page.route(/\/api\/ai\/run(\?|$)/, async (route) => {
    runRids.push(JSON.parse(route.request().postData() ?? '{}').client_request_id)
    if (blocking) await route.abort('timedout') // 请求没有任何答复：与 30 s 超时同一类（非 HTTP 响应）
    else await route.continue()
  })

  await openAssistant(page, a.baseURL)
  await page.locator('[data-ai-input]').fill(prompt)
  await page.locator('[data-ai-send="send"]').click()

  // 已发出未确认：不是「发送失败」，没有普通重试，发送钮禁用；待确认原文在那一行里
  const unconfirmed = page.locator('[data-ai-submission="unconfirmed"]')
  await expect(unconfirmed).toBeVisible()
  await expect(page.locator('[data-ai-check]')).toBeVisible()
  await expect(page.locator('[data-ai-retry]')).toHaveCount(0)
  await expect(page.locator('[data-ai-error="send"]')).toHaveCount(0)
  await expect(page.locator('[data-ai-send]')).toBeDisabled()
  await expect(page.locator('[data-ai-submission-text]')).toHaveText(prompt)
  expect(runRids).toHaveLength(1)

  // 刷新之后不会变回一条可以普通重发的草稿：还原出「已发出」的提交，并自动（只读）去后端查——
  // 后端从没见过这个 id → 自动进入「没收到」，给「重新发送」；全程不用点「查询结果」，也没有悄悄再发
  await page.reload()
  await openAssistant(page, a.baseURL, { reloaded: true })
  await expect(page.locator('[data-ai-submission="not_received"]')).toBeVisible()
  await expect(page.locator('[data-ai-input]')).toHaveValue(prompt)
  await expect(page.locator('[data-ai-send]')).toBeDisabled()
  expect(runRids).toHaveLength(1) // 自动查询只读，没有再发

  // 同一个 id 重发，这回放行
  blocking = false
  await page.locator('[data-ai-resend-same]').click()
  await expect(page.locator('[data-ai-submission]')).toHaveCount(0, { timeout: 30_000 })
  await expect(page.locator('[data-ai-session]').first()).toBeVisible({ timeout: 30_000 })
  expect(runRids).toHaveLength(2)
  expect(runRids[1]).toBe(runRids[0])
  await expect(page.locator('[data-ai-input]')).toHaveValue('')

  // 后端权威：这个 id 对应恰好一个会话，历史里这条指令恰好一条
  const status = await page.evaluate(async (rid) => (await fetch(`/api/ai/requests/${rid}`)).status, runRids[0])
  expect(status).toBe(200)
  const hist = await page.evaluate(async () => (await (await fetch('/api/ai/history?limit=50')).json()).sessions)
  expect(hist.filter((s: { prompt: string }) => s.prompt === prompt)).toHaveLength(1)
})

test('失败但已改过脚本：会话的「重试」先二次确认，取消不重跑', async ({ app, page }) => {
  const a = await startWithAgent(app, true)
  const runs: string[] = []
  await page.route(/\/api\/ai\/run(\?|$)/, async (route) => {
    runs.push(JSON.parse(route.request().postData() ?? '{}').client_request_id)
    await route.continue()
  })
  await openAssistant(page, a.baseURL)
  await page.locator('[data-ai-input]').fill('把线条加粗一点')
  await page.locator('[data-ai-send="send"]').click()
  const retry = page.locator('[data-ai-error="session"] [data-ai-retry]')
  await expect(retry).toBeVisible({ timeout: 30_000 })
  // 后端权威：这次失败的会话确实改过脚本
  const hist = await page.evaluate(async () => (await (await fetch('/api/ai/history?limit=5')).json()).sessions)
  expect(hist[0]).toMatchObject({ status: 'failed', changed: true })
  expect(runs).toHaveLength(1)

  // 点重试：弹确认，取消 → 一个新请求都不发
  await retry.click()
  const confirm = page.getByRole('dialog')
  await expect(confirm).toBeVisible()
  await confirm.locator('[data-confirm-cancel]').click()
  await expect(confirm).toHaveCount(0)
  expect(runs).toHaveLength(1)

  // 再点、确认 → 才重跑，且是新的请求 id
  await retry.click()
  await page.getByRole('dialog').locator('[data-confirm-ok]').click()
  await expect.poll(() => runs.length, { timeout: 15_000 }).toBe(2)
  expect(runs[1]).not.toBe(runs[0])
})
