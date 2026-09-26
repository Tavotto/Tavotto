/**
 * 「回到项目列表」在浏览器历史里占一格，让后退键从 Project Picker 回到编辑器。
 *
 * 项目 id 不在地址里（`lib/session.ts` 读完 `?pj=` 就把它从地址栏摘掉），所以编辑器与
 * Picker 的两格是**同一个 URL**，区别只在 `history.state` 上的这枚记号：
 *   * 去 Picker（`projectStore.showPicker`）：当前格还不是 Picker 格就 push 一格；
 *   * 后退到非 Picker 格而界面还停在 Picker：回当前项目（`returnToCurrent`）；
 *   * 前进到 Picker 格而界面在编辑器：再去一次 Picker（此时已在 Picker 格上，不再 push）；
 *   * 从 Picker 走别的路回到编辑器（「返回当前项目」/ 打开一个项目）：把那一格退掉，
 *     免得之后按一次后退什么都不发生。
 * 四条都在 `App.tsx` 的 `usePickerHistory` 里接线；这里只管记号本身。
 */
const MARK = 'tavottoPicker'

export function isPickerEntry(state: unknown = readState()): boolean {
  return typeof state === 'object' && state !== null && (state as Record<string, unknown>)[MARK] === true
}

export function pushPickerEntry(): void {
  try {
    if (!isPickerEntry()) window.history.pushState({ [MARK]: true }, '')
  } catch {
    /* 历史 API 不可用（沙盒 iframe 等）：只是少了后退键这条路，Picker 照常显示 */
  }
}

function readState(): unknown {
  try {
    return window.history.state
  } catch {
    return null
  }
}
