import { chmodSync, copyFileSync, existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, test } from './fixtures'

/**
 * 导入即扫描（T02）——只有真浏览器 + 真后端才能回答的几件：
 *
 *   * 导入一个**只有脚本**的项目：几秒内出现准备条，读的是后端的真实报告（不是演示进度）；
 *     工作台照常可用，已有的静态素材立即可见；**用户脚本一次都没被执行**（哨兵文件在项目之外）；
 *   * 静态项目：整条不出现；
 *   * 目录读不动：条出现并说「没有检查完」，而不是静悄悄当成没有脚本；
 *   * 教程：教程进行中 coachmark 独占，扫描条不出现、也没有第二个抢焦点的东西。
 *
 * 全程不联网；哨兵与项目都在临时目录。
 */
const REPO = path.resolve(import.meta.dirname, '..', '..')
const SAMPLE_PDF = path.join(REPO, 'examples', 'figures', 'Fig1_kinetics.pdf')

function tmp(prefix: string): string {
  return mkdtempSync(path.join(os.tmpdir(), prefix))
}

/** 脚本只有运行期才知道图名；真跑起来会写哨兵（在项目**之外**，扫描写不进也读得出） */
function writeScriptProject(dir: string, sentinel: string): void {
  mkdirSync(dir, { recursive: true })
  copyFileSync(SAMPLE_PDF, path.join(dir, 'Static_Fig.pdf'))
  writeFileSync(
    path.join(dir, 'plot.py'),
    [
      'import sys',
      'import matplotlib',
      'matplotlib.use("Agg")',
      'import matplotlib.pyplot as plt',
      '',
      'def main():',
      `    open(${JSON.stringify(sentinel)}, "w").write("ran")`,
      '    fig, ax = plt.subplots()',
      '    ax.plot([0, 1], [1, 0])',
      '    fig.savefig(sys.argv[0] + ".pdf")',
      '',
      'if __name__ == "__main__":',
      '    main()',
      '',
    ].join('\n'),
    'utf-8',
  )
  writeFileSync(path.join(dir, 'requirements.txt'), 'numpy>=1.20\nmatplotlib\n', 'utf-8')
}

const bar = (page: import('@playwright/test').Page) => page.locator('[data-project-scan]')

test('只有脚本的项目：自动出现真实的检查结果，工作台可用，脚本一次都没跑', async ({ app, page }) => {
  const root = tmp('tavotto-scan-')
  const sentinel = path.join(root, 'SENTINEL_script_ran')
  const project = path.join(root, '项目 空格')
  writeScriptProject(project, sentinel)
  try {
    const a = await app({ figures: project })
    await page.setViewportSize({ width: 1400, height: 900 })
    await page.goto(a.baseURL)

    // 条出现，点名了真实发现的脚本
    await expect(bar(page)).toBeVisible({ timeout: 30_000 })
    await expect(page.locator('[data-project-scan-line]')).toContainText('plot.py')
    // 它读的是后端的真实报告：同一时刻后端给的 phase / 目标与条上的一致
    const report = await (await page.request.get(`${a.baseURL}/api/project/scan`)).json()
    expect(report.default_target).toBe('plot.py')
    expect(report.outcome.kind).toBe('target_found')
    await expect(bar(page)).toHaveAttribute('data-scan-phase', report.phase)
    expect(report.environment.verified).toBe(false)

    // 工作台照常可用：静态素材立即可见（扫描不阻塞）
    await expect(page.locator('[data-card="Static_Fig.pdf"]')).toBeVisible()

    // 详情：环境永远是「未核验」，依赖只说声明了什么
    await bar(page).getByRole('button', { name: '详情' }).click()
    // 干净的用户目录里没有环境线索时说「没有找到环境线索」；有线索时说「都还没有核验」——两句都不替它说「可以了」
    await expect(page.locator('[data-scan-env]')).toContainText(/都还没有核验|没有找到环境线索/)
    await expect(page.locator('[data-scan-deps]')).toContainText('requirements.txt')
    await expect(page.locator('[data-scan-target="plot.py"]')).toBeVisible()

    // 用户脚本一次都没被执行（哨兵在项目之外）；给扫描收尾留一点时间再看
    await page.waitForTimeout(1500)
    expect(existsSync(sentinel)).toBe(false)

    // 「关闭」只隐藏，不取消扫描：后端的报告还在
    await bar(page).getByRole('button', { name: '关闭' }).click()
    await expect(bar(page)).toHaveCount(0)
    const after = await page.request.get(`${a.baseURL}/api/project/scan`)
    expect(after.status()).toBe(200)
    expect((await after.json()).state).not.toBe('cancelled')
    expect(existsSync(sentinel)).toBe(false)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('纯静态项目：没有检查条，直接可排版', async ({ app, page }) => {
  const root = tmp('tavotto-scan-static-')
  const project = path.join(root, 'static')
  mkdirSync(project, { recursive: true })
  copyFileSync(SAMPLE_PDF, path.join(project, 'Only_Static.pdf'))
  try {
    const a = await app({ figures: project })
    await page.setViewportSize({ width: 1400, height: 900 })
    await page.goto(a.baseURL)
    await expect(page.locator('[data-card="Only_Static.pdf"]')).toBeVisible({ timeout: 30_000 })
    // 等扫描有了终局再断言「没有条」，免得断言赶在扫描之前
    await expect
      .poll(async () => (await (await page.request.get(`${a.baseURL}/api/project/scan`)).json()).state, {
        timeout: 30_000,
      })
      .toBe('complete')
    const report = await (await page.request.get(`${a.baseURL}/api/project/scan`)).json()
    expect(report.outcome.kind).toBe('static_source')
    expect(report.assets.browsable).toBe(true)
    await expect(bar(page)).toHaveCount(0)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('目录读不动：条说「没有检查完」，而不是当成没有脚本', async ({ app, page }) => {
  test.skip(process.platform === 'win32', '需要 POSIX 权限位')
  test.skip(typeof process.getuid === 'function' && process.getuid() === 0, 'root 不受权限位约束')
  const root = tmp('tavotto-scan-perm-')
  const project = path.join(root, 'p')
  mkdirSync(path.join(project, 'locked'), { recursive: true })
  copyFileSync(SAMPLE_PDF, path.join(project, 'Visible.pdf'))
  writeFileSync(path.join(project, 'locked', 'hidden.py'), 'print(1)\n', 'utf-8')
  chmodSync(path.join(project, 'locked'), 0)
  try {
    const a = await app({ figures: project })
    await page.setViewportSize({ width: 1400, height: 900 })
    await page.goto(a.baseURL)
    await expect(bar(page)).toBeVisible({ timeout: 30_000 })
    await expect(page.locator('[data-project-scan-line]')).toContainText('没能检查完')
    const report = await (await page.request.get(`${a.baseURL}/api/project/scan`)).json()
    expect(report.state).toBe('partial')
    expect(report.issues.some((i: { code: string }) => i.code === 'unreadable_dir')).toBe(true)
    // 静态素材照常可见
    await expect(page.locator('[data-card="Visible.pdf"]')).toBeVisible()
  } finally {
    chmodSync(path.join(project, 'locked'), 0o755)
    rmSync(root, { recursive: true, force: true })
  }
})

test('教程进行中：coachmark 独占，扫描条不出现也不抢第二份焦点', async ({ app, page }) => {
  test.setTimeout(240_000)
  const a = await app({ noProject: true })
  await page.setViewportSize({ width: 1400, height: 900 })
  await page.goto(a.baseURL)
  const entry = page.locator('[data-onboarding-anchor="tutorial-entry"]')
  await expect(entry).toHaveText(/用示例学一遍（带引导）/)
  await entry.click()
  const coachmark = page.locator('[data-onboarding-coachmark]')
  await expect(coachmark).toBeVisible({ timeout: 60_000 })
  await expect(coachmark).toContainText('双击这张图')
  await page.waitForTimeout(1500)
  await expect(coachmark).toHaveCount(1)
  await expect(bar(page)).toHaveCount(0)
  // 焦点没有落进任何扫描相关的东西
  const inScan = await page.evaluate(() => !!document.activeElement?.closest('[data-project-scan]'))
  expect(inScan).toBe(false)
})
