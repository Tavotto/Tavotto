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
  /**
   * 锁的**持有者凭据**（`beginRestore` 发的唯一号）。`restoring` 只记上下文，同一上下文里
   * 先后两次恢复的上下文一样——只凭 ctx 摘锁的话，先结束的那次会把还在写的那次的锁摘掉
   * （Codex #831 P1）。摘锁只认凭据。
   */
  restoreToken: number | null
  bump: () => void
  setPreview: (p: TimelinePreview | null) => void
  setNamingOpen: (v: boolean) => void
  /**
   * 挂上恢复锁，返回这一把的凭据；**这个上下文已经有一次恢复在飞就拒绝**（返回 null，什么都不改）。
   * 别的上下文挂着的旧锁不挡（A 的恢复在飞时换到 B，B 照样能恢复），直接换成 B 的。
   */
  beginRestore: (ctx: string) => number | null
  /** 摘锁：只摘**凭据相符**的那一把（过期的持有者摘不掉后来者的锁） */
  endRestore: (token: number) => void
  clear: () => void
}

let restoreSeq = 0

export const useTimelineStore = create<TimelineState>((set, get) => ({
  rev: 0,
  gen: 0,
  preview: null,
  namingOpen: false,
  restoring: null,
  restoreToken: null,
  bump: () => set((s) => ({ rev: s.rev + 1 })),
  setPreview: (preview) => set({ preview }),
  setNamingOpen: (namingOpen) => set({ namingOpen }),
  beginRestore: (ctx) => {
    if (get().restoring === ctx) return null
    const token = ++restoreSeq
    set({ restoring: ctx, restoreToken: token })
    return token
  },
  endRestore: (token) =>
    set((s) => (s.restoreToken === token ? { restoring: null, restoreToken: null } : {})),
  clear: () => set((s) => ({ preview: null, namingOpen: false, gen: s.gen + 1 })),
}))
