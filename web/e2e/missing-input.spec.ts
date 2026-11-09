import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, test } from './fixtures'

// 这一组钉的是**旧**的同步试运行路径（T09 起它只在本地开关关闭时走；默认的准备面板路径见
// `preparation-card.spec.ts`）。开关是本机 localStorage 偏好，在页面脚本之前写好
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('tavotto.preparationPanel', 'off'))
})

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

/**
 * 一份用户反馈的形状：脚本按自己的位置拼出数据路径，先 `exists()` 检查、找不到就自己 raise
 * `FileNotFoundError`；数据其实在别的文件夹。以前是普通的脚本报错、接不上；现在同一个弹框，指认一次就跑通
 * （exists 也查改指表，ADR 0106 修订 2026-11-09），脚本一个字节不改。
 */
test(
  '先检查再自己报找不到：弹框指认数据所在文件夹 → 重跑按那份数据出图',
  { tag: '@feature:assets.missing-input-relink' },
  async ({ app, page }) => {
    const stamp = Date.now()
    const dir = path.join(os.tmpdir(), `tavotto-e2e-missing-check-${stamp}`)
    mkdirSync(dir, { recursive: true })
    const source = [
      'from pathlib import Path',
      'import matplotlib',
      'matplotlib.use("Agg")',
      'import matplotlib.pyplot as plt',
      '',
      'BASE_DIR = Path(__file__).resolve().parent',
      '',
      'def load(path):',
      '    if not path.exists():',
      '        raise FileNotFoundError(f"Data file not found: {path}")',
      '    return path.read_text().strip()',
      '',
      'value = load(BASE_DIR / "values.txt")',
      'fig, ax = plt.subplots()',
      'ax.plot([0, 1], [0, int(value)])',
      'fig.savefig("values_" + value + ".png")',
      '',
    ].join('\n')
    writeFileSync(path.join(dir, 'fig.py'), source, 'utf-8')
    writeFileSync(
      path.join(dir, 'tavotto_registry.json'),
      JSON.stringify({ version: 1, scripts: {} }),
      'utf-8',
    )
    const dataRoot = path.join(os.tmpdir(), `tavotto-e2e-missing-check-data-${stamp}`)
    mkdirSync(dataRoot, { recursive: true })
    writeFileSync(path.join(dataRoot, 'values.txt'), '42\n', 'utf-8')

    const a = await app({ figures: dir })
    await page.goto(a.baseURL)
    await expect(page.locator('[data-script-row="fig.py"]')).toBeVisible({ timeout: 30_000 })
    await page.locator('[data-script-run="fig.py"]').click()

    const dialog = page.locator('[data-dialog="missing-input"]')
    await expect(dialog).toBeVisible({ timeout: 120_000 })
    await expect(dialog.locator('[data-missing-input-path]')).toHaveText(/values\.txt$/)
    // 是「指认一次就记住」的那条路，不是改写脚本
    await expect(dialog.locator('[data-missing-input-rewrite-hint]')).toHaveCount(0)

    await dialog.locator('[data-testid="missing-input-path-input"]').fill(dataRoot)
    await dialog.locator('[data-testid="missing-input-use-path"]').click()
    await expect(dialog).toHaveCount(0)

    await expect(page.locator('[data-card="runtime:fig.py#values_42"]')).toBeVisible({
      timeout: 120_000,
    })
    expect(readFileSync(path.join(dir, 'fig.py'), 'utf-8')).toBe(source)
  },
)
