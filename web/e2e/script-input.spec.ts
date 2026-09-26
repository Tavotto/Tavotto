import { mkdirSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, test } from './fixtures'

/**
 * 脚本里的 `input()` 在界面上作答（ADR 0099）——真后端、真 worker、真浏览器。
 *
 * 链路：脚本打印编号清单再 `input()` → 弹框里看得到清单与提示 → 填「1,2」→ 图按选择画出来
 * （图名由答案决定：`sel_1_2`）→ 重跑不弹框、自动用「1,2」并给轻提示 → 在「记住的输入」里改成「2」
 * → 重跑后图变了（`sel_2`）。判据的主语是脚本真正产出的图（素材库里 runtime 卡的身份），不是我们的记账。
 */

function writeProject(dir: string): void {
  mkdirSync(dir, { recursive: true })
  writeFileSync(
    path.join(dir, 'pick.py'),
    [
      'import matplotlib',
      'matplotlib.use("Agg")',
      'import matplotlib.pyplot as plt',
      '',
      'print("1. a")',
      'print("2. b")',
      'choice = input("choose: ")',
      'picked = [int(x) for x in choice.split(",")]',
      'fig, ax = plt.subplots()',
      'for n in picked:',
      '    ax.plot([0, 1], [0, n], label=f"series {n}")',
      'ax.legend()',
      'fig.savefig("sel_" + "_".join(str(n) for n in picked) + ".png")',
      '',
    ].join('\n'),
    'utf-8',
  )
  writeFileSync(
    path.join(dir, 'tavotto_registry.json'),
    JSON.stringify({ version: 1, scripts: {} }),
    'utf-8',
  )
}

test('input()：弹框作答 → 记住 → 重跑自动回填 → 改答案 → 图变了', async ({ app, page }) => {
  const dir = path.join(os.tmpdir(), `tavotto-e2e-input-${Date.now()}`)
  writeProject(dir)
  const a = await app({ figures: dir })
  await page.goto(a.baseURL)

  await expect(page.getByText('pick.py').first()).toBeVisible({ timeout: 30_000 })
  await page.getByRole('button', { name: '运行 pick.py 并发现图' }).click()

  // 1) 弹框：编号清单 + 提示
  const dialog = page.locator('[data-dialog="script-input"]')
  await expect(dialog).toBeVisible({ timeout: 120_000 })
  await expect(dialog.locator('[data-script-input-output]')).toContainText('1. a')
  await expect(dialog.locator('[data-script-input-output]')).toContainText('2. b')
  await expect(dialog.locator('[data-script-input-prompt]')).toHaveText('choose: ')

  // 2) 填「1,2」→ 图按选择画出来
  await dialog.locator('[data-script-input-answer]').fill('1,2')
  await dialog.locator('[data-script-input-submit]').click()
  await expect(dialog).toHaveCount(0)
  await expect(page.getByText('已发现 1 张图')).toBeVisible({ timeout: 120_000 })
  await expect(page.locator('[data-card="runtime:pick.py#sel_1_2"]')).toBeVisible({
    timeout: 30_000,
  })

  // 3) 重跑：不弹框，自动用「1,2」，给一条轻提示
  await page.getByRole('button', { name: '重新运行 pick.py' }).click()
  const notice = page.locator('[data-script-input-autofilled]')
  await expect(notice).toContainText('1,2', { timeout: 120_000 })
  await expect(page.getByText('已发现 1 张图')).toBeVisible({ timeout: 120_000 })
  await expect(dialog).toHaveCount(0)

  // 4) 在「记住的输入」里改成「2」→ 重跑后图变了
  await page.locator('[data-script-answers="pick.py"]').click()
  const manager = page.locator('[data-dialog="script-answers"]')
  await expect(manager).toBeVisible()
  await expect(manager).toContainText('choose: ')
  const answer = manager.getByRole('textbox', { name: '你的回答' })
  await expect(answer).toHaveValue('1,2')
  await answer.fill('2')
  await manager.getByRole('button', { name: '保存并重新运行' }).click()
  await expect(page.locator('[data-card="runtime:pick.py#sel_2"]')).toBeVisible({
    timeout: 120_000,
  })
  await expect(dialog).toHaveCount(0)
})
