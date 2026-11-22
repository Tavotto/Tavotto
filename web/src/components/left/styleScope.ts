import { create } from 'zustand'

/**
 * 样式面板顶上的「画布 | 单图」开关（2026-11-20 维护者二次拍板，ADR 0081 §十四）。
 *
 * **粘性**：只由用户点开关改，画布上选中谁变了不翻它。`null` = 从没选过——面板第一次打开时按
 * 当时有没有选中的图定一个起点（`StylePanel` 里取，不写进这里），之后以用户的选择为准。
 * 不落盘：换项目 / 重启回到「从没选过」。
 */
export type StyleScope = 'canvas' | 'figure'

interface StyleScopeState {
  scope: StyleScope | null
  setScope: (scope: StyleScope) => void
}

export const useStyleScopeStore = create<StyleScopeState>((set) => ({
  scope: null,
  setScope: (scope) => set({ scope }),
}))
