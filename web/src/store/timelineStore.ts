import { create } from 'zustand'
import type { LayoutVersionMeta } from '@/lib/api'
import type { FigureDocument } from '@/types/document'

/**
 * 排版时间线的界面状态（ADR 0101）：列表的换代计数、「正在预览哪个节点」、
 * 顶部「存为命名节点」小框开没开。
 *
 * **不是第二份文档**：预览的那份快照只拿来在预览对话框里画只读的大图，
 * 从不进 documentStore、不 commit、不进历史、不发后端。恢复是另一件事，走
 * `restoreLayoutVersion` 那一次 commit。
 *
 * 项目代际：`clear()` 在 `projectStore.resetForNewProject` 里被调——A 项目的一个
 * 预览绝不留到 B 项目的画布上；`docId` 也跟着预览走，文档换了就不认。`gen` 是
 * 这一代的编号：抽屉按「代际 + 排版 id」给本地列表记账，换了就不再显示旧列表
 * （同名排版 id 在两个项目里也不会串）。
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
  /** 项目代际：每次 `clear()`（换项目）+1 */
  gen: number
  preview: TimelinePreview | null
  /** 工作面板顶部「存为命名节点」小框开没开（⌥⌘S、命令面板共用这一个开关；抽屉里的按钮有自己的展开态） */
  namingOpen: boolean
  bump: () => void
  setPreview: (p: TimelinePreview | null) => void
  setNamingOpen: (v: boolean) => void
  clear: () => void
}

export const useTimelineStore = create<TimelineState>((set) => ({
  rev: 0,
  gen: 0,
  preview: null,
  namingOpen: false,
  bump: () => set((s) => ({ rev: s.rev + 1 })),
  setPreview: (preview) => set({ preview }),
  setNamingOpen: (namingOpen) => set({ namingOpen }),
  clear: () => set((s) => ({ preview: null, namingOpen: false, gen: s.gen + 1 })),
}))
