/**
 * 「此刻有没有一轮连续编辑开着」的唯一登记处。
 *
 * 为什么需要它（issue #131）：`documentStore.commit` 在 `state.txn` 存在时会
 * **静默并入当前事务**。那对手势内部的结构性改动是对的，对「用户点了一下另一个
 * 按钮」就完全不对——改字号的安静计时器还剩 300ms 时点左对齐，两个毫不相干的
 * 动作会被压成一条历史，一次撤销把字号和对齐一起吐出来。用户看到的就是
 * 「撤销乱跳、回不到我要的那一步」。
 *
 * 光调 `documentStore.endTxn()` 不够：`useFieldGesture` 自己还有
 * `open` 标记、安静计时器、SVG 预览会话和挂起的定稿渲染。事务被外人收掉而
 * hook 不知情的话，那些状态会一直悬着（预览会话不收工、定稿图永远不来）。
 * 所以收尾回调由手势自己登记，外部只喊一声 `finishActiveGesture()`。
 *
 * 同一时刻只允许一轮：属性页里两个控件同时开手势本来就是 bug，
 * 后开的那个会先把前一个收掉（与 `beginTxn` 的语义一致）。
 */

/** 当前开着的那一轮的收尾回调；null = 没有 */
let active: (() => void) | null = null

/** 收尾进行中——回调内部大多会走 endTxn → commit，绝不能再递归收一次 */
let finishing = false

/**
 * 登记「我这一轮开始了」，返回注销函数。
 *
 * 手势 `start()` 时登记、`end()` 与组件卸载时注销。返回的注销函数是幂等的，
 * 而且只注销**自己那一份**——收尾过程中别人已经接管时不会误伤。
 */
export function registerGesture(finish: () => void): () => void {
  if (active && active !== finish) finishActiveGesture()
  active = finish
  return () => {
    if (active === finish) active = null
  }
}

/**
 * 收掉当前开着的那一轮（没有就什么都不做）。
 *
 * 一切**离散动作**执行前必须先调它：对齐、分布、等宽等高、重置元素、
 * 清理孤儿 override、版本保存/恢复、写回历史恢复、undo/redo。
 *
 * **不经 keydown / 指针按下的入口必须走 `runDiscreteAction` 闸门，不许自己调它**：
 * 系统菜单（`runMenuAction` 全部分支，加速键可能先于 keydown 到达、甚至替代它）、
 * 原生剪贴板事件（`handleCopyEvent` / `handlePasteEvent`，桌面壳的预置「粘贴」直达）。
 * 这些入口前后被 Codex #671 抓到三次同一形状——有的忘了收（⌘D、⌘V 并进方向键微调那条
 * 撤销），有的收早了（焦点在属性输入框里、本该让位的 ⌘D 把连续编辑结掉）。闸门把「先判
 * 让位、不让位才收、再执行」钉成一处；`hooks/discreteActionGate.test.tsx` 逐个 action id
 * 与剪贴板事件查它。
 */
export function finishActiveGesture(): void {
  if (finishing) return
  const finish = active
  if (!finish) return
  finishing = true
  active = null
  try {
    finish()
  } finally {
    finishing = false
  }
}

/**
 * 此刻开着的那一次指针追踪（拖动 / 缩放 / 框选 / 绘制……）的「取消」出口；null = 没有。
 * 由 `canvas/interactions.trackPointer` 登记（为什么要有这个出口见那里）；住在 store 层是因为
 * 离开文档的 `projectStore` 也要调它，而 store 不许 import canvas（`importArchitecture` 门禁）。
 */
let pointerCancel: (() => void) | null = null

/** 登记这一次指针追踪的取消出口，返回注销函数（只注销自己那一份）。 */
export function registerPointerCancel(cancel: () => void): () => void {
  pointerCancel = cancel
  return () => {
    if (pointerCancel === cancel) pointerCancel = null
  }
}

/**
 * 取消此刻进行中的指针手势。回 true = 真的取消了一次；没有进行中的手势时什么都不做、
 * 回 false，调用方照常走它自己的逻辑。
 */
export function cancelActivePointerGesture(): boolean {
  const cancel = pointerCancel
  if (!cancel) return false
  cancel()
  return true
}

/** 现在有没有开着的一轮（开发态不变式与测试用） */
export const hasActiveGesture = (): boolean => active != null

/** 换项目 / 测试隔离：把登记表清干净，不触发收尾 */
export function resetGestureCoordinator(): void {
  active = null
  finishing = false
}

/**
 * 焦点 / 事件目标在这里时画布动作让位（输入框、可编辑文本里的原生编辑，对话框自己的键）。
 * 唯一一份判据：keydown 按 `e.target` 问它，系统菜单按 `document.activeElement` 问它，
 * 剪贴板事件按 `e.target` 问它——菜单加速键可能先于 keydown 截获按键、预置「粘贴」
 * 不经 keydown，几条路必须同一条判据，否则 ⌘D 在输入框里会被菜单变成「创建副本」。
 */
export function yieldsCanvasShortcuts(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false
  return (
    el.isContentEditable ||
    /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) ||
    el.closest('[role="dialog"]') != null
  )
}

/**
 * 离散动作的作用域：
 *   - `canvas`：画布动作（复制对象、删除、缩放、撤销重做、剪贴板）——焦点在输入框 / 对话框里
 *     时**让位**：不收手势、不执行（有 `onYield` 的交给它做原生那一份，如文本框里的撤销）；
 *   - `app`：不看焦点的应用级动作（保存、打开对话框、对齐、切换侧栏……）——总是先收再执行。
 */
export type DiscreteScope = 'canvas' | 'app'

/**
 * 离散动作的唯一闸门：先按 `yieldsCanvasShortcuts` 判让位——让位就不收手势、只跑
 * `onYield`；不让位才 `finishActiveGesture()`，再执行 `run`。回 `run` 的返回值，让位时回
 * `onYield` 的返回值（没有则 undefined）。
 */
export function runDiscreteAction<T>(
  scope: DiscreteScope,
  target: EventTarget | null,
  run: () => T,
  onYield?: () => T,
): T | undefined {
  if (scope === 'canvas' && yieldsCanvasShortcuts(target)) return onYield?.()
  finishActiveGesture()
  return run()
}
