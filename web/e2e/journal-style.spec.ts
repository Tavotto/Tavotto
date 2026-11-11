import { readFileSync } from 'node:fs'
import path from 'node:path'
import { expect, test, waitForDocumentReady } from './fixtures'

/**
 * 「按期刊调整样式」（`docs/features/registry.json` 的 `style.journal-adjust`）：项目按期刊检查、画布上的字号不合期刊时，
 * 左栏样式面板给一行「N 项不合 X」+「按期刊调整」；点了是一次可撤销的修改（规范本身永远不改图，ADR 0029）。
 *
 * 用 golden 向量里的夹具目录（`fx-zeta`：字号 5–5 pt，不是任何真实期刊）。判据只认用户看得见的结果：
 * 那一行出现、点完消失且样式面板里的字号变成 5 pt、撤销一步那一行回来。
 */
const FIXTURE = JSON.parse(
  readFileSync(path.resolve(import.meta.dirname, '..', '..', 'tests', 'golden', 'journal_vectors.json'), 'utf8'),
).catalog as unknown

test.beforeEach(async ({ page }) => {
  await page.addInitScript((catalog) => {
    try {
      localStorage.setItem('tavotto.journalCatalog', JSON.stringify(catalog))
      localStorage.setItem('tavotto.dev.journalCatalogUi', '1')
    } catch {
      /* 存储不可用时照常跑 */
    }
  }, FIXTURE)
})

test(
  '按期刊调整样式：不合期刊的字号一键调到期刊范围，一次撤销全回去',
  { tag: '@feature:style.journal-adjust' },
  async ({ app, page }) => {
    const a = await app()
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto(a.baseURL)
    await page.locator('[data-card="Fig1_kinetics.pdf"]').dblclick({ timeout: 30_000 })
    await waitForDocumentReady(page)

    await page.locator('[data-rail="settings"]').click()
    const shell = page.locator('[data-settings-shell]')
    await expect(shell).toBeVisible()
    await shell.locator('[data-section="spec"]').click()
    await page.locator('[data-journal-picker]').click()
    await page.locator('[data-journal-search]').fill('fixture zeta')
    await page.locator('[data-journal-option="fx-zeta"]').click()
    await expect(async () => {
      if (await shell.count()) await page.keyboard.press('Escape')
      await expect(shell).toHaveCount(0, { timeout: 1_500 })
    }).toPass({ timeout: 15_000 })

    await page.locator('[data-rail="style"]').click()
    const row = page.locator('[data-style-journal]')
    await expect(row).toBeVisible({ timeout: 30_000 })
    await expect(row).toContainText('Fixture Zeta')

    await row.locator('[data-style-journal-adjust]').click()
    await expect(row).toHaveCount(0, { timeout: 30_000 })
    // 跟随的是一份新样式（名字就是期刊名），刻度字号在面板里读得到 5 pt
    await expect(page.locator('[data-style-apply-summary]')).toContainText('Fixture Zeta')

    await page.keyboard.press('ControlOrMeta+z')
    await expect(row).toBeVisible({ timeout: 30_000 })
  },
)
