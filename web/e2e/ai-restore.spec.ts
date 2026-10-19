import { chmodSync, mkdtempSync, writeFileSync } from 'node:fs'
import { existsSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import type { Page } from '@playwright/test'
import { expect, selectCanvasFigure, test, waitForFigureEdit } from './fixtures'

/**
 * 运行中会话的恢复（PR4b）：发起一个跑着的会话 → 刷新 → 占位在、发送锁成「中止」→ 会话结束后转成终态、锁解开。
 *
 * 假 `codex`：`--version` / `login status` 答话；执行时一直等到「放行文件」出现才退出（用例自己放行，
 * 不靠 sleep 的运气）。POSIX only。定位走 `data-*`，中英各跑一遍。
 */
test.skip(process.platform === 'win32', '假 CLI 是 shell 脚本，只在 posix-e2e 腿执行')

function fakeRunningCodex(): { PATH: string; release: string } {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'tavotto-fake-codex-'))
  const release = path.join(dir, 'release')
  const bin = path.join(dir, 'codex')
  writeFileSync(
    bin,
    `#!/bin/sh\ncase "$1" in\n  --version) echo "codex-cli 0.0.1" ;;\n  login) echo "Logged in" ;;\n  *) while [ ! -f "${release}" ]; do sleep 0.2; done ;;\nesac\n`,
  )
  chmodSync(bin, 0o755)
  return { PATH: `${dir}${path.delimiter}${process.env.PATH ?? ''}`, release }
}

async function openAssistant(page: Page, baseURL: string, reloaded = false) {
  if (reloaded) {
    // 刷新后工作区把图恢复在画布上但没选中；选中会把右栏切到「属性」，所以先选图、再切助手页签
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

test('刷新后运行中的会话接回为占位：发送锁成中止；结束后转终态、锁解开', async ({ app, page }) => {
  const { PATH: fakePath, release } = fakeRunningCodex()
  const a = await app({ env: { PATH: fakePath } })
  await openAssistant(page, a.baseURL)
  await page.locator('[data-ai-input]').fill('把线条加粗一点')
  await page.locator('[data-ai-send="send"]').click()
  await expect(page.locator('[data-ai-session="running"]')).toBeVisible({ timeout: 30_000 })

  // 刷新：会话列表是内存里的，全丢——后端那个还在跑
  await page.reload()
  await openAssistant(page, a.baseURL, true)
  const running = page.locator('[data-ai-session="running"]')
  await expect(running).toBeVisible({ timeout: 30_000 })
  await expect(page.locator('[data-ai-restored]')).toBeVisible()
  await expect(running).toContainText('把线条加粗一点') // 指令从后端历史接回
  // 发送被锁：同一个按钮是「中止」，不是「发送」
  await expect(page.locator('[data-ai-send="stop"]')).toBeVisible()
  await expect(page.locator('[data-ai-send="send"]')).toHaveCount(0)

  // 放行：假 CLI 退出 → ai.done 认领到占位上，转终态，锁解开
  writeFileSync(release, '')
  expect(existsSync(release)).toBe(true)
  await expect(page.locator('[data-ai-session="running"]')).toHaveCount(0, { timeout: 30_000 })
  await expect(page.locator('[data-ai-session="done"]')).toBeVisible()
  await expect(page.locator('[data-ai-restored]')).toHaveCount(0)
  await page.locator('[data-ai-input]').fill('再来一条')
  await expect(page.locator('[data-ai-send="send"]')).toBeEnabled()
})
