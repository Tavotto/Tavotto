/**
 * 准备面板（T09，ADR 0116）的**本地体验开关**——只决定「素材库脚本行 / 项目检查条上的运行入口」走哪条路：
 *
 * * 开（默认）：入口打开准备面板，背后是后端准备会话（`/api/engine/preparation-sessions`）——检查、授权、运行、
 *   回答 input、进入编辑都在同一处，执行只在认领后端生成的 `run` 动作之后发生；参数草稿在打开那一刻随目标冻结。
 * * 关（`localStorage['tavotto.preparationPanel'] = 'off'`）：回到旧的同步试运行（`scriptRunStore` →
 *   `/api/registry/probe`）。保留一版，给面板出问题时的退路；退出条件见 ADR 0116 §七。
 *
 * 接入中心（`RegistryDialog`）的逐行试运行、渲染路上的门（运行目录 / 依赖 / 缺数据的对话框）、PNG 准入**不受**
 * 这个开关影响——它们还没有迁到面板（ADR 0116 的退役表逐项登记了原因与退出条件）。
 *
 * 只是本机的呈现偏好：读不到 / 写不了 localStorage 时按默认（开）处理，不抛。
 */
export const PREPARATION_PANEL_KEY = 'tavotto.preparationPanel'

export function preparationPanelEnabled(): boolean {
  try {
    return localStorage.getItem(PREPARATION_PANEL_KEY) !== 'off'
  } catch {
    return true
  }
}
