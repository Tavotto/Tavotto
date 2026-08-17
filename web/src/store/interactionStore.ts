import { create } from 'zustand'
import type { Rect4 } from '@/lib/axesLayout'
import type { Rect } from '@/lib/geometry'

export type DragKind =
  | 'none' | 'move' | 'resize' | 'marquee' | 'pan'
  | 'guide' | 'draw' | 'crop' | 'endpoint' | 'element'

export type DraftPreview = Rect & {
  tool: string
  start?: { x: number; y: number }
  end?: { x: number; y: number }
}

interface InteractionState {
  kind: DragKind
  /** 框选矩形（mm） */
  marquee: Rect | null
  /**
   * 正在绘制的新对象预览（mm）。箭头 / 直线额外带真实端点：预览画的是
   * 最终那条线（含箭头帽），不是包围盒虚线框；松手落对象也用同一对端点，
   * 保证「预览什么就得到什么」（吸附 / shift 角度锁都已折算在内）。
   */
  draft: DraftPreview | null
  /** 命中的吸附参考线（mm） */
  snapXs: number[]
  snapYs: number[]
  hoverId: string | null
  /** 图内元素编辑：hover 中的 gid */
  hoverGid: string | null
  /** 图内元素拖动中的分数位移，供选中框跟随 */
  gidDrag: { gid: string; dfx: number; dfy: number } | null
  /**
   * 子图拖动 / 缩放的预览框（figure 分数、top-origin），按 gid 索引。
   * 成组缩放时同时给出组框，单个子图拖动时只有 boxes 里的一项。
   */
  elementPreview: { boxes: Record<string, Rect4>; group?: Rect4 } | null
  /** 光标位置（mm），状态栏显示 */
  cursor: { x: number; y: number } | null
  /** 从标尺拖出中的参考线 */
  pendingGuide: { axis: 'x' | 'y'; pos: number } | null

  begin: (kind: DragKind) => void
  end: () => void
  setMarquee: (r: Rect | null) => void
  setDraft: (d: DraftPreview | null) => void
  setSnap: (xs: number[], ys: number[]) => void
  setHover: (id: string | null) => void
  setHoverGid: (gid: string | null) => void
  setGidDrag: (d: { gid: string; dfx: number; dfy: number } | null) => void
  setElementPreview: (p: { boxes: Record<string, Rect4>; group?: Rect4 } | null) => void
  setCursor: (c: { x: number; y: number } | null) => void
  setPendingGuide: (g: { axis: 'x' | 'y'; pos: number } | null) => void
}

export const useInteractionStore = create<InteractionState>((set) => ({
  kind: 'none',
  marquee: null,
  draft: null,
  snapXs: [],
  snapYs: [],
  hoverId: null,
  hoverGid: null,
  gidDrag: null,
  elementPreview: null,
  cursor: null,
  pendingGuide: null,

  begin: (kind) => set({ kind }),
  end: () =>
    set({
      kind: 'none',
      marquee: null,
      draft: null,
      snapXs: [],
      snapYs: [],
      pendingGuide: null,
      gidDrag: null,
      elementPreview: null,
    }),
  setMarquee: (marquee) => set({ marquee }),
  setDraft: (draft) => set({ draft }),
  setSnap: (snapXs, snapYs) =>
    set((s) =>
      s.snapXs.length === snapXs.length &&
      s.snapYs.length === snapYs.length &&
      s.snapXs.every((v, i) => v === snapXs[i]) &&
      s.snapYs.every((v, i) => v === snapYs[i])
        ? s
        : { snapXs, snapYs },
    ),
  setHover: (hoverId) => set((s) => (s.hoverId === hoverId ? s : { hoverId })),
  setHoverGid: (hoverGid) => set((s) => (s.hoverGid === hoverGid ? s : { hoverGid })),
  setGidDrag: (gidDrag) => set({ gidDrag }),
  setElementPreview: (elementPreview) => set({ elementPreview }),
  setCursor: (cursor) => set({ cursor }),
  setPendingGuide: (pendingGuide) => set({ pendingGuide }),
}))
