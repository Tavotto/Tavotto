/**
 * 导出对话框的检查区（2026-10-16 UI 2.0 精修 03）：三个计数芯片 + 每条一行的阻断清单。
 *
 * 语言无关写法（只认稳定 data-*，文案只按页面语言挑一个词核对）：中文与英文各跑一遍——英文标签更长，
 * 一行放不下就该截断，而不是把「定位」挤出对话框。
 */
import { copyFileSync, mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, test } from './fixtures'

/** 一张两联图：(a)(b) 各有一段 6 pt 的注释——低于绝对下限 8 pt，两个子图各一条阻断（同 features.spec） */
function writeTwoPanelProject(): string {
  const dir = path.join(mkdtempSync(path.join(os.tmpdir(), 'tavotto-checks-')), 'figures')
  mkdirSync(dir, { recursive: true })
  writeFileSync(
    path.join(dir, 'fig_two.py'),
    [
      'import matplotlib',
      'matplotlib.use("Agg")',
      'import matplotlib.pyplot as plt',
      'from pathlib import Path',
      '',
      '',
      'def main():',
      '    fig, (ax_a, ax_b) = plt.subplots(1, 2, figsize=(6, 2.6))',
      '    for ax, tag in ((ax_a, "(a)"), (ax_b, "(b)")):',
      '        ax.plot([0, 1, 2, 3], [1, 3, 2, 4])',
      '        ax.text(0.5, 0.5, "tiny " + tag, transform=ax.transAxes, fontsize=6)',
      '    fig.tight_layout()',
      '    fig.savefig(Path(__file__).with_name("Fig_two.pdf"))',
      '    plt.close(fig)',
      '',
      '',
      'if __name__ == "__main__":',
      '    main()',
      '',
    ].join('\n'),
    'utf-8',
  )
  writeFileSync(
    path.join(dir, 'tavotto_registry.json'),
    JSON.stringify({ version: 1, scripts: { 'fig_two.py': { entry: 'main', cost: 'light', stems: ['Fig_two'] } } }),
    'utf-8',
  )
  copyFileSync(
    path.join(import.meta.dirname, '..', '..', 'examples', 'figures', 'Fig1_kinetics.pdf'),
    path.join(dir, 'Fig_two.pdf'),
  )
  return dir
}

test('导出检查：计数芯片只在圆点上有色，没有逐条阻断清单与警告句，点芯片带着这一档去问题面板', async ({ app, page }) => {
  const a = await app({ figures: writeTwoPanelProject() })
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(a.baseURL)
  await page.locator('[data-card="Fig_two.pdf"]').dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-element-svg] > svg')).toBeVisible({ timeout: 120_000 })
  await page.locator('[data-onboarding-anchor="export"]').click()

  const dialog = page.getByRole('dialog')
  const danger = dialog.locator('[data-count-chip="danger"]')
  await expect(danger).toHaveCount(1, { timeout: 60_000 })
  const lang = await page.locator('html').getAttribute('lang')
  await expect(danger).toContainText(lang === 'en-US' ? 'to fix' : '项要改')
  const n = Number((await danger.textContent())!.match(/\d+/)![0])
  expect(n, '两个子图各一条字号阻断').toBeGreaterThanOrEqual(2)

  // 芯片 26 高、全圆角、字与底不染色：颜色只在 6px 圆点
  const box = await danger.boundingBox()
  expect(Math.round(box!.height)).toBe(26)
  const styles = await danger.evaluate((el) => {
    const cs = getComputedStyle(el)
    const dot = el.querySelector('[aria-hidden]') as HTMLElement
    const d = getComputedStyle(dot)
    return { radius: cs.borderTopLeftRadius, dotW: dot.getBoundingClientRect().width, dotBg: d.backgroundColor, ink: cs.color, bg: cs.backgroundColor }
  })
  expect(parseFloat(styles.radius)).toBeGreaterThanOrEqual(13)
  expect(styles.dotW).toBeCloseTo(6, 0) // 6px 圆点；dialog 进场缩放动画的亚像素误差
  expect(styles.dotBg, '圆点是 danger 色，不是灰').not.toBe(styles.ink)

  // 没有逐条阻断清单、没有「定位」钮、没有任何警告句；导出照样可点
  await expect(dialog.locator('[data-blocking-issue]')).toHaveCount(0)
  await expect(dialog.getByRole('button', { name: /^(定位|Locate)$/ })).toHaveCount(0)
  await expect(dialog).not.toContainText(/投稿可能被退回|sent back/)
  await expect(dialog.locator('[data-export-start]')).toBeEnabled()

  // 点「要改」芯片：对话框让开、问题面板只留阻断
  await danger.click()
  await expect(page.locator('[data-rail="problems"]')).toHaveAttribute('aria-expanded', 'true')
  await expect(dialog).toHaveCount(0)
})
