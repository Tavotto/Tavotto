import type { Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, test } from './fixtures'

/**
 * 「把这些修改用到同脚本的其他图…」的真浏览器全程（ADR 0037 末节，设计稿 C5 / C11）：
 * **真脚本、真两张图、真后端写回**。语言无关（走 data-* 锚点，不认文案），en-US 同跑。
 *
 * 夹具：一个脚本 `two.py` 一次 savefig 两张图 Fig_a / Fig_b（登记表里同一个脚本）。
 * Fig_a 在画布上，Fig_b 不在——所以「同步并写回」走的是标准写回窗口的 detached 模式。
 *
 * 主语：右键菜单项 `[data-quick-item="sync-overrides"]`（出现条件）、写回窗口确认钮、
 * 目标图 Fig_b.pdf 的**磁盘字节**（写回后必须变）；时刻：写回事务提交、窗口出结果之后。
 */

function twoFigureLibrary(): string {
  const dir = path.join(mkdtempSync(path.join(os.tmpdir(), 'tavotto-e2e-sync-')), 'figures')
  mkdirSync(dir)
  writeFileSync(
    path.join(dir, 'two.py'),
    [
      'import matplotlib',
      'matplotlib.use("Agg")',
      'import matplotlib.pyplot as plt',
      'import numpy as np',
      '',
      '',
      'def main():',
      '    t = np.linspace(0, 10, 100)',
      '    for stem, fn in (("Fig_a", np.sin), ("Fig_b", np.cos)):',
      '        fig, ax = plt.subplots(figsize=(8 / 2.54, 8 / 2.54 * 0.72))',
      '        fig.subplots_adjust(left=0.2, bottom=0.15, right=0.85, top=0.85)',
      '        ax.plot(t, fn(t), label=stem)',
      '        ax.set_title(stem)',
      '        ax.legend(loc="upper right")',
      '        fig.savefig(stem + ".pdf")',
      '        plt.close(fig)',
      '',
    ].join('\n'),
    'utf-8',
  )
  writeFileSync(
    path.join(dir, 'tavotto_registry.json'),
    JSON.stringify({
      scripts: { 'two.py': { entry: 'main', cost: 'light', stems: ['Fig_a', 'Fig_b'] } },
    }),
    'utf-8',
  )
  // 图要先在磁盘上：素材库扫的是 figures 目录里已有的 PDF（与 arrow-nudge 的夹具同一套做法）
  const fallback = process.platform === 'win32' ? 'python' : 'python3'
  const py = process.env.TAVOTTO_WORKER_PYTHON || fallback
  execFileSync(py, ['-c', 'import two; two.main()'], { cwd: dir, timeout: 120_000 })
  return dir
}

const panelBox = (page: Page) =>
  page.evaluate(() => {
    const all = document.querySelectorAll('[data-object-id]')
    if (all.length !== 1) throw new Error(`画布上应恰有一个对象，实际 ${all.length}`)
    const r = all[0].getBoundingClientRect()
    return { x: r.x, y: r.y, w: r.width, h: r.height }
  })

const timingsCount = (page: Page) =>
  page.evaluate(() => {
    const w = window as unknown as { __MM_PREVIEW_TIMINGS__?: unknown[] }
    return w.__MM_PREVIEW_TIMINGS__?.length ?? 0
  })

test('右键同步修改 → 选 Fig_b → 同步并写回 → 标准写回窗口 → Fig_b 磁盘文件真的变了', async ({ app, page }, testInfo) => {
  const dir = twoFigureLibrary()
  const a = await app({ figures: dir })
  await page.goto(a.baseURL)
  const card = page.locator('[data-card="Fig_a.pdf"]')
  await card.focus({ timeout: 30_000 })
  await page.keyboard.press('Shift+Enter')
  await expect(page.locator('[data-object-id]')).toHaveCount(1, { timeout: 30_000 })
  await page.waitForTimeout(1500)

  // 没有图内修改：右键菜单里没有同步项（即使素材库里有兄弟图）
  let p = await panelBox(page)
  await page.mouse.click(p.x + p.w / 2, p.y + p.h / 2, { button: 'right' })
  await expect(page.locator('[data-quick-menu="panel"]')).toBeVisible()
  await expect(page.locator('[data-quick-item="sync-overrides"]')).toHaveCount(0)
  await page.keyboard.press('Escape')

  // 进图内编辑，挪一下图例（一处图内修改）
  await page.mouse.click(p.x + p.w / 2, p.y + p.h / 2)
  await page.keyboard.press('Enter')
  await expect(page.locator('[data-element-svg] > svg')).toBeVisible({ timeout: 60_000 })
  await page.waitForTimeout(1500)
  const lg = await page.evaluate(() => {
    const r = document.querySelector('[data-element-svg] [id="axes_0.legend"]')!.getBoundingClientRect()
    return { x: r.x, y: r.y, w: r.width, h: r.height }
  })
  await page.mouse.click(lg.x + lg.w / 2, lg.y + lg.h / 2)
  await page.waitForTimeout(300)
  const before = await timingsCount(page)
  await page.keyboard.press('ArrowUp')
  await expect.poll(() => timingsCount(page), { timeout: 60_000 }).toBeGreaterThan(before)
  await page.waitForTimeout(300)
  await page.keyboard.press('Escape')
  await page.keyboard.press('Escape')

  // 有修改 + 有兄弟图：右键菜单出现同步项
  p = await panelBox(page)
  await page.mouse.click(p.x + p.w / 2, p.y + p.h / 2, { button: 'right' })
  const item = page.locator('[data-quick-item="sync-overrides"]')
  await expect(item).toHaveCount(1)
  await item.click()

  // 选目标图：只列兄弟图（Fig_b），不含它自己
  const dlg = page.locator('[data-dialog="sync-overrides"]')
  await expect(dlg).toBeVisible()
  await expect(dlg.locator('[data-sync-target]')).toHaveCount(1)
  await dlg.locator('[data-sync-target="Fig_b.pdf"]').click()

  // 映射结果 → 目标不在画布上 → 同步并写回（打开标准写回窗口，此刻磁盘一个字没动）
  const pdfB = path.join(dir, 'Fig_b.pdf')
  const bytes0 = readFileSync(pdfB)
  const mtime0 = statSync(pdfB).mtimeMs
  const toWrite = page.locator('[data-sync="write-back"]')
  await expect(toWrite).toBeEnabled({ timeout: 60_000 })
  await toWrite.click()
  const confirm = page.locator('[data-write-back="confirm"]')
  await expect(confirm).toHaveCount(1)
  expect(statSync(pdfB).mtimeMs).toBe(mtime0)

  const file = testInfo.outputPath('sync-write-back-dialog.png')
  await page.locator('[data-dialog]:has([data-write-back="confirm"])').screenshot({ path: file })
  await testInfo.attach('sync-write-back-dialog', { path: file, contentType: 'image/png' })

  // 真写回：事务 prepare → verify → commit 全过，结果页出现；Fig_b.pdf 真的被改写
  await confirm.click()
  // 确认钮换成结果页的「完成」= 事务提交了（目标图热态不可比，`fresh_only` 时没有校验行，不断言它）
  await expect(confirm).toHaveCount(0, { timeout: 180_000 })
  await expect.poll(() => statSync(pdfB).mtimeMs, { timeout: 180_000 }).toBeGreaterThan(mtime0)
  expect(readFileSync(pdfB).equals(bytes0), '写回后 Fig_b.pdf 的内容应当变了').toBe(false)
  // 来源图 Fig_a 不受影响：只写目标
  expect(statSync(path.join(dir, 'Fig_a.pdf')).mtimeMs).toBeLessThan(mtime0 + 1)

  // 窗口走到结果页，关掉后整条流程结束（同步窗口不回来）
  await page.keyboard.press('Escape')
  await expect(page.locator('[data-dialog="sync-overrides"]')).toHaveCount(0)
})
