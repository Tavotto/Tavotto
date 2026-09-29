import { mkdirSync, readdirSync, readFileSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, test } from './fixtures'

/**
 * 改指表救不回的数据路径，经确认改写脚本里的那串（ADR 0110）——真后端、真 worker、真浏览器。
 *
 * 形状：脚本写死一个不存在的绝对路径，先 `exists()` 判空再 `sys.exit`（改指救不回的「探路」），再读它。
 * 数据在另一个目录里。链路：运行并发现图 → 弹「找不到脚本要读的数据」（没有「记住位置」那条路）→
 * 浏览器模式粘贴数据所在文件夹 →「预览要改的地方」→ 确认页写出要改的文件与逐行 diff →
 * 不勾选不能改 → 勾选 →「修改脚本」→ 自动重跑，图按那份数据画出来（图名由数据决定：`values_42`）。
 * 判据的主语：磁盘上的脚本真的只换了那两串、项目里有一份逐字节的原件备份，以及脚本真正产出的图。
 */

const OLD = '/nonexistent-tavotto-e2e/lab/data/values.txt'

function writeProject(dir: string): string {
  mkdirSync(dir, { recursive: true })
  const source = [
    'import os, sys',
    'import matplotlib',
    'matplotlib.use("Agg")',
    'import matplotlib.pyplot as plt',
    '',
    `if not os.path.exists("${OLD}"):`,
    '    sys.exit("找不到数据")',
    `with open("${OLD}") as f:`,
    '    value = f.read().strip()',
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
  return source
}

test(
  '探路找不到数据：预览逐行改动 → 勾选确认 → 改写脚本、留备份 → 重跑按那份数据出图',
  { tag: '@feature:assets.missing-input-rewrite' },
  async ({ app, page }, testInfo) => {
    const stamp = Date.now()
    const dir = path.join(os.tmpdir(), `tavotto-e2e-rewrite-${stamp}`)
    const original = writeProject(dir)
    const dataRoot = path.join(os.tmpdir(), `tavotto-e2e-rewrite-data-${stamp}`)
    mkdirSync(path.join(dataRoot, 'data'), { recursive: true })
    writeFileSync(path.join(dataRoot, 'data', 'values.txt'), '42\n', 'utf-8')

    const a = await app({ figures: dir })
    await page.goto(a.baseURL)

    await expect(page.locator('[data-script-row="fig.py"]')).toBeVisible({ timeout: 30_000 })
    await page.locator('[data-script-run="fig.py"]').click()

    // 1) 弹框：说出脚本要的那一串；是探路，给的是改写的出口
    const dialog = page.locator('[data-dialog="missing-input"]')
    await expect(dialog).toBeVisible({ timeout: 120_000 })
    await expect(dialog.locator('[data-missing-input-path]')).toHaveText(OLD)
    // 默认只有一句话 + 主按钮；「会改写脚本」的说明在折叠的「详情」里，展开才看得见
    await expect(dialog.locator('[data-missing-input-rewrite-hint]')).toBeHidden()
    await dialog.locator('[data-missing-input-details] summary').click()
    await expect(dialog.locator('[data-missing-input-rewrite-hint]')).toBeVisible()

    // 2) 浏览器模式：粘贴数据所在的文件夹 → 预览（脚本一个字节都不改）
    await dialog.locator('[data-testid="missing-input-path-input"]').fill(dataRoot)
    await dialog.locator('[data-testid="missing-input-use-path"]').click()
    const confirm = page.locator('[data-dialog="missing-input-rewrite"]')
    await expect(confirm).toBeVisible({ timeout: 30_000 })
    expect(readFileSync(path.join(dir, 'fig.py'), 'utf-8')).toBe(original)
    await expect(confirm.locator('[data-rewrite-warning]')).toContainText('fig.py')
    // exists 与连带的 open 同一份 diff
    await expect(confirm.locator('[data-rewrite-rows] li')).toHaveCount(2)
    await testInfo.attach('确认界面', {
      body: await confirm.screenshot(),
      contentType: 'image/png',
    })

    // 3) 不勾选不能改；勾选之后「修改脚本」
    const apply = confirm.locator('[data-testid="missing-input-rewrite-apply"]')
    await expect(apply).toBeDisabled()
    await confirm.locator('[data-testid="missing-input-rewrite-confirm"]').check()
    await apply.click()
    await expect(confirm).toHaveCount(0)

    // 4) 磁盘上只换了那两串；原件逐字节备份在项目里
    const edited = readFileSync(path.join(dir, 'fig.py'), 'utf-8')
    const target = path.join(dataRoot, 'data', 'values.txt').split(path.sep).join('/')
    expect(edited).toBe(original.split(OLD).join(target))
    // 备份目录名是「可读前半 + 路径哈希」（`fig.py-<12 位>`）：按前缀找唯一那一个，不写死布局
    const backupRoot = path.join(dir, 'tavottofile', 'script-backups')
    const slugs = readdirSync(backupRoot).filter((name) => name.startsWith('fig.py-'))
    expect(slugs).toHaveLength(1)
    const backups = path.join(backupRoot, slugs[0])
    const [stampDir] = readdirSync(backups)
    expect(readFileSync(path.join(backups, stampDir, 'original.py'), 'utf-8')).toBe(original)

    // 5) 自动重跑：图按那份数据画出来
    await expect(page.locator('[data-card="runtime:fig.py#values_42"]')).toBeVisible({
      timeout: 120_000,
    })
  },
)
