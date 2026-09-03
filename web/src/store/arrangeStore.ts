import { create } from 'zustand'
import type { AlignRef } from './actions'

/**
 * 排列的**参照**（选区包围盒 / 整个画布 / 主选对象）。
 *
 * 右侧 `ArrangeSection` 与画布上的多选浮动栏是同一组动作的两个入口，参照必须
 * 只有一份：以前它是 `ArrangeSection` 的模块级变量，浮动栏要读的话就得再抄一份
 * ——两份迟早分叉，分叉的表现是「浮动栏按画布对齐、属性页却显示着选区」。
 *
 * 性质：**UI 会话状态**。不进 `FigureDocument`（它不是文档的内容）、不进撤销
 * （换个参照不该占一条历史）、不 persist（它是「我这一轮想按什么对齐」，不是
 * 长期偏好；切文档 / 切项目也不重置——同一次会话里用户的手感应当连续）。
 * 存的是枚举，不存翻译后的字符串。
 */
interface ArrangeState {
  alignRef: AlignRef
  setAlignRef: (ref: AlignRef) => void
}

export const useArrangeStore = create<ArrangeState>((set) => ({
  alignRef: 'selection',
  setAlignRef: (alignRef) => set((s) => (s.alignRef === alignRef ? s : { alignRef })),
}))
