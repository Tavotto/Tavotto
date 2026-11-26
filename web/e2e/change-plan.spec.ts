import { chmodSync, mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import type { Page } from '@playwright/test'
import { expect, test, waitForFigureEdit, type RunningApp } from './fixtures'

/**
 * 改图助手先出修改方案卡的端到端（ADR 0206 PR 4；真浏览器 + 真后端 + **假模型**）。
 *
 * 模型缝由 `tests/support/e2e_change_plan_app.py` 换成假件（`AppOptions.planModel`）：方案由假模型回固定内容，
 * 但 B0 / 候选的渲染、方案落盘、两张对比图、前端的应用 / 撤销全是真的。绝不真调模型。
 *
 *   1. 发一句图内能改的指令 → 方案卡（待确认）→「查看」弹窗 →「应用」→ 这张图的 overrides 真的写进文档（自动保存的请求体为证）、
 *      卡片「已应用」→ ⌘Z → overrides 回到原样、卡片回到待确认；
 *   2. 当前连接不支持结构化调用（同步预检判不适用 / 线程里才判出）→ 指令自动落回改脚本会话，不留方案失败卡。
 *
 * 要一个「可用」的改图助手才能发：往 PATH 里放一个假的 `codex`（同 ai-draft.spec.ts）。POSIX only。定位一律走 `data-*`，两个 locale 都跑。
 */
test.skip(process.platform === 'win32', '假 CLI 是 shell 脚本，只在 posix-e2e 腿执行')

function fakeCodexDir(): string {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'tavotto-fake-codex-'))
  mkdirSync(dir, { recursive: true })
  const bin = path.join(dir, 'codex')
  writeFileSync(bin, `#!/bin/sh\ncase "$1" in\n  --version) echo "codex-cli 0.0.1" ;;\n  login) echo "Logged in" ;;\n  *) sleep 1 ;;\nesac\n`)
  chmodSync(bin, 0o755)
  return dir
}

async function start(app: (o?: Parameters<typeof import('./fixtures').startApp>[0]) => Promise<RunningApp>, planModel: 'ok' | 'log' | 'unsupported_sync' | 'unsupported_thread') {
  return app({ planModel, env: { PATH: `${fakeCodexDir()}${path.delimiter}${process.env.PATH ?? ''}` } })
}

async function openAssistant(page: Page, baseURL: string) {
  await page.goto(baseURL)
  await page.locator('[data-asset-list]').getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  await waitForFigureEdit(page)
  await page.locator('[data-inspector-tab="assistant"]').click()
  await expect(page.locator('[data-ai-input]')).toBeEnabled({ timeout: 30_000 })
}

type Patch = { gid: string; prop: string; value: unknown }

/** 记下每一次自动保存请求体里所有面板的 overrides：文档状态的证据（与 autosaveEvidence 同一来源——真请求，不读内部 store） */
function trackAutosave(page: Page) {
  const seen: Patch[][] = []
  page.on('request', (r) => {
    if (r.method() !== 'PUT' || !/\/api\/autosave\//.test(r.url())) return
    try {
      const body = r.postDataJSON() as { canvases?: { objects?: { type?: string; overrides?: Patch[] }[] }[] }
      seen.push((body.canvases ?? []).flatMap((c) => (c.objects ?? []).filter((o) => o.type === 'panel').flatMap((o) => o.overrides ?? [])))
    } catch {
      /* 不是 JSON：忽略 */
    }
  })
  return { last: () => seen.at(-1) ?? null }
}

const hasWidth = (ps: Patch[] | null) => !!ps?.some((p) => p.prop === 'linewidth' && p.value === 4)

test('方案卡：发指令 → 待确认 → 查看 → 应用（overrides 真变了）→ ⌘Z 回到待确认', async ({ app, page }) => {
  const a = await start(app, 'ok')
  const doc = trackAutosave(page)
  await openAssistant(page, a.baseURL)
  await page.locator('[data-ai-input]').fill('把线条加粗一点')
  await page.locator('[data-ai-send="send"]').click()

  // 方案出来：待确认；没有起改脚本会话
  const card = page.locator('[data-change-plan-card]')
  await expect(card).toHaveAttribute('data-change-plan-card', 'ready', { timeout: 60_000 })
  await expect(page.locator('[data-ai-session]')).toHaveCount(0)
  expect(hasWidth(doc.last())).toBe(false) // 方案不应用就什么都不改

  // 查看：弹窗里对比
  await page.locator('[data-change-plan-view]').click()
  await expect(page.locator('[data-change-plan-dialog]')).toBeVisible()
  await page.locator('[data-change-plan-confirm]').click()

  // 应用：卡片「已应用」，文档里这张图的 overrides 真的多了线宽 4（自动保存的请求体为证）
  await expect(card).toHaveAttribute('data-change-plan-card', 'applied')
  await expect.poll(() => hasWidth(doc.last()), { timeout: 30_000 }).toBe(true)

  // ⌘Z：一步撤回，overrides 回到原样，卡片回到待确认
  await page.locator('[data-change-plan-card]').click({ position: { x: 4, y: 4 } }) // 焦点离开输入框，快捷键才落到画布
  await page.keyboard.press('ControlOrMeta+z')
  await expect(card).toHaveAttribute('data-change-plan-card', 'ready')
  await expect.poll(() => hasWidth(doc.last()), { timeout: 30_000 }).toBe(false)
})

test('对数轴下限：先问「一起改吗」，没回答前没有查看 / 应用；一起改 → 应用带上正数下限', async ({ app, page }) => {
  const a = await start(app, 'log')
  const doc = trackAutosave(page)
  await openAssistant(page, a.baseURL)
  await page.locator('[data-ai-input]').fill('把纵轴改成对数坐标')
  await page.locator('[data-ai-send="send"]').click()

  const card = page.locator('[data-change-plan-card]')
  await expect(card).toHaveAttribute('data-change-plan-card', 'ready', { timeout: 60_000 })
  await expect(page.locator('[data-change-plan-confirm-log="yes"]')).toBeVisible()
  await expect(page.locator('[data-change-plan-view]')).toHaveCount(0)
  await expect(page.locator('[data-change-plan-apply]')).toHaveCount(0)
  const shots = process.env.TAVOTTO_E2E_SHOTS // 只在要截图留档时设：目录
  if (shots) {
    for (const scheme of ['light', 'dark'] as const) {
      await page.emulateMedia({ colorScheme: scheme })
      await card.screenshot({ path: path.join(shots, `confirm-row-${scheme}.png`) })
    }
    await page.emulateMedia({ colorScheme: 'light' })
  }

  await page.locator('[data-change-plan-confirm-log="yes"]').click()
  await expect(page.locator('[data-change-plan-confirm-log]')).toHaveCount(0)
  await page.locator('[data-change-plan-apply]').click()
  await expect(card).toHaveAttribute('data-change-plan-card', 'applied')
  await expect
    .poll(() => {
      const last = doc.last() ?? []
      const lim = last.find((p) => p.prop === 'ylim')?.value as number[] | undefined
      return last.some((p) => p.prop === 'yscale' && p.value === 'log') && !!lim && Math.min(...lim) > 0
    }, { timeout: 30_000 })
    .toBe(true)
})

for (const mode of ['unsupported_sync', 'unsupported_thread'] as const) {
  test(`连接不支持结构化调用（${mode}）：指令自动落回改脚本会话，不留方案失败卡`, async ({ app, page }) => {
    const a = await start(app, mode)
    await openAssistant(page, a.baseURL)
    await page.locator('[data-ai-input]').fill('把线条加粗一点')
    await page.locator('[data-ai-send="send"]').click()
    // 改脚本会话出现（现有的会话标志）；用户的话没丢
    // 一次发送只该有一个改脚本会话、一条用户话：用计数断言，落回若重复执行会被抓到（不用 first() 掩盖）
    const session = page.locator('[data-ai-session]')
    await expect(session).toHaveCount(1, { timeout: 60_000 })
    await expect(session).toBeVisible()
    const said = page.locator('[data-ai-user]')
    await expect(said).toHaveCount(1)
    await expect(said).toContainText('把线条加粗一点')
    await expect(page.locator('[data-change-plan-card="failed"]')).toHaveCount(0)
    await expect(page.locator('[data-change-plan-card="ready"]')).toHaveCount(0)
  })
}
