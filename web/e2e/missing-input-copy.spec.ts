import { execFileSync } from 'node:child_process'
import { chmodSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, test } from './fixtures'
import { card, primary } from './prepCard'

/**
 * native 缺数据时，把用户指认过的数据复制到脚本要读的原路径（ADR 0173）——真后端、真 native 运行、真浏览器。
 *
 * 形状：脚本读一个**绝对路径**的数据（项目外、目录在、文件不在），并把图存成 PNG 后用 PIL 读回——safe 的守卫拦下读回，
 * 浏览器模式不静默回退，卡片给「允许并运行」（要页面会话 cookie，所以这里开着会话认证起），用户点一次才以 native 重跑。
 * 用户此前已经指认过那份数据（改指规则），但 native 不应用改指，所以读原路径落空。
 * 判据的主语是磁盘上的真文件：目标路径里真的出现了与数据逐字节相同的文件、重跑出的图名由数据内容决定（`values_42`）。
 */

function setup(root: string) {
  const project = path.join(root, 'proj')
  const moved = path.join(root, 'moved')
  const wanted = path.join(root, 'wanted')
  for (const d of [project, moved, wanted]) mkdirSync(d, { recursive: true })
  writeFileSync(path.join(moved, 'values.txt'), '42\n', 'utf-8')
  const target = path.join(wanted, 'values.txt')
  writeFileSync(
    path.join(project, 'fig.py'),
    [
      'import matplotlib',
      'matplotlib.use("Agg")',
      'import matplotlib.pyplot as plt',
      'from PIL import Image',
      '',
      `with open(${JSON.stringify(target)}) as f:`,
      '    value = f.read().strip()',
      'fig, ax = plt.subplots()',
      'ax.plot([0, 1], [0, int(value)])',
      'name = "values_" + value + ".png"',
      'fig.savefig(name)',
      'Image.open(name).load()',
      '',
    ].join('\n'),
    'utf-8',
  )
  writeFileSync(path.join(project, 'tavotto_registry.json'), JSON.stringify({ version: 1, scripts: {} }), 'utf-8')
  return { project, moved, wanted, target }
}

/** 让目录写不进去：POSIX 用权限位，Windows 用真 ACL（对 Everyone 拒绝创建文件） */
function lockDir(dir: string) {
  if (process.platform === 'win32') execFileSync('icacls', [dir, '/deny', '*S-1-1-0:(OI)(CI)(WD,AD)'])
  else chmodSync(dir, 0o555)
}
function unlockDir(dir: string) {
  if (process.platform === 'win32') execFileSync('icacls', [dir, '/remove:d', '*S-1-1-0'])
  else chmodSync(dir, 0o755)
}

/** 开着认证起、用登录地址换 cookie 进页面；点一次「允许并运行」让脚本以 native 跑到缺数据 */
async function reachCopyCard(
  a: { baseURL: string; loginURL: string | null },
  page: import('@playwright/test').Page,
  target: string,
  chosen: string,
) {
  await page.setViewportSize({ width: 1400, height: 900 })
  await page.goto(a.loginURL!)
  await expect(page.locator('[data-work-panel]')).toBeVisible({ timeout: 30_000 })
  await pointAtData(page, a.baseURL, target, chosen)
  await page.reload()
  await expect(page.locator('[data-script-row="fig.py"]')).toBeVisible({ timeout: 30_000 })
  await page.locator('[data-script-run="fig.py"]').click()
  await expect(primary(page)).toHaveAttribute('data-prep-primary', 'confirm_native_run', { timeout: 180_000 })
  await primary(page).click()
  await expect(card(page)).toHaveAttribute('data-prep-state', 'missing_data', { timeout: 180_000 })
}

async function pointAtData(page: import('@playwright/test').Page, baseURL: string, target: string, chosen: string) {
  const res = await page.request.post(`${baseURL}/api/engine/input-remap`, {
    data: { requested: target, chosen, chosen_kind: 'file' },
  })
  expect(res.ok()).toBe(true)
}

test(
  '缺数据 · 复制：native 读不到原路径 → 复制过去再运行 → 文件出现在原路径、重跑出图',
  { tag: '@feature:assets.missing-input-copy' },
  async ({ app, page }) => {
    const root = path.join(os.tmpdir(), `tavotto-e2e-copy-${Date.now()}`)
    const { project, moved, target } = setup(root)
    const a = await app({ figures: project, auth: true })
    await reachCopyCard(a, page, target, path.join(moved, 'values.txt'))

    // c1：一句话 + 主「复制过去再运行」+ 次「换一份数据」，没有 AI 入口
    await expect(primary(page)).toHaveAttribute('data-prep-primary', 'copy_run')
    await expect(card(page).locator('[data-prep-alt="other_data"]')).toBeVisible()
    await expect(card(page).locator('[data-prep-ai-fix]')).toHaveCount(0)
    expect(existsSync(target)).toBe(false) // 提议本身什么都不写

    await primary(page).click()

    // 文件真的出现在脚本要读的路径，内容与指认的那份逐字节相同；重跑按那份数据画出图
    await expect.poll(() => existsSync(target), { timeout: 60_000 }).toBe(true)
    expect(readFileSync(target, 'utf-8')).toBe('42\n')
    expect(readFileSync(path.join(moved, 'values.txt'), 'utf-8')).toBe('42\n')
    // 重跑：浏览器模式仍先走 safe、再被守卫拦下，用户再点一次「允许并运行」；这一次 native 读得到原路径
    await expect(primary(page)).toHaveAttribute('data-prep-primary', 'confirm_native_run', { timeout: 180_000 })
    await primary(page).click()
    await expect(card(page)).toHaveAttribute('data-prep-state', 'completed', { timeout: 180_000 })
    await expect(card(page)).toContainText('values_42') // 图名由数据内容决定：读到的就是复制过去的那份
  },
)

test(
  '缺数据 · 复制：目标目录写不进去 → 「授权后复制」，且「改脚本里的路径」点得到',
  { tag: '@feature:assets.missing-input-copy' },
  async ({ app, page }) => {
    const root = path.join(os.tmpdir(), `tavotto-e2e-copy-ro-${Date.now()}`)
    const { project, moved, wanted, target } = setup(root)
    const a = await app({ figures: project, auth: true })
    await reachCopyCard(a, page, target, path.join(moved, 'values.txt'))
    await expect(primary(page)).toHaveAttribute('data-prep-primary', 'copy_run')

    lockDir(wanted)
    try {
      await primary(page).click()
      await expect(primary(page)).toHaveAttribute('data-prep-primary', 'grant_copy', { timeout: 60_000 })
      const rewrite = card(page).locator('[data-prep-alt="rewrite_path"]')
      await expect(rewrite).toBeVisible()
      await expect(rewrite).toBeEnabled()
      await expect(card(page).locator('[data-prep-ai-fix]')).toBeVisible()
      expect(existsSync(target)).toBe(false)
      // 「改脚本里的路径」直接进逐行预览（用已指认的源，不弹选择器）
      await rewrite.click()
      await expect(page.locator('[data-dialog="missing-input-rewrite"], [data-dialog="missing-input"]').first()).toBeVisible({
        timeout: 30_000,
      })
    } finally {
      unlockDir(wanted)
    }
  },
)
