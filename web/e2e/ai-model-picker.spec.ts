import type { Page } from '@playwright/test'
import { expect, test, waitForFigureEdit, type RunningApp } from './fixtures'

/**
 * 「执行器与模型」两级选择器（2026-10-22）：第一级执行器家族（带图标），第二级该家族的型号；
 * 推理强度档位跟着型号走。capabilities 在浏览器侧换成固定的 codex(多型号) + claude，不依赖本机装了什么。
 * 语言无关写法（只认 data-* 锚点与 role），chromium 与 chromium-en 各走一遍。
 *
 * 设置环境变量 TAVOTTO_SHOT_DIR 时顺手把两级展开的样子截图存进去（人工验收用，不是断言）。
 */

const ALL = ['low', 'medium', 'high', 'xhigh', 'max', 'ultra']
const detail = (id: string, name: string, desc: string, efforts: string[], def: string) => ({
  id,
  display_name: name,
  description: desc,
  efforts,
  default_effort: def,
})

async function openAssistant(page: Page, a: RunningApp) {
  await page.route('**/api/ai/capabilities*', async (route) => {
    const real = await (await route.fetch()).json()
    const base = real.agents?.[0] ?? {}
    const common = { ...base, state: 'ready', installed: true, enabled: true, usable: true }
    const codex = {
      ...common,
      id: 'codex',
      display_name: 'Codex',
      icon_key: 'codex',
      models: ['gpt-6-astra', 'gpt-6.1-sol', 'gpt-6-luna'],
      default_model: 'gpt-6-astra',
      model_details: [
        detail('gpt-6-astra', 'GPT-6-Astra', 'Frontier intelligence for the most demanding work', ALL, 'medium'),
        detail('gpt-6.1-sol', 'GPT-6.1-Sol', 'Latest workhorse model for coding and everyday tasks', ALL, 'low'),
        detail('gpt-6-luna', 'GPT-6-Luna', 'Fast and affordable model for easier tasks', ALL.slice(0, 5), 'medium'),
      ],
      efforts: ALL,
      default_effort: 'medium',
      features: { ...(base.features ?? {}), model_selection: true, effort_selection: true },
    }
    const claude = {
      ...common,
      id: 'claude',
      display_name: 'Claude Code',
      icon_key: 'claude',
      models: ['sonnet', 'opus', 'haiku'],
      default_model: 'sonnet',
      model_details: ['Sonnet', 'Opus', 'Haiku'].map((n) => detail(n.toLowerCase(), n, '', [], null as never)),
      efforts: [],
      default_effort: null,
      features: { ...(base.features ?? {}), model_selection: true, effort_selection: false },
    }
    // 真后端现在下发 vendors / connections：界面走「连接」视图，一级是连接（ChatGPT / Claude），不再是执行器家族
    const conn = (id: string, label: string, a: typeof codex | typeof claude, route: string) => ({
      id,
      vendor: id,
      label,
      logo: id,
      state: 'ready',
      route,
      models: a.models,
      model_details: a.model_details,
      default_model: a.default_model,
      efforts: a.efforts,
      default_effort: a.default_effort,
    })
    const connections = [conn('chatgpt', 'ChatGPT', codex, 'cli:codex'), conn('claude', 'Claude', claude, 'cli:claude')]
    await route.fulfill({ json: { ...real, agents: [codex, claude], connections } })
  })
  await page.goto(a.baseURL)
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  await waitForFigureEdit(page)
  await page.getByRole('tab', { name: /改图助手|Assistant/i }).first().click()
  await expect(page.locator('[data-ai-composer]')).toBeVisible({ timeout: 30_000 })
  await expect(page.locator('[data-ai-pill="model"]')).toBeVisible({ timeout: 30_000 })
}

test('两级选择：家族带图标，→ 进型号，选中后推理强度只给该型号的档位', async ({ app, page }) => {
  await openAssistant(page, await app())
  await page.locator('[data-ai-pill="model"]').click()
  const trigger = page.locator('[data-ai-model-picker="trigger"]')
  await expect(trigger).toBeVisible()
  await trigger.click()

  const families = page.locator('[data-ai-connection]')
  await expect(families).toHaveCount(2)
  await expect(families.nth(0).locator('svg').first()).toBeVisible()
  await expect(families.nth(1).locator('svg').first()).toBeVisible()

  // 键盘：→ 打开第二级
  await page.locator('[data-ai-connection="chatgpt"]').focus()
  await page.keyboard.press('ArrowRight')
  const items = page.locator('[data-ai-model-item]')
  await expect(items).toHaveCount(3)
  const dir = process.env.TAVOTTO_SHOT_DIR
  if (dir) await page.waitForTimeout(600) // 过了弹出动画再截
  if (dir) await page.screenshot({ path: `${dir}/${test.info().project.name}-two-level.png` })

  await page.locator('[data-ai-model-item="gpt-6-luna"]').click()
  await expect(trigger).toContainText('GPT-6-Luna')
  await page.keyboard.press('Escape')
  await page.locator('[data-ai-pill="effort"]').click()
  // luna 没有 ultra 档
  const efforts = page.locator('[data-ai-effort-item]')
  await expect(efforts).toHaveCount(5)
  await expect(page.locator('[data-ai-effort-item="ultra"]')).toHaveCount(0)
})

test('Esc 逐层退出：先收子菜单（←）再关菜单，选择器不改值', async ({ app, page }) => {
  await openAssistant(page, await app())
  await page.locator('[data-ai-pill="model"]').click()
  const trigger = page.locator('[data-ai-model-picker="trigger"]')
  const before = await trigger.textContent()
  await trigger.click()
  await page.locator('[data-ai-connection="claude"]').focus()
  await page.keyboard.press('ArrowRight')
  await expect(page.locator('[data-ai-model-item]')).toHaveCount(3)
  await page.keyboard.press('ArrowLeft')
  await expect(page.locator('[data-ai-model-item]')).toHaveCount(0)
  await expect(page.locator('[data-ai-connection]')).toHaveCount(2)
  await page.keyboard.press('Escape')
  await expect(page.locator('[data-ai-connection]')).toHaveCount(0)
  await expect(trigger).toHaveText(before ?? '')
})
