import { chmodSync, mkdtempSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

/**
 * Sign in with ChatGPT 默认开（ADR 0209 §六，真后端，不设 `TAVOTTO_EXPERIMENTAL_SIWC`）——老用户升级后的那条路：
 *   1. 本机装着已登录的 Codex CLI：「连接 AI」里 ChatGPT 一家只给「Continue with ChatGPT」，Codex 登录不再列出，`sub:codex` 也不在连接里；
 *   2. ChatGPT 登录失败（这里路由层把登录流程答成「没允许订阅额度」，不联网）→ 那句后面有「改用 Codex 登录」；
 *   3. 点它：先记下选择（`POST /api/ai/siwc/codex-fallback`），之后能力里重新有 `sub:codex`。
 * 假 `codex`：`--version` / `login status` 答已登录。POSIX only；定位一律走 `data-*`。
 */
test.skip(process.platform === 'win32', '假 CLI 是 shell 脚本，只在 posix-e2e 腿执行')

function fakeCodexPath(): string {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'tavotto-fake-codex-'))
  const bin = path.join(dir, 'codex')
  writeFileSync(bin, '#!/bin/sh\ncase "$1" in\n  --version) echo "codex-cli 0.0.1" ;;\n  login) echo "Logged in" ;;\n  *) sleep 1 ;;\nesac\n')
  chmodSync(bin, 0o755)
  return `${dir}${path.delimiter}${process.env.PATH ?? ''}`
}

async function capsConnections(page: Page): Promise<string[]> {
  return page.evaluate(async () => {
    const r = await fetch(`/api/ai/capabilities${location.search}`)
    const caps = (await r.json()) as { connections?: { id: string }[] }
    return (caps.connections ?? []).map((c) => c.id)
  })
}

test('默认开：老用户只看到 Continue with ChatGPT；登录失败可以改用 Codex 登录，选了之后 Codex 连接回来', async ({ app, page }) => {
  const a = await app({ env: { PATH: fakeCodexPath() } })
  await page.addInitScript(() => {
    window.open = () => null
  })
  await page.route('**/api/ai/siwc/login?*', (r) =>
    r.fulfill({ json: { ok: true, flow_id: 'f1', flow: { flow_id: 'f1', state: 'waiting', authorize_url: 'https://example.invalid/' } } }),
  )
  await page.route('**/api/ai/siwc/flows/**', (r) =>
    r.fulfill({ json: { flow_id: 'f1', state: 'plan_disabled', authorize_url: 'https://example.invalid/' } }),
  )
  // Codex 官方登录本身不在本用例范围（ai-cli-update 等守）：只答「已发起」
  await page.route('**/api/ai/agents/codex/login*', (r) => r.fulfill({ json: { ok: true, already: true } }))

  await page.goto(a.baseURL)
  await page.locator('[data-rail="settings"]').click({ timeout: 60_000 })
  await page.locator('[data-section="ai"]').first().click()
  const vendor = page.locator('[data-connect-vendor="openai"]').first()
  await expect(vendor).toBeVisible({ timeout: 30_000 })
  // 按这一家的展开状态（aria-expanded）决定点不点，不赌一次性 count()（错题本 L-071 / L-082）
  const methods = vendor.locator('[data-connect-action="methods"]')
  await expect(methods).toHaveCount(1)
  if ((await methods.getAttribute('aria-expanded')) !== 'true') await methods.click()

  // 1. 只有 Continue with ChatGPT；Codex 登录与 sub:codex 都不在
  await expect(page.locator('[data-connect-method="openai:siwc"]')).toBeVisible()
  await expect(page.locator('[data-connect-method="openai:login"]')).toHaveCount(0)
  expect(await capsConnections(page)).not.toContain('sub:codex')

  // 2. 登录失败 → 一句话 + 「改用 Codex 登录」（本机装了 Codex）
  await page.locator('[data-connect-action="siwc"]').first().click()
  await expect(page.locator('[data-connect-login-hint="failed"]')).toBeVisible({ timeout: 15_000 })
  const useCodex = page.locator('[data-connect-action="siwc-use-codex"]')
  await expect(useCodex).toBeVisible()

  // 3. 选了 → 先记下选择，之后 Codex 连接回来
  const remembered = page.waitForResponse((r) => r.url().includes('/api/ai/siwc/codex-fallback') && r.request().method() === 'POST')
  await useCodex.click()
  expect((await remembered).ok()).toBe(true)
  await expect.poll(() => capsConnections(page), { timeout: 30_000 }).toContain('sub:codex')
})
