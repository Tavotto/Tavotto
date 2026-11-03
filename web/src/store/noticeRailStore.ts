import { create } from 'zustand'

/**
 * 底部通知轨此刻最上面那条在场通知（没在退场的）的顶边，窗口坐标；没有通知 = null。`NotificationRail` 每次渲染后
 * 量一次写进来（量真实的盒，不按「条数 × 行高」估——长文案的通知会折成好几行）。浮动快编胶囊（`ContextBar`）据此把通知轨
 * 占的那一带当禁区：贴在对象下方会与通知轨相交就翻到上方 / 夹到通知轨之上，否则通知上的按钮被胶囊盖住点不到。
 */
export const useNoticeRailStore = create<{ top: number | null; setTop: (v: number | null) => void }>((set, get) => ({
  top: null,
  setTop: (v) => {
    if (get().top !== v) set({ top: v })
  },
}))
