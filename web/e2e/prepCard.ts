import { expect, type Locator, type Page } from '@playwright/test'

/**
 * 准备引导卡（T13b）的 e2e 钩子：卡片 / 角标 / 主按钮都认稳定的 `data-*`，不认文案。
 */
export const card = (page: Page) => page.locator('[data-prep-card]')
export const pill = (page: Page) => page.locator('[data-prep-pill]')
export const primary = (page: Page) => card(page).locator('[data-prep-primary]')

/** 展开「详情」（默认不展开；已展开就不动） */
export async function openDetails(page: Page): Promise<Locator> {
  const toggle = card(page).locator('[data-prep-details-toggle]')
  if ((await toggle.getAttribute('aria-expanded')) !== 'true') await toggle.click()
  return card(page).locator('[data-prep-details]')
}

/**
 * 工作面板此刻的上沿与高度：卡片出现前后比一比，就知道它有没有把页面往下推（左右会随左栏抽屉开合变，那与本卡无关）
 */
export const workPanelRect = (page: Page) =>
  page.evaluate(() => {
    const w = document.querySelector('[data-work-panel]')!.getBoundingClientRect()
    return { top: Math.round(w.top), height: Math.round(w.height) }
  })

/**
 * 页面不下移（用户硬性要求：不要顶部横幅）：卡片浮在画布工作面板里、不占布局——顶栏仍贴着窗口顶，旧的检查条不在了，
 * 整页没有纵向滚动；卡片自己整张落在工作面板里面。「卡片出现前后工作面板不动」用 `workPanelRect` 在用例里比
 * （接入状态横幅等既有的横条与本卡无关，不在这里数）
 */
export async function expectPageNotShifted(page: Page) {
  const m = await page.evaluate(() => {
    const top = document.querySelector('header')!.getBoundingClientRect()
    const work = document.querySelector('[data-work-panel]')!.getBoundingClientRect()
    const c = document.querySelector('[data-prep-card], [data-prep-pill]')?.getBoundingClientRect() ?? null
    const se = document.scrollingElement!
    return {
      headerTop: Math.round(top.top),
      scanBar: document.querySelectorAll('[data-project-scan]').length,
      scroll: se.scrollTop,
      overflow: se.scrollHeight - window.innerHeight,
      inside: c
        ? c.left >= work.left - 1 && c.right <= work.right + 1 && c.top >= work.top - 1 && c.bottom <= work.bottom + 1
        : null,
    }
  })
  expect(m.headerTop, '顶栏应当贴着窗口顶').toBe(0)
  expect(m.scanBar, '顶部检查条不应再出现').toBe(0)
  expect(m.scroll, '整页不应滚动').toBe(0)
  expect(m.overflow, '整页不应比窗口高').toBeLessThanOrEqual(0)
  if (m.inside !== null) expect(m.inside, '卡片应整张落在工作面板里').toBe(true)
}
