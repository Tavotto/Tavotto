import { create } from 'zustand'
import type { LayoutVersionMeta } from '@/lib/api'
import type { FigureDocument } from '@/types/document'

/**
 * 排版时间线的界面状态（ADR 0101）：列表的换代计数与「正在预览哪个节点」。
 *
 * **不是第二份文档**：预览的那份快照只拿来画一张只读的大图（`TimelinePreview`），
 * 从不进 documentStore、不 commit、不进历史、不发后端。恢复是另一件事，走
 * `restoreLayoutVersion` 那一次 commit。
 *
 * 项目代际：`clear()` 在 `projectStore.resetForNewProject` 里被调——A 项目的一个
 * 预览绝不留到 B 项目的画布上；`docId` 也跟着预览走，文档换了就不认。
 */
export interface TimelinePreview {
  docId: string
  meta: LayoutVersionMeta
  /** 快照正文；还在取的时候是 null */
  doc: FigureDocument | null
}

interface TimelineState {
  /** 节点变了（新拍 / 改名 / 删除）就 +1，打开着的抽屉据此重取列表 */
  rev: number
  preview: TimelinePreview | null
  /** 抽屉里「存为命名节点」输入框要不要抢焦点（⌥⌘S / 命令面板进来时） */
  focusNameRequest: number
  bump: () => void
  setPreview: (p: TimelinePreview | null) => void
  requestNameFocus: () => void
  clear: () => void
}

export const useTimelineStore = create<TimelineState>((set) => ({
  rev: 0,
  preview: null,
  focusNameRequest: 0,
  bump: () => set((s) => ({ rev: s.rev + 1 })),
  setPreview: (preview) => set({ preview }),
  requestNameFocus: () => set((s) => ({ focusNameRequest: s.focusNameRequest + 1 })),
  clear: () => set({ preview: null }),
}))
