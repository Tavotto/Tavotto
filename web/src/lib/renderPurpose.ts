/**
 * 渲染请求的**意图**（闭集；与后端 `engine/pool.py` 的 `PURPOSES` / `BACKGROUND_DEFERRED` 同源）。
 *
 * - `foreground`：用户在等的渲染（缺省；老请求不带这个字段就是它）。
 * - `neighbour`：同步器「顺手补渲染」视口里别的图（跨图对齐，ADR 0128）。后端不为它刷新会话池的 LRU 时间；
 *   需要冷启动而池已满（起它就得挤掉前台会话）时回 409 + `RENDER_DEFERRED_CODE`。
 *
 * 独立成无依赖的小模块：`lib/api` 与 `store/renderStore` 都要它，而两者之间已有依赖，别再绕环。
 */
export type RenderPurpose = 'foreground' | 'neighbour'

/** 后端说「这次后台补渲染没做」。**不是失败**：保留旧画面，不报错、不弹提示。 */
export const RENDER_DEFERRED_CODE = 'background_render_deferred'
