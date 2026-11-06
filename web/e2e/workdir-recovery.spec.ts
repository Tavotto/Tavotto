import { existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, test } from './fixtures'

// #1254：真实 worker 中 helper 的 glob 在沙盒扫空，但仍成功画出一张空图。
// 判据是画布上的实际数据标签及真实渲染响应，不以提示消失代替数据恢复。
for (const legacy of [false, true]) {
test(`成功空图 → 选择脚本目录 → 曲线恢复数据 → 保存并重开（${legacy ? '旧试运行' : '默认准备卡'}）`, async ({ app, page }) => {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'tavotto-workdir-recovery-'))
  const original = [
    'import glob',
    'import matplotlib.pyplot as plt',
    '',
    'def read_parts(pattern):',
    '    values = []',
    '    for filename in sorted(glob.glob(pattern)):',
    '        with open(filename, encoding="utf-8") as f:',
    '            values.extend(float(x) for x in f.read().split())',
    '    return values',
    '',
    'values = read_parts("data/run_*.txt")',
    'fig, ax = plt.subplots()',
    'ax.plot(values, label="signal")',
    'ax.set_title("points: " + str(len(values)))',
    'plt.show()',
    '',
  ].join('\n')
  mkdirSync(path.join(dir, 'data'))
  writeFileSync(path.join(dir, 'plot.py'), original)
  writeFileSync(path.join(dir, 'data', 'run_1.txt'), '3 1 4\n')
  writeFileSync(path.join(dir, 'tavotto_registry.json'), JSON.stringify({ version: 1, scripts: {} }))
  if (legacy) await page.addInitScript(() => localStorage.setItem('tavotto.preparationPanel', 'off'))
  try {
    const a = await app({ figures: dir })
    await page.goto(a.baseURL)
    const empty = page.waitForResponse((r) => new URL(r.url()).pathname === '/api/engine/render' && r.ok(), { timeout: 120_000 })
    await page.locator('[data-script-run="plot.py"]').click({ timeout: 30_000 })
    if (legacy) {
      const figure = page.locator('[data-card="runtime:plot.py#plot"]')
      await expect(figure).toBeVisible({ timeout: 120_000 })
      await figure.dblclick()
    }
    expect((await (await empty).json()).workdir_hint?.source).toBe('runtime')
    const image = page.locator('[data-element-svg] svg')
    await expect(image).toHaveCount(1)
    await expect.poll(() => image.innerHTML()).toContain('points: 0')

    await page.locator('[data-status-tone="info"][data-state="open"] [data-toast-action]').click()
    const dialog = page.locator('[data-dialog="workdir-confirm"]')
    await expect(dialog).toBeVisible()
    await dialog.locator('[data-workdir-option="project"] input').check()
    const recovered = page.waitForResponse((r) => new URL(r.url()).pathname === '/api/engine/render' && r.ok())
    await dialog.locator('[data-workdir-run]').click()
    const body = await (await recovered).json()
    expect(body.workdir_hint).toBeUndefined()
    await expect.poll(() => image.innerHTML(), { timeout: 120_000 }).toContain('points: 3')
    await expect(dialog).toBeHidden()

    const object = page.locator('[data-canvas-stage] [data-object-id]')
    await expect(object).toHaveCount(1)
    const panelId = (await object.getAttribute('data-object-id'))!
    await page.keyboard.press('ControlOrMeta+s')
    await expect(page.locator('[data-layout-save]')).toBeVisible()
    const autosaves = path.join(a.dataDir, 'layouts', '_autosave')
    await expect.poll(() => existsSync(autosaves) && readdirSync(autosaves).some((name) => {
      if (!name.endsWith('.json')) return false
      const doc = JSON.parse(readFileSync(path.join(autosaves, name), 'utf-8'))
      return doc.canvases?.some((c: { objects: { id: string; fileId?: string }[] }) =>
        c.objects.some((o) => o.id === panelId && o.fileId === 'runtime:plot.py#plot'))
    })).toBe(true)
    await page.keyboard.press('Escape')
    await page.reload()
    const restored = page.locator(`[data-object-id="${panelId}"]`)
    await expect(restored).toBeVisible({ timeout: 30_000 })
    await restored.click({ button: 'right' })
    await page.locator('[data-quick-item="edit-elements"]').click()
    await expect.poll(() => image.innerHTML(), { timeout: 120_000 }).toContain('points: 3')
    expect(readFileSync(path.join(dir, 'plot.py'), 'utf-8')).toBe(original)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})
}
