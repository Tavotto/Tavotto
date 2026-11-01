/**
 * 期刊标准库**界面入口**的总开关（ADR 0155；用户 2026-11-01 决定：PR 2 随 0.20.0 合入，但入口默认关闭，数据就绪后再开）。
 *
 * 关着时下面这些一个都不出现（界面与此 PR 之前逐像素相同）：设置 › 期刊规范里的「期刊」行与「期刊要求」、问题面板行尾的「出处」图标、
 * 导出对话框里的期刊名、设置搜索里的 `spec.journal`。已经带着期刊绑定的旧文档照旧按绑定预检，画布页的预设栏名也照旧跟着绑定走
 * ——它们读的是文档里的绑定，不是入口。
 *
 * 仓库里没有前端功能开关机制（登记表只有 `active` / `removed`，实验开关都在后端 config 里），所以这里只加这一个常量。
 * **打开的办法**：把 `JOURNAL_CATALOG_UI` 改成 `true`（数据就绪那个 PR）。没有任何生产界面能打开它；
 * 单测 / e2e 用开发期 localStorage 键 `tavotto.dev.journalCatalogUi = '1'`（只在测试里设，用户界面不写它）。
 */
export const JOURNAL_CATALOG_UI = false

/** 测试 / 开发用的覆盖键：值为 `'1'` 才算开 */
export const JOURNAL_UI_DEV_KEY = 'tavotto.dev.journalCatalogUi'

/** 期刊入口此刻开着吗（每次读，单测里改 localStorage 立即生效）。 */
export function journalCatalogUi(): boolean {
  if (JOURNAL_CATALOG_UI) return true
  try {
    return window.localStorage.getItem(JOURNAL_UI_DEV_KEY) === '1'
  } catch {
    return false
  }
}
