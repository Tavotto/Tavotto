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

/** 现在有没有开着的一轮（开发态不变式与测试用） */
export const hasActiveGesture = (): boolean => active != null

/** 换项目 / 测试隔离：把登记表清干净，不触发收尾 */
export function resetGestureCoordinator(): void {
  active = null
  finishing = false
}
