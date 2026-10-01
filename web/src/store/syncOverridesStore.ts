import { create } from 'zustand'

/**
 * 「把这些修改用到同脚本的其他图…」窗口的开合状态（2026-10-01，设计稿 C5 / C11：
 * 入口从属性栏「源文件与高级」搬到画布对象的右键菜单）。
 *
 * 菜单一合上菜单项就卸载，窗口不能挂在菜单里，所以开合放这里，窗口常驻在画布舞台上
 * （`SyncOverridesHost`）。只存「从哪张面板出发」——兄弟图、映射结果都是窗口自己的临时状态。
 */
interface SyncOverridesState {
  panelId: string | null
  open: (panelId: string) => void
  close: () => void
}

export const useSyncOverrides = create<SyncOverridesState>((set) => ({
  panelId: null,
  open: (panelId) => set({ panelId }),
  close: () => set((s) => (s.panelId ? { panelId: null } : s)),
}))

export const openSyncOverrides = (panelId: string) => useSyncOverrides.getState().open(panelId)
