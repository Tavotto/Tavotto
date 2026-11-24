/**
 * 推荐配色**界面入口**的总开关（ADR 0210；`docs/1.0-release-readiness.md` §1.8：随 1.0 进代码，入口默认关）。
 *
 * 关着时样式面板里没有「配色」一节，界面与此 PR 之前逐像素相同；文档里没有 `colorKeys` 的画布，
 * 任何写入路径都与之前一样（样式的配色循环照旧上色）。
 *
 * **打开的办法**：把 `RECOMMENDED_PALETTE_UI` 改成 `true`，单独一个 PR、维护者点头。没有任何生产界面能打开它；
 * 单测 / e2e 用开发期 localStorage 键 `tavotto.dev.paletteUi = '1'`（只在测试里设，用户界面不写它）。
 */
export const RECOMMENDED_PALETTE_UI = false

/** 测试 / 开发用的覆盖键：值为 `'1'` 才算开 */
export const PALETTE_UI_DEV_KEY = 'tavotto.dev.paletteUi'

/** 推荐配色入口此刻开着吗（每次读，单测里改 localStorage 立即生效）。 */
export function paletteUi(): boolean {
  if (RECOMMENDED_PALETTE_UI) return true
  try {
    return window.localStorage.getItem(PALETTE_UI_DEV_KEY) === '1'
  } catch {
    return false
  }
}
