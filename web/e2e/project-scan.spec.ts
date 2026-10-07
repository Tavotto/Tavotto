import { chmodSync, copyFileSync, existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, test } from './fixtures'
import { card, expectPageNotShifted, openDetails, pill, workPanelRect } from './prepCard'

/**
 * 导入即扫描（T02）——只有真浏览器 + 真后端才能回答的几件：
 *
 *   * 导入一个**只有脚本**的项目：几秒内右下引导卡自动弹出（T13b，页面不下移），读的是后端的真实报告（不是演示进度）；
 *     工作台照常可用，已有的静态素材立即可见；**用户脚本一次都没被执行**（哨兵文件在项目之外），弹出时也没建准备会话；
 *   * 静态项目：卡片与角标都不出现；
 *   * 目录读不动：角标说「没能检查完」，而不是静悄悄当成没有脚本；
 *   * 教程：教程进行中 coachmark 独占，引导卡不出现、也没有第二个抢焦点的东西。
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


test('只有脚本的项目：自动出现真实的检查结果，工作台可用，脚本一次都没跑', async ({ app, page }) => {
  const root = tmp('tavotto-scan-')
  const sentinel = path.join(root, 'SENTINEL_script_ran')
  const project = path.join(root, '项目 空格')
  writeScriptProject(project, sentinel)
  try {
    const a = await app({ figures: project })
    await page.setViewportSize({ width: 1400, height: 900 })
    const sessions: string[] = []
    page.on('request', (r) => {
      if (r.method() === 'POST' && new URL(r.url()).pathname === '/api/engine/preparation-sessions') sessions.push(r.url())
    })
    await page.goto(a.baseURL)
    await expect(page.locator('[data-card="Static_Fig.pdf"]')).toBeVisible({ timeout: 30_000 })
    const before = await workPanelRect(page)

    // 卡片自动弹出，点名了真实发现的脚本；页面不下移（工作面板不动）；弹出时没建准备会话
    await expect(card(page)).toHaveAttribute('data-prep-state', 'discover', { timeout: 30_000 })
    expect(await workPanelRect(page)).toEqual(before)
    await expect(card(page).locator('[data-prep-line]')).toContainText('plot.py')
    await expectPageNotShifted(page)
    // 它读的是后端的真实报告：同一时刻后端给的 phase / 目标与卡上的一致
    const report = await (await page.request.get(`${a.baseURL}/api/project/scan`)).json()
    expect(report.default_target).toBe('plot.py')
    expect(report.outcome.kind).toBe('target_found')
    await expect(card(page)).toHaveAttribute('data-scan-phase', report.phase)
    expect(report.environment.verified).toBe(false)
    expect(sessions).toEqual([]) // 弹出 ≠ 检查：没建准备会话

    // 工作台照常可用：静态素材立即可见（扫描不阻塞）
    await expect(page.locator('[data-card="Static_Fig.pdf"]')).toBeVisible()

    // 详情：脚本与依赖声明（只说声明了什么，没说装没装）；环境线索不再显示（用哪一套由程序决定）
    const details = await openDetails(page)
    await expect(details.locator('[data-scan-deps]')).toContainText('requirements.txt')
    await expect(details.locator('[data-prep-row="target:plot.py"]')).toBeVisible()
    await expect(details).not.toContainText('环境')

    // 用户脚本一次都没被执行（哨兵在项目之外）；给扫描收尾留一点时间再看
    await page.waitForTimeout(1500)
    expect(existsSync(sentinel)).toBe(false)

    // 「稍后」缩成角标；角标 × 才收起——都不取消扫描：后端的报告还在
    await card(page).locator('[data-prep-later]').click()
    await expect(card(page)).toHaveCount(0)
    await expect(pill(page)).toContainText('plot.py')
    await pill(page).locator('[data-prep-pill-close]').click()
    await expect(pill(page)).toHaveCount(0)
    const after = await page.request.get(`${a.baseURL}/api/project/scan`)
    expect(after.status()).toBe(200)
    expect((await after.json()).state).not.toBe('cancelled')
    expect(existsSync(sentinel)).toBe(false)

    // 每个项目只自动弹一次：刷新页面不再弹
    await page.reload()
    await expect(page.locator('[data-card="Static_Fig.pdf"]')).toBeVisible({ timeout: 30_000 })
    await page.waitForTimeout(1500)
    await expect(card(page)).toHaveCount(0)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('纯静态项目：没有引导卡，直接可排版', async ({ app, page }) => {
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
    await expect(card(page)).toHaveCount(0)
    await expect(pill(page)).toHaveCount(0)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('目录读不动：角标说「没能检查完」，而不是当成没有脚本', async ({ app, page }) => {
  test.skip(process.platform === 'win32', '需要 POSIX 权限位；由 posix-e2e 的 chromium 执行')
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
    await expect(pill(page)).toContainText('没能检查完', { timeout: 30_000 })
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

test('教程进行中：coachmark 独占，引导卡不出现也不抢第二份焦点', async ({ app, page }) => {
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
  await expect(card(page)).toHaveCount(0)
  await expect(pill(page)).toHaveCount(0)
  // 焦点没有落进任何引导卡相关的东西
  const inScan = await page.evaluate(() => !!document.activeElement?.closest('[data-prep-card], [data-prep-pill]'))
  expect(inScan).toBe(false)
})
