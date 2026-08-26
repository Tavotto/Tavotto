import { create } from 'zustand'

/**
 * 多 Figure 交接的选择器状态（Session 6）。
 *
 * `tavotto open script.py` 产出不止一张图时，CLI 不静默选第一张，而是把
 * 脚本（选择信息）经 `?pick=` / `tavotto:open` 事件交进来；这里只存
 * 「该给哪个脚本开选择器」，条目本体由 FigurePickerDialog 从素材数据源
 * （assetStore + runtimeAssetStore）现算——选择器打开期间素材可能刷新，
 * 快照存进 store 反而会陈旧。
 */
interface FigurePickerStore {
  /** 正在选择的脚本（项目相对路径）；null = 选择器关闭 */
  script: string | null
  open: (script: string) => void
  close: () => void
}

export const useFigurePickerStore = create<FigurePickerStore>((set) => ({
  script: null,
  open: (script) => set({ script }),
  close: () => set({ script: null }),
}))
