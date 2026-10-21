import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

/**
 * 主页最近项目卡的布局回归（拆自 home.spec.ts，单独进 webkit 腿）：
 * 旧 WebKit（macOS 14.3.1 / Safari 17）里 `<button class="flex flex-col">` 不拉伸子项，卡片曾溢出；
 * 这条只在 WebKit 上才可能红，所以必须在 webkit 项目里真跑。
 */

/** 本机 onboarding 那一格：走完了教程 → 老手版（格式见 store/onboardingStore） */
const COMPLETED = JSON.stringify({
  schemaVersion: 1,
  flowVersion: 2,
  status: 'completed',
  currentStep: null,
  completedSteps: [],
  skippedSteps: [],
  hintSeen: {},
  startedAt: 1,
  completedAt: 2,
  tutorialProjectId: null,
  tutorialDocumentId: null,
  pausedBy: null,
})

async function asReturningUser(page: Page) {
  await page.addInitScript((blob) => window.localStorage.setItem('tavotto.onboarding', blob), COMPLETED)
}

/**
 * 最近项目卡遇上超长路径（坚果云 / iCloud：中文 + 邮箱 + 无空格长串）：每张卡仍是等宽的一列，
 * 缩略图格占满卡的内宽（不被文字撑塌成竖条），路径在卡内被 TailPath 截断，不横穿相邻卡。
 * 合伙人 beta 0.19.0b1 的截图是这个形状；结构上靠整条链 min-w-0 / w-full / overflow-hidden 保证。
 */
test('最近项目卡 超长路径：等宽列、缩略图占满内宽、路径不溢出卡边界', async ({ app, page }) => {
  await asReturningUser(page)
  const long =
    '/Users/qingqin/Library/CloudStorage/坚果云-24110190013@m.fudan.edu.cn/' + '实验数据汇总绘图脚本最终版'.repeat(8) + '_figures_revision_2'
  const recent = [long, '/Users/me/b', '/Users/me/c'].map((p, i) => ({
    path: p,
    name: i ? `p${i}` : '图表项目',
    last_opened: Date.now() - 3600_000,
    exists: true,
    current: false,
  }))
  await page.route('**/api/projects/recent', (r) => r.fulfill({ json: { recent, pinned: [] } }))
  for (const width of [900, 1100]) {
    await page.setViewportSize({ width, height: 800 })
    const a = await app({ noProject: true })
    await page.goto(a.baseURL)
    await expect(page.locator('[data-home-recent]')).toHaveCount(3)
    const m = await page.evaluate(() =>
      [...document.querySelectorAll<HTMLElement>('[data-home-recent]')].map((li) => {
        const card = li.querySelector<HTMLElement>('[data-card]')!.getBoundingClientRect()
        const thumb = li.querySelector<HTMLElement>('button span[aria-hidden]')!.getBoundingClientRect()
        const path = li.querySelector<HTMLElement>('[dir="rtl"]')
        const p = path?.getBoundingClientRect()
        return { card: [card.left, card.right], thumbW: thumb.width, pathRight: p?.right ?? null, w: card.width }
      }),
    )
    for (const c of m) {
      expect(c.w).toBeCloseTo(m[0].w, 0)
      expect(c.thumbW).toBeGreaterThan(c.w - 12)
      if (c.pathRight !== null) expect(c.pathRight).toBeLessThanOrEqual(c.card[1] + 0.5)
    }
  }
})
