import { useSyncExternalStore } from 'react'

/**
 * 外观主题（2026-10-07 设计审计 P2 #14；宪法第二十八节）：跟随系统 / 浅色 / 深色。
 *
 * **值不在这里**：两张值表都在 `index.css`（浅色 = `@theme`，深色 = 文件末尾那两段）。这里只决定
 * `<html>` 上挂不挂 `data-theme`——
 *   - `system`：不挂，`@media (prefers-color-scheme: dark)` 那一段自己生效（系统切换外观时当场跟着变，
 *     CSS 这一侧不用监听 matchMedia）；
 *   - `light` / `dark`：挂 `data-theme="light"` / `"dark"`，前者挡住媒体查询，后者直接套深色表。
 *
 * 偏好本身住在 `uiStore`（`theme`，与侧栏 / 网格那些本机界面偏好同一份 `tavotto.ui`），写入口只有
 * `uiStore.setTheme`。开机的首帧落点是 `index.html` `<head>` 里的同步脚本（入口模块延迟执行，等它就晚了一帧）；
 * 那段脚本重写了一遍键 / 合法值，`themeBoot.test` 拿原文对着 uiStore + `applyTheme` 逐例对拍。入口再调一次
 * `applyTheme` 对账（`<html>` 上的 `data-theme` 仍只由这里的语义决定）。
 *
 * **把 token 读进 JS 缓存起来的地方**（canvas 画不了 `var(--…)`，只能 `getComputedStyle` 量出值再画——今天是
 * `canvas/Rulers` 的 `readInk`）：缓存必须以 `useEffectiveTheme()` 为键（Codex #834 P2）。它是「现在生效的是哪套值表」
 * 的唯一出处：显式偏好看 `data-theme`，跟随系统看 `prefers-color-scheme`；订阅 `<html>` 的 `data-theme` 变化
 * （不经 uiStore——开机脚本、`applyTheme` 谁挂的都算）与系统外观的 change 事件。按挂载量一次、永不失效的缓存
 * 会让切换主题后画布上留着旧色。
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

/** 生效的那套值表：只有浅 / 深两种（`system` 已按系统外观落成其一） */
export type EffectiveTheme = 'light' | 'dark'

/** 与 `index.css` 暗色那一段的媒体查询同一句 */
export const DARK_SCHEME_QUERY = '(prefers-color-scheme: dark)'

const darkSchemeQuery = (): MediaQueryList | null =>
  typeof globalThis.matchMedia === 'function' ? globalThis.matchMedia(DARK_SCHEME_QUERY) : null

/** 现在生效的主题：`data-theme` 优先（显式偏好），没挂就看系统外观；没有 matchMedia 的环境按浅色 */
export function effectiveTheme(root: HTMLElement | undefined = globalThis.document?.documentElement): EffectiveTheme {
  const attr = root?.getAttribute('data-theme')
  if (attr === 'light' || attr === 'dark') return attr
  return darkSchemeQuery()?.matches ? 'dark' : 'light'
}

/** 生效主题可能变了就回调：`<html>` 的 `data-theme` 变化 + 系统外观 change */
export function subscribeEffectiveTheme(onChange: () => void): () => void {
  const root = globalThis.document?.documentElement
  const observer = root && typeof MutationObserver !== 'undefined' ? new MutationObserver(onChange) : null
  if (root) observer?.observe(root, { attributes: true, attributeFilter: ['data-theme'] })
  const mql = darkSchemeQuery()
  mql?.addEventListener?.('change', onChange)
  return () => {
    observer?.disconnect()
    mql?.removeEventListener?.('change', onChange)
  }
}

const effectiveThemeSnapshot = () => effectiveTheme()
const serverSnapshot = (): EffectiveTheme => 'light'

/**
 * 组件里读生效主题：换主题（设置里切、或跟随系统时系统换外观）就重渲染。把 CSS token 量进 JS / canvas 并缓存的
 * 地方拿它当缓存键（`useMemo(readInk, [theme])`），并让画的那一步依赖缓存——换主题当场重画。
 */
export function useEffectiveTheme(): EffectiveTheme {
  return useSyncExternalStore(subscribeEffectiveTheme, effectiveThemeSnapshot, serverSnapshot)
}
