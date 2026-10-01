import { mkdirSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, test } from './fixtures'

/**
 * 数据找不到时请用户指认一次（ADR 0106）——真后端、真 worker、真浏览器。
 *
 * 形状：脚本被单独复制进项目，相对路径 `data/values.txt` 落空；数据在项目外的另一个目录里。
 * 链路：运行并发现图 → 弹「找不到脚本要读的数据」、写出脚本要的那串 → 浏览器模式粘贴数据所在
 * 文件夹 → 自动重跑 → 图按那份数据画出来（图名由数据内容决定：`values_42`）。判据的主语是脚本
 * 真正产出的图（素材库里 runtime 卡的身份），不是我们的记账。
 */

function writeProject(dir: string): void {
  mkdirSync(dir, { recursive: true })
  writeFileSync(
    path.join(dir, 'fig.py'),
    [
      'import matplotlib',
      'matplotlib.use("Agg")',
      'import matplotlib.pyplot as plt',
      '',
      'with open("data/values.txt") as f:',
      '    value = f.read().strip()',
      'fig, ax = plt.subplots()',
      'ax.plot([0, 1], [0, int(value)])',
      'fig.savefig("values_" + value + ".png")',
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

test(
  '数据找不到：弹框指认数据所在文件夹 → 记住 → 重跑按那份数据出图',
  { tag: '@feature:assets.missing-input-relink' },
  async ({ app, page }) => {
    const stamp = Date.now()
    const dir = path.join(os.tmpdir(), `tavotto-e2e-missing-${stamp}`)
    writeProject(dir)
    // 数据在项目外：脚本是单独复制进来的
    const dataRoot = path.join(os.tmpdir(), `tavotto-e2e-missing-data-${stamp}`)
    mkdirSync(path.join(dataRoot, 'data'), { recursive: true })
    writeFileSync(path.join(dataRoot, 'data', 'values.txt'), '42\n', 'utf-8')

    const a = await app({ figures: dir })
    await page.goto(a.baseURL)

    await expect(page.locator('[data-script-row="fig.py"]')).toBeVisible({ timeout: 30_000 })
    await page.locator('[data-script-run="fig.py"]').click()

    // 1) 弹框：说出脚本要的那一串
    const dialog = page.locator('[data-dialog="missing-input"]')
    await expect(dialog).toBeVisible({ timeout: 120_000 })
    await expect(dialog.locator('[data-missing-input-path]')).toHaveText('data/values.txt')

    // 2) 浏览器模式：粘贴数据所在的文件夹，确认
    const use = dialog.locator('[data-testid="missing-input-use-path"]')
    await expect(use).toBeDisabled()
    await dialog.locator('[data-testid="missing-input-path-input"]').fill(dataRoot)
    await use.click()
    await expect(dialog).toHaveCount(0)

    // 3) 自动重跑：图按那份数据画出来
    await expect(page.locator('[data-card="runtime:fig.py#values_42"]')).toBeVisible({
      timeout: 120_000,
    })
  },
)
