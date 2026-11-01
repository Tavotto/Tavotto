import { create } from 'zustand'

/** 回滚冲突后「查看差异」要看的那一次修改 */
export interface RevertDiffTarget {
  diff: string
  script?: string
}

interface RevertDiffState {
  target: RevertDiffTarget | null
  open: (target: RevertDiffTarget) => void
  close: () => void
}

/** 同 `traceDialogStore`：从会自己收起的任务历史弹层里打开的对话框，状态放这里，由常驻的 `RevertDiffHost` 画 */
export const useRevertDiffStore = create<RevertDiffState>((set) => ({
  target: null,
  open: (target) => set({ target }),
  close: () => set({ target: null }),
}))
