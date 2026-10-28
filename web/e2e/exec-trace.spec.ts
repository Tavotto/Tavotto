import { chmodSync, mkdtempSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import type { Page } from '@playwright/test'
import { expect, selectCanvasFigure, test, waitForFigureEdit } from './fixtures'

/**
 * 「执行记录」宽浮动卡里的执行追踪（ADR 0151 PR3；窄栏只有一行入口），经**真实后端**：假 `codex` 在用例放行前一直跑、放行后以非零码退出——
 * 真实的 `ai_bridge.run` → 执行追踪 → `/api/exec-trace` → 适配层 → Waterfall。断言运行中有条在跑、失败后
 * Agent 那一行是第一次失败、刷新页面后从后端权威快照恢复。截图存到 test-results 作为证据。POSIX only。
 */
test.skip(process.platform === 'win32', '假 CLI 是 shell 脚本，只在 posix-e2e 腿执行')

function fakeFailingCodex(): { PATH: string; release: string } {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'tavotto-fake-codex-'))
  const release = path.join(dir, 'release')
  const bin = path.join(dir, 'codex')
  writeFileSync(
    bin,
    `#!/bin/sh\ncase "$1" in\n  --version) echo "codex-cli 0.0.1" ;;\n  login) echo "Logged in" ;;\n  *) while [ ! -f "${release}" ]; do sleep 0.2; done; exit 3 ;;\nesac\n`,
  )
  chmodSync(bin, 0o755)
  return { PATH: `${dir}${path.delimiter}${process.env.PATH ?? ''}`, release }
}

async function openAssistant(page: Page, baseURL: string) {
  await page.goto(baseURL)
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  await waitForFigureEdit(page)
  await page.locator('[data-inspector-tab="assistant"]').click()
  await expect(page.locator('[data-ai-input]')).toBeEnabled({ timeout: 30_000 })
}

test('执行追踪：运行中实时、失败归因到 Agent 运行、刷新后从后端恢复', async ({ app, page }, info) => {
  const { PATH: fakePath, release } = fakeFailingCodex()
  const a = await app({ env: { PATH: fakePath } })
  await openAssistant(page, a.baseURL)
  await page.locator('[data-ai-input]').fill('把线条加粗一点')
  await page.locator('[data-ai-send="send"]').click()
  const session = page.locator('[data-ai-session="running"]')
  await expect(session).toBeVisible({ timeout: 30_000 })

  // 窄栏里只有一行入口：没打开宽卡之前不发请求、页面上没有任何 Waterfall 行
  const toggle = session.locator('[data-ai-trace-toggle]')
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')
  await expect(page.locator('[data-trace-row]')).toHaveCount(0)
  await toggle.click()
  const card = page.locator('[data-dialog="exec-trace"]')
  await expect(card).toBeVisible()
  // 宽：不是窄栏里的内联（窄栏不到 400px）；至少 720px，且不超出视口
  const box = (await card.boundingBox())!
  expect(box.width).toBeGreaterThan(720)
  expect(box.x).toBeGreaterThanOrEqual(0)
  expect(box.x + box.width).toBeLessThanOrEqual(page.viewportSize()!.width)
  const agentRow = page.locator('[data-trace-row]', { has: page.locator('[data-trace-bar]') }).filter({
    hasText: /Codex/,
  })
  await expect(agentRow.first()).toBeVisible({ timeout: 15_000 })
  await expect(page.locator('[data-ai-trace-state="running"]')).toBeVisible()
  await expect(page.locator('[data-trace-bar][data-running]').first()).toBeVisible()
  // 运行中走实时，没有回放条
  await expect(page.locator('[data-trace-playback]')).toHaveCount(0)
  await card.screenshot({ path: info.outputPath('exec-trace-running.png') })

  // 放行：假 CLI 以 3 退出 → Agent 运行失败；那一行是第一次失败
  writeFileSync(release, '')
  await expect(page.locator('[data-ai-session="failed"]')).toBeVisible({ timeout: 30_000 })
  await expect(page.locator('[data-ai-trace-state="failed"]')).toBeVisible({ timeout: 15_000 })
  await expect(page.locator('[data-trace-badge="first-failure"]')).toHaveCount(1)
  await expect(page.locator('[data-trace-bar][data-running]')).toHaveCount(0)
  // 跑着时开的卡，收尾后出现回放条但不自动播（它已经看过实时了）：停在终点
  await expect(page.locator('[data-trace-playback]')).toBeVisible()
  await expect(page.locator('[data-trace-waterfall]')).not.toHaveAttribute('data-replay', /.*/)
  await card.screenshot({ path: info.outputPath('exec-trace-failed.png') })
  // 暗色主题：颜色全走语义 token，换主题当场变（截图为证）
  await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'dark'))
  await card.screenshot({ path: info.outputPath('exec-trace-failed-dark.png') })
  await page.evaluate(() => document.documentElement.removeAttribute('data-theme'))

  // 点 Agent 那一条：popover 里有错误码与「内部不可观察」
  await page.locator('[data-trace-badge="first-failure"]').first().locator('xpath=ancestor::*[@data-trace-row][1]').locator('[data-trace-trigger]').first().click()
  const pop = page.locator('[data-trace-popover]')
  await expect(pop).toBeVisible()
  await expect(pop).toContainText('agent_exit_nonzero')
  // Codex 内部看不见：调用次数是「—」（不知道），不是 0；根的失败不重复计
  await expect(page.locator('[data-trace-metric="modelCalls"] dd')).toHaveAttribute('data-value', '—')
  await expect(page.locator('[data-trace-metric="errors"] dd')).toHaveAttribute('data-value', '1')
  // 浮层挂在卡里：能点到它的关闭钮；Esc 只关浮层、不关宽卡
  await page.keyboard.press('Escape')
  await expect(pop).toHaveCount(0)
  await expect(card).toBeVisible()
  // 导出诊断在卡的页脚（任务已结束）；关闭钮关卡
  await expect(card.locator('[data-ai-trace-export]')).toBeVisible()
  await card.locator('[data-ai-trace-close]').click()
  await expect(card).toHaveCount(0)

  // 刷新：前端什么都不记得；已结束的那一轮只在任务历史里，执行记录从后端保留期内的落盘快照恢复
  await page.reload()
  await selectCanvasFigure(page)
  await page.locator('[data-inspector-tab="assistant"]').click()
  await page.locator('[data-ai-history-trigger]').click()
  const row = page.locator('[data-ai-history-row="failed"]').first()
  await expect(row).toBeVisible({ timeout: 30_000 })
  // 失败行与别的行同一个版式：红点 + 失败；原因和「查看执行记录」在点元信息行出来的详情卡里
  await expect(row.locator('[data-ai-history-state]')).toBeVisible()
  await expect(row.locator('[data-ai-history-error]')).toHaveCount(0)
  await row.locator('[data-ai-history-details]').click()
  await expect(page.locator('[data-ai-history-card="details"]')).toBeVisible()
  await expect(page.locator('[data-ai-history-card="details"] [data-ai-history-strip]')).toBeVisible()
  await page.locator('[data-ai-history-trace]').click()
  const histCard = page.locator('[data-dialog="exec-trace"]')
  await expect(histCard).toBeVisible()
  // 打开宽卡 = 任务历史弹层和详情卡先让路：不能再有任何浮层压在宽卡上面遮住 Waterfall
  await expect(page.locator('[data-ai-history]')).toHaveCount(0)
  await expect(page.locator('[data-ai-history-card]')).toHaveCount(0)
  const dialogBox = (await histCard.boundingBox())!
  const covering = await page.evaluate(
    ({ x, y }) => {
      const top = document.elementFromPoint(x, y)
      return !!top && !top.closest('[data-dialog="exec-trace"]')
    },
    { x: dialogBox.x + dialogBox.width / 2, y: dialogBox.y + dialogBox.height / 2 },
  )
  expect(covering).toBe(false)
  await expect(histCard.locator('[data-ai-trace-state="failed"]')).toBeVisible({ timeout: 15_000 })
  // 已结束的 Trace 打开就是回放：有回放条，能拖到终点、能重播
  await expect(histCard.locator('[data-trace-playback]')).toBeVisible()
  const scrub = histCard.locator('[data-trace-playback-scrub]')
  await scrub.focus()
  await page.keyboard.press('End')
  await expect(histCard.locator('[data-trace-waterfall]')).not.toHaveAttribute('data-replay', /.*/)
  await expect(histCard.locator('[data-trace-playback-clock]')).toHaveText(/^0:\d\d \/ 0:\d\d$/)
  await histCard.locator('[data-trace-playback-replay]').click()
  await expect(histCard.locator('[data-trace-waterfall]')).toHaveAttribute('data-replay', 'true')
  await scrub.focus()
  await page.keyboard.press('End')
  await expect(histCard.locator('[data-trace-badge="first-failure"]')).toHaveCount(1)
  await histCard.screenshot({ path: info.outputPath('exec-trace-history.png') })
})
