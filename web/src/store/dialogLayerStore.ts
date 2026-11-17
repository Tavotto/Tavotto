import { create } from 'zustand'

/**
 * 此刻开着几个模态对话框（`ui/Dialog` 打开时登记、关闭 / 卸载时注销）。
 * 通知轨据此决定自己画在哪一层：没有模态时在画布列里（`z-canvas-chrome`）；有模态时移进 body 的 `z-toast` 层，
 * 压在对话框上面（对话框 `z-dialog` 的遮罩会把画布列里的胶囊盖暗；「已导出 · 在 Finder 中显示」得看得见、点得到）。
 */
export const useDialogLayerStore = create<{ open: number; epoch: number }>(() => ({ open: 0, epoch: 0 }))

export function registerDialogOpen(): () => void {
  useDialogLayerStore.setState((s) => ({
    open: s.open + 1,
    // 从无模态进入模态层时推进一代：此前画布上残留的普通 done/info 提示留在遮罩下，不越层压到对话框上
    epoch: s.open === 0 ? s.epoch + 1 : s.epoch,
  }))
  return () => useDialogLayerStore.setState((s) => ({ open: Math.max(0, s.open - 1) }))
}
