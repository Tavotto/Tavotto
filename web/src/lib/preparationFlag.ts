/**
 * 准备面板（T09 / T09b，ADR 0116）的**本地体验开关**——只决定「素材库脚本行 / 项目检查条 / 接入中心逐行试运行」这三个
 * 首跑入口走哪条路：
 *
 * * 开（默认，T09b 迁完之后仍默认开）：三个入口都只打开准备面板，背后是后端准备会话（`/api/engine/preparation-sessions`）——
 *   检查、授权、运行、回答 input、进入编辑都在同一处，执行只在认领后端生成的 `run` 动作之后发生；参数草稿在打开那一刻随目标冻结。
 * * 关（`localStorage['tavotto.preparationPanel'] = 'off'`）：三个入口都回到**同一台**旧的同步试运行状态机（`scriptRunStore.run`
 *   → `/api/registry/probe`；接入中心 T09b 起委派到它，不再自己记一份门与重跑）。旧路径仍完整可用：参数草稿照样经
 *   `probeWithDraft` 带上（T03：不会退化成空 argv、不丢运行配置），门照样弹同一个框、作答后 `rerunGated` 重跑停在门上的那一行。
 *   保留一版，给面板出问题时的退路；退出条件见 ADR 0116 §七。
 *
 * 渲染路上的门（运行目录 / 依赖 / 缺数据的对话框）与完整 PNG 准入**不受**这个开关影响：它们是编辑已知图时的执行器
 * （合同 §A：render / PNG 准入与 probe 是不同的执行器），对话框是同一后端判据（`pool._new_worker` 的两道门）的薄展示适配器，
 * 答完续上的是被挡住的那次渲染 / 准入，不是脚本首跑（ADR 0116 §二、§七）。
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
