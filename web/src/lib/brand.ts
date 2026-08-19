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

export const PACKAGE_KIND = 'tavotto-package'
export const PROOF_KIND = 'tavotto-proof'
export const CLIPBOARD_FORMAT = 'tavotto/objects@1'
export const PACKAGE_EXT = '.tavotto'
