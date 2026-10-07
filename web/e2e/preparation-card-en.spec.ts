import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, test } from './fixtures'
import { card, expectPageNotShifted, pill, primary, workPanelRect } from './prepCard'

/**
 * 准备引导卡（T13b）的英文一遍：en-US 文案同样极简（一句话、一个主按钮），可达名与布局在英文下照样成立。
 * 只有一个用 argparse 的绘图脚本（一个必填参数）：卡片自动弹出 → Get ready → 1 argument missing（Continue 置灰）→
 * 填上 → Continue → Ready to run → Run → Drew 1 figure → Edit（卡片收起）。真后端、真 worker；全程不联网。
 */
test.use({ locale: 'en-US' })

const SCRIPT = [
  'import argparse',
  'import matplotlib',
  'matplotlib.use("Agg")',
  'import matplotlib.pyplot as plt',
  '',
  'def main():',
  '    p = argparse.ArgumentParser()',
  '    p.add_argument("--scale", type=float, required=True, help="Scale factor")',
  '    a = p.parse_args()',
  '    fig, ax = plt.subplots(figsize=(3, 2))',
  '    ax.plot([0, 1, 2], [0, a.scale, 0])',
  '    fig.savefig("curve.pdf")',
  '',
  'if __name__ == "__main__":',
  '    main()',
  '',
].join('\n')

test('English guide card: one sentence, one primary button, through to editing', async ({ app, page }) => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'tavotto-prep-en-'))
  const project = path.join(root, 'proj')
  mkdirSync(project, { recursive: true })
  writeFileSync(path.join(project, 'plot.py'), SCRIPT, 'utf-8')
  try {
    const a = await app({ figures: project })
    await page.setViewportSize({ width: 1400, height: 900 })
    await page.goto(a.baseURL)
    await expect(page.locator('[data-work-panel]')).toBeVisible({ timeout: 30_000 })
    const before = await workPanelRect(page)

    await expect(card(page)).toHaveAttribute('data-prep-state', 'discover', { timeout: 30_000 })
    await expect(card(page).locator('[data-prep-line]')).toHaveText('Found plot script plot.py')
    await expect(primary(page)).toHaveText('Get ready')
    await expect(card(page).locator('[data-prep-later]')).toHaveText('Later')
    expect(await workPanelRect(page)).toEqual(before)
    await expectPageNotShifted(page)

    await primary(page).click()
    await expect(card(page)).toHaveAttribute('data-prep-state', 'args', { timeout: 60_000 })
    await expect(card(page).locator('[data-prep-line]')).toHaveText('1 argument missing')
    await expect(primary(page)).toHaveText('Continue')
    await expect(primary(page)).toBeDisabled()
    await expect(card(page)).not.toContainText('Ready to run')
    await card(page).getByRole('textbox', { name: 'Value 1 of --scale' }).fill('2')
    await expect(card(page).locator('[data-prep-line]')).toHaveText('Arguments filled in')
    await primary(page).click()
    await expect(card(page)).toHaveAttribute('data-prep-state', 'ready', { timeout: 60_000 })
    await expect(card(page).locator('[data-prep-line]')).toHaveText('Ready to run')
    await expect(primary(page)).toHaveText('Run')

    await primary(page).click()
    await expect(card(page)).toHaveAttribute('data-prep-state', 'completed', { timeout: 120_000 })
    await expect(card(page).locator('[data-prep-line]')).toHaveText('Drew 1 figure')
    await expect(primary(page)).toHaveText('Edit')
    // 每张卡至多一个黑色主按钮
    await expect(card(page).locator('button.bg-ink')).toHaveCount(1)
    await primary(page).click()
    await expect(card(page)).toHaveCount(0)
    await expect(pill(page)).toHaveCount(0)
    await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 120_000 })
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})
