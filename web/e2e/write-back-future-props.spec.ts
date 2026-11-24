import { execFileSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, test } from './fixtures'

/**
 * 写回撞上「更新版本的 Tavotto 做的修改」（后端 409 `write_back_future_props`）时界面给什么：不让人卡在一句红字报错上
 * （「所有用户当小白」）——写回窗口里一行灰字说清楚 + 一个能点的出口，与「这份排版来自更新的版本」同一个动作：
 * 桌面版「检查更新」，浏览器版「发给我们看看」（反馈入口关着的环境里只留这行字）。
 *
 * 后端那条路径由 `tests/test_write_back.py` 用真事务钉住；这里只看界面出口，所以写回请求本身用路由拦下回 409，
 * 原文件一个字节都不碰（断言）。浅 / 深各截一张给审阅。
 */
function library(): string {
  const dir = path.join(mkdtempSync(path.join(os.tmpdir(), 'tavotto-e2e-wbfuture-')), 'figures')
  mkdirSync(dir)
  writeFileSync(
    path.join(dir, 'fig_note.py'),
    [
      'import matplotlib',
      'matplotlib.use("Agg")',
      'import matplotlib.pyplot as plt',
      '',
      '',
      'def main():',
      '    fig, ax = plt.subplots(figsize=(12 / 2.54, 8 / 2.54))',
      '    ax.plot([0, 1, 2], [0, 1, 0.5])',
      '    ax.text(0.2, 0.8, "note", transform=ax.transAxes, fontsize=14)',
      '    fig.savefig("Fig_note.pdf")',
      '',
    ].join('\n'),
    'utf-8',
  )
  writeFileSync(
    path.join(dir, 'tavotto_registry.json'),
    JSON.stringify({ scripts: { 'fig_note.py': { entry: 'main', cost: 'light', stems: ['Fig_note'] } } }),
    'utf-8',
  )
  const py = process.env.TAVOTTO_WORKER_PYTHON || (process.platform === 'win32' ? 'python' : 'python3')
  execFileSync(py, ['-c', 'import fig_note; fig_note.main()'], { cwd: dir, timeout: 120_000 })
  return dir
}

for (const scheme of ['light', 'dark'] as const) {
  test(`写回遇到更新版本的修改：一行灰字 + 能点的出口，原文件不动（${scheme}）`, async ({ app, page }, testInfo) => {
    await page.emulateMedia({ colorScheme: scheme })
    const figs = library()
    const before = readFileSync(path.join(figs, 'Fig_note.pdf'))
    const a = await app({ figures: figs })
    // 请求可能带查询串（会话令牌等）：按路径正则拦
    await page.route(/\/api\/engine\/update_source(\?|$)/, (route) =>
      route.fulfill({
        status: 409,
        contentType: 'application/json',
        body: JSON.stringify({
          error: '这张图里有用更新版本做的修改，这一版写不回去，原文件没动。',
          code: 'write_back_future_props',
          props: ['axes_0.lines_0.bend_magic'],
        }),
      }),
    )
    await page.goto(a.baseURL)
    await page.locator('[data-card="Fig_note.pdf"]').focus({ timeout: 30_000 })
    await page.keyboard.press('Shift+Enter')
    await expect(page.locator('[data-object-id]')).toHaveCount(1, { timeout: 30_000 })
    await page.waitForTimeout(1500)
    const p = (await page.locator('[data-object-id]').boundingBox())!
    await page.mouse.click(p.x + p.width / 2, p.y + p.height / 2)
    await page.keyboard.press('Enter')
    await expect(page.locator('[data-element-svg] > svg')).toBeVisible({ timeout: 60_000 })
    await page.waitForTimeout(1500)
    // 改一处（文字往右推四下），好让「写回」有东西可写
    const t = (await page.locator('[data-element-svg] [id="axes_0.texts_0"]').boundingBox())!
    await page.mouse.click(t.x + t.width / 2, t.y + t.height / 2)
    for (let i = 0; i < 4; i++) await page.keyboard.press('ArrowRight')
    await page.waitForTimeout(1500)
    await page.keyboard.press('Escape')
    await page.keyboard.press('Escape')
    await page.mouse.click(p.x + p.width / 2, p.y + p.height / 2)
    // 写回在属性页「源文件」那一行展开的动作里
    const openBtn = page.locator('[data-write-back="open"]').first()
    if (!(await openBtn.isVisible())) await page.getByText('源文件', { exact: true }).first().click()
    await openBtn.click({ timeout: 30_000 })
    await page.locator('[data-write-back="confirm"]').click({ timeout: 30_000 })

    // 写回窗口里：一行灰字说清楚（不是红字报错），有出口时一个主按钮
    const notice = page.locator('[data-write-back-future]')
    await expect(notice).toBeVisible({ timeout: 30_000 })
    await expect(notice).toContainText('更新版本')
    await expect(page.getByRole('dialog').locator('.text-danger')).toHaveCount(0)
    const exit = notice.locator('[data-write-back-future-exit]')
    if (await exit.count()) await expect(exit).toBeVisible()
    await page.waitForTimeout(300)
    const file = testInfo.outputPath(`wb-future-${scheme}.png`)
    await page.getByRole('dialog').screenshot({ path: file })
    await testInfo.attach(`wb-future-${scheme}`, { path: file, contentType: 'image/png' })
    expect(readFileSync(path.join(figs, 'Fig_note.pdf')).equals(before), '原文件一个字节都不动').toBe(true)
  })
}
