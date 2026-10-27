import { chmodSync, mkdtempSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import type { Page } from '@playwright/test'
import { expect, selectCanvasFigure, test, waitForFigureEdit } from './fixtures'

/**
 * 「技术详情」里的执行追踪（ADR 0151 PR3），经**真实后端**：假 `codex` 在用例放行前一直跑、放行后以非零码退出——
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

  // 默认收起：普通用户看不到技术细节，也不发请求
  const toggle = session.locator('[data-ai-trace-toggle]')
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')
  await expect(page.locator('[data-trace-row]')).toHaveCount(0)
  await toggle.click()
  const agentRow = page.locator('[data-trace-row]', { has: page.locator('[data-trace-bar]') }).filter({
    hasText: /Codex/,
  })
  await expect(agentRow.first()).toBeVisible({ timeout: 15_000 })
  await expect(page.locator('[data-ai-trace-state="running"]')).toBeVisible()
  await expect(page.locator('[data-trace-bar][data-running]').first()).toBeVisible()
  await page.locator('[data-ai-trace]').screenshot({ path: info.outputPath('exec-trace-running.png') })

  // 放行：假 CLI 以 3 退出 → Agent 运行失败；那一行是第一次失败
  writeFileSync(release, '')
  await expect(page.locator('[data-ai-session="failed"]')).toBeVisible({ timeout: 30_000 })
  await expect(page.locator('[data-ai-trace-state="failed"]')).toBeVisible({ timeout: 15_000 })
  await expect(page.locator('[data-trace-badge="first-failure"]')).toHaveCount(1)
  await expect(page.locator('[data-trace-bar][data-running]')).toHaveCount(0)
  await page.locator('[data-ai-trace]').screenshot({ path: info.outputPath('exec-trace-failed.png') })
  // 暗色主题：颜色全走语义 token，换主题当场变（截图为证）
  await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'dark'))
  await page.locator('[data-ai-trace]').screenshot({ path: info.outputPath('exec-trace-failed-dark.png') })
  await page.evaluate(() => document.documentElement.removeAttribute('data-theme'))

  // 点 Agent 那一条：popover 里有错误码与「内部不可观察」
  await page.locator('[data-trace-badge="first-failure"]').first().locator('xpath=ancestor::*[@data-trace-row][1]').locator('[data-trace-trigger]').first().click()
  const pop = page.locator('[data-trace-popover]')
  await expect(pop).toBeVisible()
  await expect(pop).toContainText('agent_exit_nonzero')
  // Codex 内部看不见：调用次数是「—」（不知道），不是 0；根的失败不重复计
  await expect(page.locator('[data-trace-metric="modelCalls"] dd')).toHaveAttribute('data-value', '—')
  await expect(page.locator('[data-trace-metric="errors"] dd')).toHaveAttribute('data-value', '1')
  await page.keyboard.press('Escape')
  await expect(pop).toHaveCount(0)

  // 刷新：前端什么都不记得；已结束的那一轮只在任务历史里，执行记录从后端保留期内的落盘快照恢复
  await page.reload()
  await selectCanvasFigure(page)
  await page.locator('[data-inspector-tab="assistant"]').click()
  await page.locator('[data-ai-history-trigger]').click()
  const row = page.locator('[data-ai-history-row="failed"]').first()
  await expect(row).toBeVisible({ timeout: 30_000 })
  await row.locator('[data-ai-history-details]').click()
  await row.locator('[data-ai-trace-toggle]').click()
  await expect(row.locator('[data-ai-trace-state="failed"]')).toBeVisible({ timeout: 15_000 })
  await expect(row.locator('[data-trace-badge="first-failure"]')).toHaveCount(1)
  await row.locator('[data-ai-trace]').screenshot({ path: info.outputPath('exec-trace-history.png') })
})
