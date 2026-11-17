import { create } from 'zustand'
import type { V2Patch } from '@/lib/journalV2'

/**
 * 本次会话里取到的各刊 v2 折叠结果（只在内存，不进项目文件）。
 * 「需要你确认」那一组只读这里：取不到（离线 / 被拒 / 开关关）= 没有这一组，不是报错。
 * 项目代际：`documentStore` 换项目时不需要清——键是期刊 id，内容只由那本刊的 v2 数据决定。
 */
interface JournalV2State {
  byJournal: Record<string, V2Patch | null>
  remember: (journalId: string, patch: V2Patch | null) => void
}

export const useJournalV2Store = create<JournalV2State>((set) => ({
  byJournal: {},
  remember: (journalId, patch) => set((s) => ({ byJournal: { ...s.byJournal, [journalId]: patch } })),
}))
