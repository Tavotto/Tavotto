/**
 * 导出窗口里灰掉的格式（PY / EPS）停上去要说为什么灰：说明走应用自己的气泡，不靠原生 `title`——
 * 灰掉的 `<input>` 不派发指针事件，停在复选框上原生提示永远不出（用户 2026-11-09 在 Tutorial 两图排版里撞见）。
 * 语言无关：气泡文字与行上的 `data-export-format-hint` 比。
 */
import { addSecondFigure, expect, test } from './fixtures'

test('排版整版导出：停在灰掉的 PY 复选框上，气泡说明为什么不能选', async ({ app, page }) => {
  const a = await app()
  await page.goto(a.baseURL)
  const card = page.locator('[data-card="Fig1_kinetics.pdf"]')
  await expect(card).toHaveCount(1, { timeout: 30_000 })
  await card.dblclick()
  await expect(page.locator('[data-figure-edge]')).toHaveCount(4, { timeout: 90_000 })
  await addSecondFigure(page)
  await page.keyboard.press('ControlOrMeta+e')
  const dialog = page.locator('[data-dialog="export"]')
  await expect(dialog).toBeVisible()
  const row = dialog.locator('[data-export-format="py"]')
  await expect(row).toHaveAttribute('aria-disabled', 'true')
  const hint = (await row.getAttribute('data-export-format-hint'))!
  expect(hint.length).toBeGreaterThan(0)
  await row.locator('input[type="checkbox"]').hover({ force: true })
  await expect(page.locator('[data-tooltip] > [data-tooltip-label]')).toHaveText(hint, { timeout: 5_000 })
  if (process.env.TAVOTTO_E2E_SHOT) await page.screenshot({ path: `${process.env.TAVOTTO_E2E_SHOT}/py-hint.png` })
})
