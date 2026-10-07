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
  /**
   * 正在恢复的上下文（`timelineCtxKey`：项目代际 + 排版 id），没有为 null。两个恢复入口
   * （预览对话框页脚、抽屉选中行的内联条）共用这**一把锁**（Codex #679 / #831 P1）：
   * 它在时预览对话框以 `busy` 开着——遮罩挡住画布、×/Esc/点外面关不掉——抽屉也关不掉。
   * 按上下文记账：A 的恢复还在飞时换到 B，B 不该被锁住。只经 `VersionDialog.restoreUnderLock` 写。
   */
  restoring: string | null
  bump: () => void
  setPreview: (p: TimelinePreview | null) => void
  setNamingOpen: (v: boolean) => void
  /** 挂上 / 摘掉恢复锁；摘的时候只摘**自己**挂的那一把（`ctx` 不符不动） */
  beginRestore: (ctx: string) => void
  endRestore: (ctx: string) => void
  clear: () => void
}

export const useTimelineStore = create<TimelineState>((set) => ({
  rev: 0,
  gen: 0,
  preview: null,
  namingOpen: false,
  restoring: null,
  bump: () => set((s) => ({ rev: s.rev + 1 })),
  setPreview: (preview) => set({ preview }),
  setNamingOpen: (namingOpen) => set({ namingOpen }),
  beginRestore: (restoring) => set({ restoring }),
  endRestore: (ctx) => set((s) => (s.restoring === ctx ? { restoring: null } : {})),
  clear: () => set((s) => ({ preview: null, namingOpen: false, gen: s.gen + 1 })),
}))
