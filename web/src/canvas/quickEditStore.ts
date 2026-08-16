import { create } from 'zustand'

/**
 * 右键快捷编辑的开合状态。
 *
 * 与组件分文件：ObjectView / PanelView 只需要「打开」这一个动作，
 * 不该为此把整个弹层组件模块拖进来。
 */
export type QuickTarget =
  /** focusText：双击文字元素进来，弹层打开即聚焦内容输入框 */
  | { kind: 'element'; panelId: string; gid: string; focusText?: boolean }
  | { kind: 'object'; id: string }

interface QuickEditState {
  target: QuickTarget | null
  /** 光标位置（视口坐标），弹层贴着它出现 */
  at: { x: number; y: number }
  open: (target: QuickTarget, x: number, y: number) => void
  close: () => void
}

export const useQuickEdit = create<QuickEditState>((set) => ({
  target: null,
  at: { x: 0, y: 0 },
  open: (target, x, y) => set({ target, at: { x, y } }),
  close: () => set((s) => (s.target ? { target: null } : s)),
}))

export const openQuickEdit = (
  target: QuickTarget,
  e: { clientX: number; clientY: number },
) => useQuickEdit.getState().open(target, e.clientX, e.clientY)
