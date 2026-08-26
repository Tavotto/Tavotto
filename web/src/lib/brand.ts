/**
 * 品牌与格式标识的唯一出处。产品正式名称 Tavotto，拼写与大小写固定，
 * 界面任何地方不得再手写产品名。
 *
 * **这一档没有 LEGACY_*，是有意的**（与 `engine/brand.py` 同源的决定）：
 * 2026-08-20 从 Magplot 改名时选的是干净断裂，`magplot-package` / `.magplot` /
 * `magplot/objects@1` 一律不再认，Magic Matplot 时代那一档也一并去掉了——
 * 只认两代前的名字、却不认上一代的，那种半吊子状态比干净断裂更难解释。
 * 文档 schema 的迁移（`migrateToProject`，接受 schema 2/3）是另一回事，照旧。
 */
export const PRODUCT_NAME = 'Tavotto'

/** 仓库与发行地址（与 `engine/brand.py` 的 REPO_URL 同源） */
export const REPO_URL = 'https://github.com/Tavotto/Tavotto'
export const RELEASES_LATEST_URL = `${REPO_URL}/releases/latest`
/**
 * 「在 Codex 中第一次使用 Tavotto」的使用指南（README 的章节锚点）。
 *
 * **「Tavotto for Codex」与「本机装了 Codex CLI」是两件事**：前者是把
 * Tavotto 装进 Codex（插件 + 技能 + MCP 画布），后者是 Tavotto 借用本机的
 * codex 命令行改脚本。设置页的两个小节分别对应这两个方向，别把它们的状态
 * 混在一起说。
 */
export const CODEX_GUIDE_URL = `${REPO_URL}#using-tavotto-with-codex-for-the-first-time`

export const PACKAGE_KIND = 'tavotto-package'
export const PROOF_KIND = 'tavotto-proof'
export const CLIPBOARD_FORMAT = 'tavotto/objects@1'
export const PACKAGE_EXT = '.tavotto'
