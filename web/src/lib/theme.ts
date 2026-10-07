/**
 * 外观主题（2026-10-07 设计审计 P2 #14；宪法第二十八节）：跟随系统 / 浅色 / 深色。
 *
 * **值不在这里**：两张值表都在 `index.css`（浅色 = `@theme`，深色 = 文件末尾那两段）。这里只决定
 * `<html>` 上挂不挂 `data-theme`——
 *   - `system`：不挂，`@media (prefers-color-scheme: dark)` 那一段自己生效（系统切换外观时当场跟着变，
 *     不用监听 matchMedia）；
 *   - `light` / `dark`：挂 `data-theme="light"` / `"dark"`，前者挡住媒体查询，后者直接套深色表。
 *
 * 偏好本身住在 `uiStore`（`theme`，与侧栏 / 网格那些本机界面偏好同一份 `tavotto.ui`），写入口只有
 * `uiStore.setTheme`。开机的首帧落点是 `index.html` `<head>` 里的同步脚本（入口模块延迟执行，等它就晚了一帧）；
 * 那段脚本重写了一遍键 / 合法值，`themeBoot.test` 拿原文对着 uiStore + `applyTheme` 逐例对拍。入口再调一次
 * `applyTheme` 对账（`<html>` 上的 `data-theme` 仍只由这里的语义决定）。
 */

export const THEME_PREFS = ['system', 'light', 'dark'] as const
export type ThemePref = (typeof THEME_PREFS)[number]

export const isThemePref = (v: unknown): v is ThemePref =>
  typeof v === 'string' && (THEME_PREFS as readonly string[]).includes(v)

/** 把偏好落到 `<html>` 上（唯一写 `data-theme` 的地方） */
export function applyTheme(pref: ThemePref, root: HTMLElement | undefined = globalThis.document?.documentElement) {
  if (!root) return
  if (pref === 'system') root.removeAttribute('data-theme')
  else root.setAttribute('data-theme', pref)
}
