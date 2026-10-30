import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, openElementsTab, test } from './fixtures'
import { autosavePanelEvidence } from './autosaveEvidence'

test('中文回退字体：编辑、撤销、保存、重开和导出', async ({ app, page }) => {
  test.setTimeout(900_000)
  const shared = mkdtempSync(path.join(os.tmpdir(), 'tavotto-font-e2e-'))
  const figures = path.join(shared, 'figures')
  mkdirSync(figures, { recursive: true })
  writeFileSync(path.join(figures, 'mixed.py'), [
    'import matplotlib', 'matplotlib.use("Agg")', 'import matplotlib.pyplot as plt',
    'plt.rcParams["font.family"] = "Times New Roman"',
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
  await page.getByRole('button', { name: '运行 mixed.py 并发现图' }).click()
  const card = page.locator('[data-card="runtime:mixed.py#mixed"]')
  await expect(card).toBeVisible({ timeout: 120_000 })
  await card.dblclick()
  await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 120_000 })
  await openElementsTab(page)
  for (let i = 0; i < 8; i++) {
    const target = page.getByRole('treeitem', { name: /^标题/ }).first()
    if (await target.isVisible()) break
    const group = page.locator('[role="treeitem"][aria-expanded="false"]').first()
    if (await group.count()) await group.click()
    await page.waitForTimeout(150)
  }
  await page.getByRole('treeitem', { name: /^标题/ }).first().click()
  const panel = page.getByLabel('右侧面板', { exact: true })
  const fallback = panel.getByRole('combobox', { name: '中文字体' })
  await expect(fallback).toContainText('自动')
  const shots = process.env.TAVOTTO_E2E_SHOTS
  if (shots) await panel.screenshot({ path: path.join(shots, 'inspector-auto.png') })
  const latin = panel.getByRole('combobox', { name: '英文字体' })
  await latin.click()
  await page.getByRole('option', { name: /冬青黑体简体中文/ }).first().click()
  await fallback.click()
  await page.getByRole('option', { name: /宋体-简/ }).first().click()
  const hint = panel.getByRole('button', { name: '请改选不含中文字符的英文字体。' })
  await expect(hint).toBeVisible({ timeout: 60_000 })
  await hint.hover()
  await expect(page.getByRole('tooltip')).toBeVisible()
  if (shots) await panel.screenshot({ path: path.join(shots, 'inspector-covered.png') })
  await latin.click()
  await expect(page.getByRole('option', { name: /苹方-简/ })).toHaveCount(0)
  await page.getByRole('option', { name: /Times New Roman/ }).first().click()
  await expect(hint).toHaveCount(0)
  await expect(fallback).toContainText('宋体-简')
  if (shots) await panel.screenshot({ path: path.join(shots, 'inspector-songti.png') })
  await page.getByRole('button', { name: '撤销' }).click()
  await expect(hint).toBeVisible()
  await page.getByRole('button', { name: '重做' }).click()
  await expect(latin).toContainText('Times New Roman')
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
  expect(savedPanel.overrides).toContainEqual(expect.objectContaining({ gid: 'axes_0.title', prop: 'fontfamily', value: { primary: 'Times New Roman', fallback: 'Songti SC' } }))
  const a2 = await app({ figures, env })
  await page.addInitScript((entries) => { for (const [k, v] of Object.entries(entries)) localStorage.setItem(k, v as string) }, stored)
  await page.goto(a2.baseURL)
  const reopened = page.locator(`[data-object-id="${panelId}"]`)
  await expect(reopened).toBeVisible({ timeout: 30_000 })
  await reopened.click()
  await page.getByRole('button', { name: '改图里的内容' }).first().click()
  await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 120_000 })
  await openElementsTab(page)
  await page.getByRole('treeitem', { name: /^标题/ }).first().click()
  await expect(page.getByLabel('右侧面板', { exact: true }).getByRole('combobox', { name: '中文字体' })).toContainText('宋体-简')
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
  await page.getByRole('button', { name: '复制一份再修改' }).click()
  const row = page.locator('[data-style-row="title"]')
  await expect(row.locator('[data-style-cell="title.fallback"]')).toBeVisible()
  await row.scrollIntoViewIfNeeded()
  if (shots) await page.locator('[data-settings-content]').screenshot({ path: path.join(shots, 'settings-auto.png') })
  await row.locator('[data-style-cell="title.fallback"] [role="combobox"]').click()
  await page.getByRole('option', { name: /宋体-简|Songti SC/ }).first().click()
  await expect(row.locator('[data-style-cell="title.fallback"]')).toContainText(/宋体-简|Songti SC/)
  await expect(page.getByRole('listbox')).toHaveCount(0)
  if (shots) await page.locator('[data-settings-content]').screenshot({ path: path.join(shots, 'settings-songti.png') })
})
