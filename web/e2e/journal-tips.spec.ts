import net from 'node:net'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import type { Page } from '@playwright/test'
import { expect, test, waitForDocumentReady } from './fixtures'

/**
 * 期刊线索入口（ADR 0213 一期，`docs/features/registry.json` 的 `journal.tips`）：真浏览器 + 真引擎。
 *
 *   1. 出处卡底栏与「打开原网页」同一行多一个文字链接「这条不对」；点开是「联系作者」同一个窗，标题下一行灰字写明哪本刊哪一条，
 *      多一个「原文链接」框，没有主题框和诊断包；点发送之前零连接，点发送才出现第一条连接（对端不是 TLS 服务，所以以网络类错误失败：
 *      失败有人话、草稿（含链接）还在）；
 *   2. 选择器搜不到那一行句尾多一个「没有这本刊」，点开带着搜索词；
 *   3. 入口只在三个开关都满足时出现：`TAVOTTO_NO_CONTACT=1`（fixtures 默认）的引擎上，卡与选择器都没有这个链接。
 *
 * 目录用夹具（`fx-*`，经本地缓存槽喂入，与 `journal-picker.spec.ts` 同一做法）。期刊联网开关保持「开」，但目录地址指向非 https
 * 的占位地址：引擎对非 https 的覆盖一律不联网，所以这条用例不会连任何远端。
 */
const FIXTURE = JSON.parse(
  readFileSync(path.resolve(import.meta.dirname, '..', '..', 'tests', 'golden', 'journal_vectors.json'), 'utf8'),
).catalog as unknown
const SNAP_DIR = path.resolve(import.meta.dirname, '..', '..', 'tests', 'fixtures', 'journal_snapshots')
const SHOTS = process.env.TAVOTTO_E2E_SHOTS

test.beforeEach(async ({ page }) => {
  await page.route('**/journal-catalog/snapshots/*.webp', (route) => {
    const file = new URL(route.request().url()).pathname.split('/').pop()!
    return route.fulfill({
      status: 200,
      contentType: 'image/webp',
      body: readFileSync(path.join(SNAP_DIR, 'snapshots', file)),
    })
  })
  await page.addInitScript((catalog) => {
    try {
      localStorage.setItem('tavotto.journalCatalog', JSON.stringify(catalog))
      localStorage.setItem('tavotto.dev.journalCatalogUi', '1')
      const saved = JSON.parse(localStorage.getItem('tavotto.ui') || '{}')
      localStorage.setItem('tavotto.ui', JSON.stringify({ ...saved, prefsVersion: 2, snapEnabled: false }))
    } catch {
      /* 存储不可用时照常跑 */
    }
  }, FIXTURE)
})

/** 浅 / 深各截一张（只在设了 TAVOTTO_E2E_SHOTS 时） */
async function shoot(page: Page, name: string) {
  if (!SHOTS) return
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme })
    await page.waitForTimeout(250)
    await page.screenshot({ path: path.join(SHOTS, `${name}-${scheme}.png`) })
  }
  await page.emulateMedia({ colorScheme: 'light' })
}

async function openSpec(page: Page) {
  await page.locator('[data-rail="settings"]').click()
  const shell = page.locator('[data-settings-shell]')
  await expect(shell).toBeVisible()
  await shell.locator('[data-section="spec"]').click()
}

test(
  '期刊线索：出处卡「这条不对」与选择器「没有这本刊」打开同一个联系作者窗，点发送前零连接',
  { tag: '@feature:journal.tips' },
  async ({ app, page }) => {
    test.setTimeout(240_000)
    let connections = 0
    const sink = net.createServer((sock) => {
      connections += 1
      sock.destroy()
    })
    await new Promise<void>((r) => sink.listen(0, '127.0.0.1', r))
    const port = (sink.address() as net.AddressInfo).port
    try {
      const a = await app({
        env: {
          TAVOTTO_NO_CONTACT: '0',
          TAVOTTO_DIAG_DEV_LOOPBACK: '1',
          TAVOTTO_CONTACT_ENDPOINT: `https://127.0.0.1:${port}`,
          TAVOTTO_NO_JOURNAL_SYNC: '0',
          TAVOTTO_JOURNAL_CATALOG_URL: 'http://127.0.0.1:9/', // 非 https：引擎不联网
        },
      })
      await page.setViewportSize({ width: 1440, height: 900 })
      await page.goto(a.baseURL)
      await page.locator('[data-card="Fig1_kinetics.pdf"]').dblclick({ timeout: 30_000 })
      await waitForDocumentReady(page)

      // ── 选一本夹具期刊，打开位图分辨率的出处卡 ────────────────────────────
      await openSpec(page)
      await page.locator('[data-journal-picker]').click()
      await page.locator('[data-journal-search]').fill('fixture beta')
      await page.locator('[data-journal-option="fx-beta"]').click()
      await page.locator('[data-journal-disclosure] button[aria-expanded]').click()
      await page.locator('[data-journal-row="min_raster_dpi"] [data-journal-source]').click()
      const card = page.locator('[data-journal-card]')
      await expect(card).toBeVisible()
      const report = card.locator('[data-journal-report]')
      await expect(report).toBeVisible()
      await expect(report).toHaveText('这条不对')
      // 与「打开原网页」同一行：同一个父节点、垂直中线相差不到一行
      const open = card.locator('[data-journal-open]')
      expect(await report.evaluate((el, o) => el.parentElement === o.parentElement, await open.elementHandle())).toBe(true)
      const [rb, ob] = [(await report.boundingBox())!, (await open.boundingBox())!]
      expect(Math.abs(rb.y + rb.height / 2 - (ob.y + ob.height / 2))).toBeLessThan(rb.height)
      // 卡仍在视口里
      const box = (await card.boundingBox())!
      const vp = page.viewportSize()!
      expect(box.x).toBeGreaterThanOrEqual(0)
      expect(box.x + box.width).toBeLessThanOrEqual(vp.width)
      await shoot(page, 'source-card')

      // ── 点开：同一个「联系作者」窗，灰字写明哪本刊哪一条 ──────────────────
      await report.click()
      const dialog = page.locator('[data-dialog="contact"]')
      await expect(dialog).toBeVisible()
      await expect(dialog.locator('[data-contact-journal-about]')).toContainText('Fixture Beta')
      await expect(dialog.locator('[data-contact-journal-about]')).toContainText('位图分辨率')
      await expect(dialog.locator('[data-contact-journal-url]')).toBeVisible()
      await expect(dialog.locator('[data-contact-subject]')).toHaveCount(0)
      await expect(dialog.locator('[data-contact-diag]')).toHaveCount(0)
      expect(connections).toBe(0)
      await shoot(page, 'dialog-wrong')

      // 链接没写协议会补 https；点发送才出现第一条连接；失败时草稿（含链接）还在
      await dialog.locator('[data-contact-journal-url]').fill('example.invalid/guide')
      await dialog.locator('[data-contact-send]').click()
      await expect(dialog.locator('[data-contact-status="failed"]')).toBeVisible({ timeout: 60_000 })
      expect(connections).toBeGreaterThan(0)
      await expect(dialog.locator('[data-contact-journal-url]')).toHaveValue('example.invalid/guide')
      await dialog.locator('[data-contact-dismiss]').click()
      await expect(dialog).toHaveCount(0)

      // ── 选择器搜不到：句尾「没有这本刊」 ───────────────────────────────
      await page.keyboard.press('Escape') // 关出处卡
      await page.locator('[data-journal-picker]').click()
      await page.locator('[data-journal-search]').fill('zzzz no such journal')
      const empty = page.locator('[data-journal-empty]')
      await expect(empty).toContainText('没有匹配的期刊')
      await shoot(page, 'picker-empty')
      await empty.getByRole('button', { name: '告诉作者加上' }).click()
      await expect(dialog).toBeVisible()
      await expect(dialog.locator('[data-contact-journal-about]')).toContainText('zzzz no such journal')
      await shoot(page, 'dialog-missing')
      await dialog.locator('[data-contact-dismiss]').click()

      // ── 普通「联系作者」：不带线索，提示语仍是原来的，没有原文链接框 ───────
      // 关掉选择器与期刊规范窗（各吃一次 Esc），直到没有遮罩
      const scrim = page.locator('[data-dialog-scrim][data-state="open"]')
      for (let i = 0; i < 4 && (await scrim.count()) > 0; i++) {
        await page.keyboard.press('Escape')
        await page.waitForTimeout(300)
      }
      await expect(scrim).toHaveCount(0)
      await page.locator('[data-contact-button="topbar"]').click()
      await expect(dialog).toBeVisible()
      await expect(dialog.locator('[data-contact-text]')).toHaveAttribute('placeholder', /遇到的问题、想要的功能/)
      await expect(dialog.locator('[data-contact-journal-url]')).toHaveCount(0)
      await expect(dialog.locator('[data-contact-journal-about]')).toHaveCount(0)
      await shoot(page, 'contact-plain')
    } finally {
      sink.close()
    }
  },
)

test('「联系作者」被硬关时（fixtures 默认）：出处卡与选择器都没有线索入口', async ({ app, page }) => {
  test.setTimeout(240_000)
  const a = await app()
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(a.baseURL)
  await page.locator('[data-card="Fig1_kinetics.pdf"]').dblclick({ timeout: 30_000 })
  await waitForDocumentReady(page)
  await openSpec(page)
  await page.locator('[data-journal-picker]').click()
  await page.locator('[data-journal-search]').fill('zzzz no such journal')
  await expect(page.locator('[data-journal-empty]')).toContainText('没有匹配的期刊')
  await expect(page.locator('[data-journal-empty] button')).toHaveCount(0)
  await page.locator('[data-journal-search]').fill('fixture beta')
  await page.locator('[data-journal-option="fx-beta"]').click()
  await page.locator('[data-journal-disclosure] button[aria-expanded]').click()
  await page.locator('[data-journal-row="min_raster_dpi"] [data-journal-source]').click()
  await expect(page.locator('[data-journal-card] [data-journal-open]')).toBeVisible()
  await expect(page.locator('[data-journal-report]')).toHaveCount(0)
})
