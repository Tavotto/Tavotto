import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, openElementsTab, test } from './fixtures'
import { autosavePanelEvidence } from './autosaveEvidence'

test('中文回退字体：编辑、撤销、保存、重开和导出', { tag: '@feature:figure.mixed-script-fonts' }, async ({ app, page }) => {
  test.setTimeout(900_000)
  const shared = mkdtempSync(path.join(os.tmpdir(), 'tavotto-font-e2e-'))
  const figures = path.join(shared, 'figures')
  const mac = process.platform === 'darwin' && process.env.TAVOTTO_E2E_PORTABLE_FONTS !== '1'
  const latinName = mac ? 'Times New Roman' : 'DejaVu Sans'
  const chineseName = mac ? 'Songti SC' : 'Noto Sans SC'
  const chineseLabel = mac ? /宋体-简/ : /Noto Sans SC|思源黑体/
  const coveredName = mac ? 'Hiragino Sans GB' : chineseName
  const fontsDir = process.env.TAVOTTO_FONTS_DIR ?? path.resolve(import.meta.dirname, '..', '..', 'src', 'tavotto', 'resources', 'fonts')
  const notoFile = path.join(fontsDir, 'noto', 'NotoSansSC-Regular.otf')
  mkdirSync(figures, { recursive: true })
  writeFileSync(path.join(figures, 'mixed.py'), [
    'import matplotlib', 'matplotlib.use("Agg")', 'import matplotlib.pyplot as plt',
    `from matplotlib import font_manager; font_manager.fontManager.addfont(${JSON.stringify(notoFile)})`,
    `plt.rcParams["font.family"] = ${JSON.stringify(latinName)}`,
    'plt.plot([1, 2], [3, 4])', 'plt.title("中文 China 123")', 'plt.show()', '',
  ].join('\n'))
  writeFileSync(path.join(figures, 'tavotto_registry.json'), JSON.stringify({ version: 1, scripts: {} }))
  const env = {
    HOME: path.join(shared, 'home'), USERPROFILE: path.join(shared, 'home'),
    TAVOTTO_DATA_DIR: path.join(shared, 'data'), TAVOTTO_CONFIG_DIR: path.join(shared, 'config'),
  }
  mkdirSync(env.HOME, { recursive: true })
  await page.addInitScript(() => localStorage.setItem('tavotto.preparationPanel', 'off'))
  const a = await app({ figures, env })
  await page.goto(a.baseURL)
  await page.locator('[data-script-run="mixed.py"]').click()
  const card = page.locator('[data-card="runtime:mixed.py#mixed"]')
  await expect(card).toBeVisible({ timeout: 120_000 })
  await card.dblclick()
  await expect(page.locator('[data-element-svg] svg')).toBeVisible({ timeout: 120_000 })
  await openElementsTab(page)
  const title = page.locator('[data-tree="elements"] [data-el="axes_0.title"]')
  for (let i = 0; i < 8 && !(await title.isVisible()); i++) {
    const keys = await page.locator('[data-tree="elements"] [data-el][aria-expanded="false"]').evaluateAll(
      (nodes) => nodes.map((node) => node.getAttribute('data-el')).filter((key): key is string => key !== null),
    )
    for (const key of keys) {
      const group = page.locator(`[data-tree="elements"] [data-el=${JSON.stringify(key)}]`)
      await expect(group).toHaveCount(1)
      await group.click()
    }
  }
  await expect(title).toHaveCount(1)
  await title.click()
  const panel = page.locator('[data-inspector-panel]')
  await expect(panel).toHaveCount(1)
  const fallback = panel.locator('[data-prop="fontfamily.fallback"] [data-select-trigger]')
  await expect(fallback).toHaveCount(1)
  await expect(fallback).toContainText('自动')
  const shots = process.env.TAVOTTO_E2E_SHOTS
  if (shots) await panel.screenshot({ path: path.join(shots, 'inspector-auto.png') })
  const latin = panel.locator('[data-prop="fontfamily"] [data-select-trigger]')
  await expect(latin).toHaveCount(1)
  await latin.click()
  await page.locator(`[data-select-option=${JSON.stringify(coveredName)}]`).click()
  await fallback.click()
  await page.locator(`[data-select-option=${JSON.stringify(chineseName)}]`).click()
  const hint = panel.locator('[data-font-fallback-hint]')
  await expect(hint).toBeVisible({ timeout: 60_000 })
  await hint.hover()
  await expect(page.locator('[data-font-fallback-tooltip]')).toBeVisible()
  if (shots) await panel.screenshot({ path: path.join(shots, 'inspector-covered.png') })
  await latin.click()
  await expect(page.locator('[data-select-option="PingFang SC"]')).toHaveCount(0)
  await page.locator(`[data-select-option=${JSON.stringify(latinName)}]`).click()
  await expect(hint).toHaveCount(0)
  await expect(fallback).toContainText(chineseLabel)
  if (shots) await panel.screenshot({ path: path.join(shots, 'inspector-songti.png') })
  await page.locator('[data-history-action="undo"]').click()
  await expect(hint).toBeVisible()
  await page.locator('[data-history-action="redo"]').click()
  await expect(latin).toContainText(latinName)
  const object = page.locator('[data-canvas-stage] [data-object-id]')
  await expect(object).toHaveCount(1)
  const panelId = (await object.getAttribute('data-object-id'))!
  const saveTarget = { panelId, fileId: 'runtime:mixed.py#mixed', requiredOverrides: [{ gid: 'axes_0.title', prop: 'fontfamily' }] }
  const response = await page.waitForResponse((r) => autosavePanelEvidence(r, saveTarget) !== null, { timeout: 60_000 })
  const evidence = autosavePanelEvidence(response, saveTarget)!
  await page.keyboard.press('ControlOrMeta+s')
  await expect(page.locator('[data-layout-save]')).toBeVisible()
  await page.keyboard.press('Escape')
  const stored = await page.evaluate(() => Object.fromEntries(Object.entries(localStorage).filter(([k]) => k.startsWith('tavotto'))))
  await a.stop()
  const saved = JSON.parse(readFileSync(path.join(env.TAVOTTO_DATA_DIR, 'layouts', '_autosave', `${evidence.docId}.json`), 'utf8'))
  const savedPanel = saved.canvases[0].objects.find((o: { id: string }) => o.id === panelId)
  expect(savedPanel.overrides).toContainEqual(expect.objectContaining({ gid: 'axes_0.title', prop: 'fontfamily', value: { primary: latinName, fallback: chineseName } }))
  const a2 = await app({ figures, env })
  await page.addInitScript((entries) => { for (const [k, v] of Object.entries(entries)) localStorage.setItem(k, v as string) }, stored)
  await page.goto(a2.baseURL)
  const reopened = page.locator(`[data-object-id="${panelId}"]`)
  await expect(reopened).toBeVisible({ timeout: 30_000 })
  await reopened.click()
  await reopened.dblclick()
  await expect(page.locator('[data-element-svg] svg')).toBeVisible({ timeout: 120_000 })
  await openElementsTab(page)
  await expect(title).toHaveCount(1)
  await title.click()
  await expect(fallback).toContainText(chineseLabel)
  const result = await page.request.post(`${a2.baseURL}/api/export`, { data: {
    page_w_mm: 120, page_h_mm: 90, formats: ['pdf', 'svg'], stem: 'mixed_font_e2e',
    objects: [{ type: 'panel', id: 'runtime:mixed.py#mixed', x_mm: 5, y_mm: 5, w_mm: 100, h_mm: 75, overrides: savedPanel.overrides }],
  }, timeout: 300_000 })
  expect(result.ok()).toBeTruthy()
  const out = await result.json()
  expect(out.warnings ?? []).toEqual([])
  expect(out.files).toHaveLength(2)
  for (const f of out.files as { name: string }[]) expect(existsSync(path.join(out.export_dir, f.name))).toBeTruthy()
  console.log(`mixed-font-outputs ${out.export_dir}`)
  await page.locator('[data-rail="settings"]').click()
  await page.locator('[data-section="style"]').click()
  await page.locator('[data-profile-duplicate]').click()
  const row = page.locator('[data-style-row="title"]')
  await expect(row.locator('[data-style-cell="title.fallback"]')).toBeVisible()
  await row.scrollIntoViewIfNeeded()
  if (shots) await page.locator('[data-settings-content]').screenshot({ path: path.join(shots, 'settings-auto.png') })
  await row.locator('[data-style-cell="title.fallback"] [data-select-trigger]').click()
  await page.locator(`[data-select-option=${JSON.stringify(chineseName)}]`).click()
  await expect(row.locator('[data-style-cell="title.fallback"]')).toContainText(chineseLabel)
  await expect(page.locator(`[data-select-option=${JSON.stringify(chineseName)}]`)).toHaveCount(0)
  if (shots) await page.locator('[data-settings-content]').screenshot({ path: path.join(shots, 'settings-songti.png') })
})
