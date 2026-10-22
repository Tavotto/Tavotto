import { execFileSync } from 'node:child_process'
import { cpSync, mkdtempSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, test } from './fixtures'

const OUT = process.env.SNAP_SHOTS_DIR
/**
 * hold 让位（ADR 0128 §一修订 2026-10-21）：教程项目里拖 Fig2 子图 1 去对 Fig1 底边，途经 Fig2 整图中线时
 * 被 hold 住；旧判据下离 Fig1 底边只差 0.6 px 也不让位，松手后差 0.82 mm。断言松手后两底边差 ≤ 0.05 mm。
 * 夹具 = 教程资源目录的拷贝（不碰用户 data_dir）。
 */
test.setTimeout(600_000)
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    const key = 'tavotto.ui'
    const saved = JSON.parse(localStorage.getItem(key) || '{}')
    localStorage.setItem(key, JSON.stringify({ ...saved, prefsVersion: 2, snapEnabled: true, snapToObjects: true }))
  })
})
test('教程：拖 Fig2 子图底边靠近 Fig1 底边，途经整图中线不被 hold 拽住，松手后底边对齐（≤ 0.05 mm）', async ({ app, page }) => {
  const dir = path.join(mkdtempSync(path.join(os.tmpdir(), 'tavotto-tut-')), 'Tutorial')
  cpSync(path.resolve(import.meta.dirname, '../../src/tavotto/resources/tutorial_project'), dir, { recursive: true })
  const py = process.env.TAVOTTO_WORKER_PYTHON || 'python3'
  for (const s of ['fig1_kinetics', 'fig2_correlation']) execFileSync(py, ['-c', `import ${s}; ${s}.main()`], { cwd: dir, timeout: 120_000 })
  const a = await app({ figures: dir })
  await page.setViewportSize({ width: 1600, height: 1000 })
  await page.goto(a.baseURL)
  const mans: Record<string, any> = {}
  page.on('response', async (r) => {
    if (!r.url().includes('/api/engine/render')) return
    try { const j = await r.json(); if (j.manifest) mans[j.manifest.stem] = j.manifest } catch {}
  })
  const objs = page.locator('[data-object-id]')
  const addCard = async (card: string, count: number) => {
    const rail = page.locator('[data-rail="assets"]')
    if ((await rail.getAttribute('aria-expanded')) !== 'true') await rail.click()
    const cardEl = page.locator(`[data-card="${card}"]`)
    await expect(cardEl).toBeVisible({ timeout: 30_000 })
    await cardEl.focus()
    await page.keyboard.press('Shift+Enter')
    await expect(objs).toHaveCount(count, { timeout: 30_000 })
    await page.waitForTimeout(1200)
  }
  await addCard('Fig1_kinetics.pdf', 1)
  await addCard('Fig2_correlation.pdf', 2)
  const drag = async (i: number, dx: number, dy: number) => {
    const b = (await objs.nth(i).boundingBox())!
    const cx = b.x + b.width / 2, cy = b.y + b.height / 2
    await page.mouse.move(cx, cy); await page.mouse.down()
    for (let k = 1; k <= 8; k++) await page.mouse.move(cx + (dx * k) / 8, cy + (dy * k) / 8)
    await page.mouse.up(); await page.waitForTimeout(300)
    await page.keyboard.press('Escape')
  }
  const pxmm = (await objs.nth(0).boundingBox())!.width / 64
  await drag(1, 40 * pxmm, -3 * pxmm)
  await drag(0, -40 * pxmm, 0)
  // zoom via dropdown
  const enter = async () => {
    const c = (await objs.nth(1).boundingBox())!
    await page.mouse.click(c.x + c.width / 2, c.y + c.height / 2)
    await page.keyboard.press('Enter')
    await expect(page.locator('[data-element-svg] > svg')).toBeVisible({ timeout: 240_000 })
    await page.waitForTimeout(3000)
  }
  await enter()
  const g = (stem: string) => mans[stem].elements.find((e: any) => e.gid === 'axes_0')
  const aa = (await objs.nth(0).boundingBox())!
  const bb = (await objs.nth(1).boundingBox())!
  const mA = g('Fig1_kinetics'), mB = g('Fig2_correlation')
  const botA = aa.y + (mA.bbox[1] + mA.bbox[3]) * aa.height
  const botB = bb.y + (mB.bbox[1] + mB.bbox[3]) * bb.height
  const dy = botA - botB - 0.6
  const sx = bb.x + (mB.bbox[0] + 0.55 * mB.bbox[2]) * bb.width, sy = bb.y + (mB.bbox[1] + 0.06 * mB.bbox[3]) * bb.height
  await page.mouse.click(sx, sy); await page.waitForTimeout(400)
  await page.mouse.move(sx, sy); await page.mouse.down()
  for (let i = 1; i <= 12; i++) await page.mouse.move(sx, sy + (dy * i) / 12)
  await page.waitForTimeout(300)
  if (OUT) await page.screenshot({ path: `${OUT}/hold-yield-during.png` })
  const rendered = page.waitForResponse((r) => r.url().includes('/api/engine/render'), { timeout: 120_000 })
  await page.mouse.up(); await rendered
  await page.waitForTimeout(1500)
  if (OUT) await page.screenshot({ path: `${OUT}/hold-yield-after.png` })
  const mB2 = g('Fig2_correlation')
  const pxPerMm = aa.width / mans['Fig1_kinetics'].size_mm[0]
  const errMm = (bb.y + (mB2.bbox[1] + mB2.bbox[3]) * bb.height - botA) / pxPerMm
  expect(Math.abs(errMm), `松手后两图 axes 底边差（mm）`).toBeLessThanOrEqual(0.05)
})
