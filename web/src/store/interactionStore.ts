import { create } from 'zustand'
import { perfSegmentBegin, perfSegmentEnd } from '@/perf/core'
import type { Rect4 } from '@/lib/axesLayout'
import type { Rect } from '@/lib/geometry'

export type DragKind =
  | 'none' | 'move' | 'resize' | 'marquee' | 'pan'
  | 'guide' | 'draw' | 'crop' | 'endpoint' | 'element' | 'pageedge'

export type DraftPreview = Rect & {
  tool: string
  start?: { x: number; y: number }
  end?: { x: number; y: number }
}

export interface ElementPreview {
  boxes: Record<string, Rect4>
  group?: Rect4
  /** 只改图框尺寸时用矩形表达目标，暂不描画引擎的旧圆角路径。 */
  resizingGid?: string
}

/** 一个吸上的轴：`v` 是线（页面 mm），`self` 是自身所属框，`target` 是别的图里被吸上的元素框 */
export interface SnapFeedback {
  axis: 'x' | 'y'
  v: number
  self: Rect
  target?: Rect
}

const NO_FEEDBACK: SnapFeedback[] = []

const sameRect = (a: Rect | undefined, b: Rect | undefined) =>
  a === b || (!!a && !!b && a.x === b.x && a.y === b.y && a.w === b.w && a.h === b.h)

const sameFeedback = (a: SnapFeedback[], b: SnapFeedback[]) =>
  a.length === b.length &&
  a.every(
    (f, i) =>
      f.axis === b[i].axis &&
      f.v === b[i].v &&
      sameRect(f.self, b[i].self) &&
      sameRect(f.target, b[i].target),
  )

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
  /**
   * 吸附反馈（ADR 0128 §四）：每个吸上的轴一项——自身所属框（边加粗 + 虚线框）与，目标是别的图的
   * 元素时，对方的框。与 `snapXs/snapYs` 同一生命周期；不带反馈的吸附（画布对象平移等）为空。
   */
  snapFeedback: SnapFeedback[]
  hoverId: string | null
  /** 图内元素编辑：hover 中的 gid */
  hoverGid: string | null
  /** 图内元素拖动中的分数位移，供选中框跟随 */
  gidDrag: { gid: string; dfx: number; dfy: number } | null
  /** 图内独立箭头端点拖动中的预览端点（figure 分数、top-origin） */
  arrowPreview: { gid: string; a: [number, number]; b: [number, number] } | null
  /**
   * 子图拖动 / 缩放的预览框（figure 分数、top-origin），按 gid 索引。
   * 成组缩放时同时给出组框，单个子图拖动时只有 boxes 里的一项。
   */
  elementPreview: ElementPreview | null
  /** 光标位置（mm），状态栏显示 */
  cursor: { x: number; y: number } | null
  /**
   * 方向键微调这一段的累计位移（页面 mm，y 向下），画布读数盒显示；null = 没在微调。
   * 微调不占 `kind`：占了的话撤销会被 `undoRedoBlocked` 挡住，而微调中按 ⌘Z 应当
   * 先收掉这一段、再撤销它（`canvas/nudge.ts`）。
   */
  nudge: { dx: number; dy: number } | null
  /** 从标尺拖出中的参考线 */
  pendingGuide: { axis: 'x' | 'y'; pos: number } | null
  /**
   * 拖页面边中的尺寸标注状态：哪条边、吸上了哪一档栏宽（`snapLabel` 是已翻好的「规范 栏名」）；
   * `rolls` = 这一帧的读数是离散跳变（吸上 / 离开栏宽、⌥ 按下 / 松开）→ 会滚的数字滚过去，
   * 逐 mm 的连续拖动为 false → 读数即时换（与缩放读数同一条规矩）。
   * 只是画布叠加层的读数，不进文档；尺寸本身读文档（手势事务里每帧在写）。
   */
  pageEdge: { edge: 'l' | 'r' | 't' | 'b'; snapLabel: string | null; rolls: boolean } | null

  begin: (kind: DragKind) => void
  end: () => void
  setMarquee: (r: Rect | null) => void
  setDraft: (d: DraftPreview | null) => void
  setSnap: (xs: number[], ys: number[], feedback?: SnapFeedback[]) => void
  setHover: (id: string | null) => void
  setHoverGid: (gid: string | null) => void
  setGidDrag: (d: { gid: string; dfx: number; dfy: number } | null) => void
  setArrowPreview: (p: { gid: string; a: [number, number]; b: [number, number] } | null) => void
  setElementPreview: (p: ElementPreview | null) => void
  setCursor: (c: { x: number; y: number } | null) => void
  setNudge: (n: { dx: number; dy: number } | null) => void
  setPendingGuide: (g: { axis: 'x' | 'y'; pos: number } | null) => void
  setPageEdge: (p: InteractionState['pageEdge']) => void
}

export const useInteractionStore = create<InteractionState>((set) => ({
  kind: 'none',
  marquee: null,
  draft: null,
  snapXs: [],
  snapYs: [],
  snapFeedback: [],
  hoverId: null,
  hoverGid: null,
  gidDrag: null,
  arrowPreview: null,
  elementPreview: null,
  cursor: null,
  nudge: null,
  pendingGuide: null,
  pageEdge: null,

  // 一次拖动 = 性能探针的一个片段（ADR 0075）；没在录制时两个调用都是空转
  begin: (kind) => {
    perfSegmentBegin(kind)
    set({ kind })
  },
  end: () => {
    perfSegmentEnd()
    set({
      kind: 'none',
      marquee: null,
      draft: null,
      snapXs: [],
      snapYs: [],
      snapFeedback: [],
      pendingGuide: null,
      pageEdge: null,
      gidDrag: null,
      arrowPreview: null,
      elementPreview: null,
    })
  },
  setMarquee: (marquee) => set({ marquee }),
  setDraft: (draft) => set({ draft }),
  setSnap: (snapXs, snapYs, snapFeedback = NO_FEEDBACK) =>
    set((s) =>
      s.snapXs.length === snapXs.length &&
      s.snapYs.length === snapYs.length &&
      s.snapXs.every((v, i) => v === snapXs[i]) &&
      s.snapYs.every((v, i) => v === snapYs[i]) &&
      sameFeedback(s.snapFeedback, snapFeedback)
        ? s
        : { snapXs, snapYs, snapFeedback },
    ),
  setHover: (hoverId) => set((s) => (s.hoverId === hoverId ? s : { hoverId })),
  setHoverGid: (hoverGid) => set((s) => (s.hoverGid === hoverGid ? s : { hoverGid })),
  setGidDrag: (gidDrag) => set({ gidDrag }),
  setArrowPreview: (arrowPreview) => set({ arrowPreview }),
  setElementPreview: (elementPreview) => set({ elementPreview }),
  setCursor: (cursor) => set({ cursor }),
  setNudge: (nudge) => set({ nudge }),
  setPendingGuide: (pendingGuide) => set({ pendingGuide }),
  setPageEdge: (pageEdge) => set({ pageEdge }),
}))
