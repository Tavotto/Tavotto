import { chmodSync, mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import type { Page } from '@playwright/test'
import { expect, test, waitForFigureEdit } from './fixtures'

/**
 * CLI 比模型清单旧（2026-11-04 用户反馈）：Codex 桌面版先升级、刷新了共用的 `models_cache.json`，
 * Tavotto 启动的那份旧 codex 选到新型号就 404。这里**真后端**判（假 codex 报旧版本号 + 假 CODEX_HOME 里的新清单），
 * 只验界面：型号子菜单末行出「更新 Codex」，失败卡一句话 + 一个主按钮。
 *
 * 假 `codex`：`--version` 报 `VERSION`；`login status` 答已登录；执行时吐一行 Codex 真机格式的 404 `turn.failed` 后非零退出。
 * 设置环境变量 TAVOTTO_SHOT_DIR 时把菜单与失败卡（浅 / 深）截图存进去（人工验收用，不是断言）。POSIX only。
 */
test.skip(process.platform === 'win32', '假 CLI 是 shell 脚本，只在 posix-e2e 腿执行')

function fakeOldCodex(version: string, catalogVersion: string): Record<string, string> {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'tavotto-old-codex-'))
  const home = path.join(dir, 'codex-home')
  mkdirSync(home)
  const model = (slug: string, name: string, priority: number) => ({
    slug,
    display_name: name,
    description: '',
    visibility: 'list',
    priority,
    supported_reasoning_levels: [{ effort: 'medium' }, { effort: 'high' }],
    default_reasoning_level: 'medium',
  })
  writeFileSync(
    path.join(home, 'models_cache.json'),
    JSON.stringify({
      client_version: catalogVersion,
      models: [model('gpt-6.1-sol', 'GPT-6.1-Sol', 1), model('gpt-6-astra', 'GPT-6-Astra', 2), model('gpt-5.6-sol', 'GPT-5.6-Sol', 3)],
    }),
  )
  writeFileSync(path.join(home, 'config.toml'), 'model = "gpt-5.6-sol"\n')
  const line = JSON.stringify({ type: 'turn.failed', error: { message: 'unexpected status 404 Not Found: model not found' } })
  const bin = path.join(dir, 'codex')
  writeFileSync(
    bin,
    `#!/bin/sh\ncase "$1" in\n  --version) echo "codex-cli ${version}" ;;\n  login) echo "Logged in using ChatGPT" ;;\n  *) echo '${line}'; exit 1 ;;\nesac\n`,
  )
  chmodSync(bin, 0o755)
  return { PATH: `${dir}${path.delimiter}${process.env.PATH ?? ''}`, CODEX_HOME: home }
}

async function openAssistant(page: Page, baseURL: string) {
  await page.goto(baseURL)
  await page.locator('[data-asset-list]').getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  await waitForFigureEdit(page)
  await page.locator('[data-inspector-tab="assistant"]').click()
  await expect(page.locator('[data-ai-input]')).toBeEnabled({ timeout: 30_000 })
}

async function openChatGptModels(page: Page) {
  await page.locator('[data-ai-pill="model"]').click()
  await page.locator('[data-ai-model-picker="trigger"]').click()
  await page.locator('[data-ai-connection="sub:codex"]').focus()
  await page.keyboard.press('ArrowRight')
  await expect(page.locator('[data-ai-model-menu="sub:codex"]')).toBeVisible()
}

async function closeMenus(page: Page) {
  for (let i = 0; i < 3 && (await page.locator('[data-ai-model-picker="trigger"]').count()) > 0; i++) {
    await page.keyboard.press('Escape')
  }
}

/** 菜单弹在面板右下：截面板那一片（过了弹出动画） */
async function shootMenu(page: Page, name: string) {
  const dir = process.env.TAVOTTO_SHOT_DIR
  if (!dir) return
  await page.waitForTimeout(600)
  await page.screenshot({ path: `${dir}/${test.info().project.name}-${name}.png`, clip: { x: 560, y: 360, width: 720, height: 360 } })
}

async function shoot(page: Page, name: string, el: ReturnType<Page['locator']>) {
  const dir = process.env.TAVOTTO_SHOT_DIR
  if (!dir) return
  const box = await el.boundingBox()
  if (!box) return
  const pad = 24
  const clip = { x: Math.max(0, box.x - pad), y: Math.max(0, box.y - pad), width: box.width + pad * 2, height: box.height + pad * 2 }
  await page.screenshot({ path: `${dir}/${test.info().project.name}-${name}.png`, clip })
}

async function sendAndFail(page: Page) {
  await page.locator('[data-ai-input]').fill('把线条加粗一点')
  await page.locator('[data-ai-send="send"]').click()
  const card = page.locator('[data-ai-error="session"]')
  await expect(card).toBeVisible({ timeout: 30_000 })
  return card
}

test('模型清单比 CLI 新、托管锁追得上：菜单末行「更新 Codex」，404 失败卡主按钮也是它', async ({ app, page }) => {
  // 托管锁当前是 0.165.0：清单 0.165.0 → 应用内装一份就够（install）；本机另有够新的一份时是改用它（switch）——两样都是一键
  const a = await app({ env: fakeOldCodex('0.150.0', '0.165.0') })
  await openAssistant(page, a.baseURL)
  await openChatGptModels(page)
  const item = page.locator('[data-ai-cli-update="install"], [data-ai-cli-update="switch"]')
  await expect(item).toBeVisible()
  await expect(item).toBeEnabled()
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme })
    await shootMenu(page, `menu-install-${scheme}`)
  }
  await closeMenus(page)
  await page.emulateMedia({ colorScheme: 'light' })

  const card = await sendAndFail(page)
  // 一句话 + 一个主按钮
  await expect(card.locator('[data-ai-cli-update-card]')).toBeVisible()
  await expect(card.locator('[data-ai-check-connection]')).toHaveCount(0)
  await expect(card.locator('[data-ai-retry]')).toHaveCount(0)
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme })
    await shoot(page, `card-install-${scheme}`, card)
  }
})

test('只能自己更新：菜单末行置灰写怎么做，失败卡主按钮换回默认型号、做法在详情里', async ({ app, page }) => {
  // 清单 0.999.0：托管锁和本机任何一份都追不上 → manual（来源 path → npm 那句）
  const a = await app({ env: fakeOldCodex('0.165.0', '0.999.0') })
  await openAssistant(page, a.baseURL)
  await openChatGptModels(page)
  const item = page.locator('[data-ai-cli-update="manual"]')
  await expect(item).toBeVisible()
  await expect(item).toHaveAttribute('data-disabled', '')
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme })
    await shootMenu(page, `menu-manual-${scheme}`)
  }
  await page.emulateMedia({ colorScheme: 'light' })
  // 选一个新型号（不是 config.toml 里的默认 gpt-5.6-sol），失败后才有「换回」
  await page.locator('[data-ai-model-item="gpt-6.1-sol"]').click()
  await closeMenus(page)
  await expect(page.locator('[data-ai-pill="model"]')).toContainText('GPT-6.1-Sol')

  const card = await sendAndFail(page)
  await expect(card.locator('[data-ai-model-switch-back]')).toBeVisible()
  await expect(card.locator('[data-ai-cli-update-card]')).toHaveCount(0)
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme })
    await shoot(page, `card-manual-${scheme}`, card)
  }
  // 怎么更新在「详情」里（正文只留一句）
  await card.locator('[data-notice-detail-toggle]').click()
  await expect(card.locator('[data-notice-detail]')).toContainText('@openai/codex')
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme })
    await shoot(page, `card-manual-detail-${scheme}`, card)
  }
  await page.emulateMedia({ colorScheme: 'light' })
  // 点「换回」：选择器回到默认型号，卡上的按钮随之消失（当前已是默认型号）
  await card.locator('[data-ai-model-switch-back]').click()
  await expect(page.locator('[data-ai-pill="model"]')).toContainText('GPT-5.6-Sol')
})
