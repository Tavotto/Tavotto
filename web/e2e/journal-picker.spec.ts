import { readFileSync } from 'node:fs'
import path from 'node:path'
import type { Page } from '@playwright/test'
import { addSecondFigure, expect, test, waitForDocumentReady } from './fixtures'

/**
 * 内置期刊标准库的选择器与出处卡（ADR 0155 PR 2，`docs/features/registry.json` 的 `settings.journal-picker`）。
 *
 * 仓库里的种子目录是空的，所以用 golden 向量里的**夹具目录**（`fx-*`、`example.invalid`，不是任何真实期刊）：
 * 经 `tavotto.journalCatalog` 这个本地缓存槽喂进去（整份过同一道校验，见 `store/journalCatalogStore.ts`）。
 *
 * 判据只认用户看得见的结果：页宽读数真的换了、一次撤销全回去、胶囊 toast 出现、出处卡里命中的数被 `<mark>` 标出、
 * 链接指向带页码 / 锚点的原网页。
 */
const FIXTURE = JSON.parse(
  readFileSync(path.resolve(import.meta.dirname, '..', '..', 'tests', 'golden', 'journal_vectors.json'), 'utf8'),
).catalog as unknown

// fx-beta：出版社给单栏 85 mm，期刊自己把双栏改成 180 mm（出版社默认 175）
const COLUMNS = [85, 180]
const nearest = (w: number) => COLUMNS.reduce((a, b) => (Math.abs(b - w) < Math.abs(a - w) ? b : a))

/** 夹具快照图（假的「原网页」，内容寻址）：种子目录没有真实快照，这里按资产根的地址喂给页面 */
const SNAP_DIR = path.resolve(import.meta.dirname, '..', '..', 'tests', 'fixtures', 'journal_snapshots')

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

/** 属性栏「画布」页头里的页宽（mm）。读屏标签里是「W × H mm」；父级 `[data-canvas-identity]` 是稳定锚点。 */
async function pageWidthMm(page: Page): Promise<number> {
  const label = await page
    .locator('[data-canvas-identity] [role="img"]')
    .getAttribute('aria-label', { timeout: 15_000 })
  const m = /([\d.]+)\s*[×x]\s*[\d.]+/.exec(label ?? '')
  if (!m) throw new Error(`读不出页宽: ${label}`)
  return parseFloat(m[1])
}

/** Esc 关设置；选项刚收起时焦点还在过渡，按一次不一定落在对话框上，所以按到它真的没了为止 */
async function closeSettings(page: Page) {
  const shell = page.locator('[data-settings-shell]')
  await expect(async () => {
    if (await shell.count()) await page.keyboard.press('Escape')
    await expect(shell).toHaveCount(0, { timeout: 1_500 })
  }).toPass({ timeout: 15_000 })
}

test(
  '选期刊：页宽跟到该期刊的一栏、一次撤销全回去；出处卡给出原网页快照与链接；「不按期刊」退回之前的规范与页宽',
  { tag: '@feature:settings.journal-picker' },
  async ({ app, page }) => {
    const a = await app()
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto(a.baseURL)
    await page.locator('[data-card="Fig1_kinetics.pdf"]').dblclick({ timeout: 30_000 })
    await waitForDocumentReady(page)
    await addSecondFigure(page)

    await page.locator('[data-inspector-tab="canvas"]').click()
    const before = await pageWidthMm(page)
    const target = nearest(before)
    expect(target, '夹具栏宽里要有一档与默认页宽不同，否则看不出页宽换没换').not.toBe(before)

    // ── 设置 → 期刊规范 → 选期刊 ────────────────────────────────────────
    await page.locator('[data-rail="settings"]').click()
    const shell = page.locator('[data-settings-shell]')
    await expect(shell).toBeVisible()
    await shell.locator('[data-section="spec"]').click()
    await page.locator('[data-journal-picker]').click()

    // 按出版社分组，可搜（名称 / 缩写 / ISSN）
    await expect(page.locator('[data-journal-group]')).not.toHaveCount(0)
    await page.locator('[data-journal-search]').fill('fixture beta')
    await expect(page.locator('[data-journal-option]')).toHaveCount(1)
    await page.locator('[data-journal-option="fx-beta"]').click()

    // 确认是胶囊 toast，不是推挤版面的横条
    await expect(page.locator('[data-toast-layout="pill"][data-status-tone="done"]')).toContainText(String(target), { timeout: 10_000 })

    // 页宽真的换了；关掉设置回到画布再量
    await closeSettings(page)
    await expect.poll(() => pageWidthMm(page), { message: '页宽没有跟到所选期刊的栏宽' }).toBe(target)

    // 右侧画布页的页面预设：当前宽度对上期刊的哪一栏就显示哪一栏，下拉里是该期刊的栏宽（不是课题组的）
    const current = page.locator('[data-page-preset]').first()
    await expect(current).toHaveAttribute('data-page-preset', 'double')
    await current.click()
    await expect(page.getByRole('option', { name: /单栏/ })).toContainText('85 mm')
    await expect(page.getByRole('option', { name: /双栏/ })).toContainText(`${target} mm`)
    await page.keyboard.press('Escape')

    // 一次撤销：页宽回来
    await page.keyboard.press('ControlOrMeta+z')
    await expect.poll(() => pageWidthMm(page), { message: '撤销一步页宽没有回到原值' }).toBe(before)
    // 再做一遍（重做），之后看出处
    await page.keyboard.press('ControlOrMeta+Shift+z')
    await expect.poll(() => pageWidthMm(page)).toBe(target)

    // ── 不按期刊：退回选期刊之前的规范与页宽，撤销一步回到期刊 ─────────────────
    await page.locator('[data-rail="settings"]').click()
    await expect(shell).toBeVisible()
    await shell.locator('[data-section="spec"]').click()
    await page.locator('[data-journal-picker]').click()
    const none = page.locator('[data-journal-list] > :first-child')
    await expect(none).toHaveAttribute('data-journal-none', 'true')
    await none.click()
    await expect(page.locator('[data-journal-picker]')).toContainText('选择期刊')
    await expect(page.locator('[data-journal-requirements]')).toHaveCount(0)
    await closeSettings(page)
    await expect.poll(() => pageWidthMm(page), { message: '「不按期刊」没有把页宽退回去' }).toBe(before)
    await page.keyboard.press('ControlOrMeta+z')
    await expect.poll(() => pageWidthMm(page), { message: '撤销一步没有回到期刊' }).toBe(target)

    // ── 出处卡 ────────────────────────────────────────────────────────
    await page.locator('[data-rail="settings"]').click()
    await expect(shell).toBeVisible()
    await shell.locator('[data-section="spec"]').click()
    await page.locator('[data-disclosure="row"] button', { hasText: '期刊要求' }).click()
    const row = page.locator('[data-journal-row="min_raster_dpi"]')
    await expect(row).toContainText('300 dpi')
    await row.locator('[data-journal-source]').click()

    const card = page.locator('[data-journal-card]')
    await expect(card).toBeVisible()
    // 结论是底栏第一行（不再有单独的顶部标题句）
    await expect(card.locator('[data-journal-sentence]')).toHaveText('位图分辨率 ≥ 300 dpi')
    const box = (await card.boundingBox())!
    const vp = page.viewportSize()!
    expect(box.x).toBeGreaterThanOrEqual(0)
    expect(box.x + box.width).toBeLessThanOrEqual(vp.width)
    expect(box.y + box.height).toBeLessThanOrEqual(vp.height)
    // 有快照：卡里是（高亮后裁好的）原网页快照图，真的加载出来了；不再画摘录框与换算行
    const shot = card.locator('[data-journal-snapshot] img')
    await expect(shot).toBeVisible()
    await expect.poll(() => shot.evaluate((el: HTMLImageElement) => el.complete && el.naturalWidth)).toBe(1080)
    await expect(card.locator('[data-journal-quote]')).toHaveCount(0)
    await expect(card).not.toContainText('->')
    // 打开原网页 / 来源域名与采集日期
    await expect(card.locator('[data-journal-meta]')).toHaveText('Fixture Beta · www.nature.com · 2026-10-31')
    const open = card.locator('[data-journal-open]')
    expect(await open.getAttribute('href')).toBe(
      'https://www.nature.com/nenergy/submission-guidelines/aip-and-formatting#:~:text=minimum%20resolution%20of%20300%20dpi',
    )
    expect(await open.getAttribute('target')).toBe('_blank')
  },
)
