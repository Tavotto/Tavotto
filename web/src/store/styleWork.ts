/**
 * 样式写入的唯一在途 / 欠账状态。styleBinding 写，准入与同步器读同一份。
 * 不依赖动作或绑定编排，避免 actions ↔ styleBinding 成环；不另记准入副本。
 */
import { figKey } from '@/lib/styleOwned'
import { currentProjectId } from '@/lib/session'
import type { StyleProfileData } from '@/lib/stylePresets'
import type { PanelObject } from '@/types/document'
import { useDocumentStore } from './documentStore'

/**
 * 「此刻是哪一份文档的哪一张画布」：项目 · 文档 · 载入代次 · 画布。
 * `loadSeq` 在每一次整份替换文档时都会前进，即使 id 全都一样（版面 / 崩溃恢复）。
 * 样式写入、会话记账与样式面板重挂共用这一份，不按相同 id 继承旧代次的工作。
 */
export function documentGeneration(
  s: Pick<ReturnType<typeof useDocumentStore.getState>, 'documentId' | 'loadSeq' | 'activeCanvasId'>,
): string {
  return JSON.stringify([currentProjectId(), s.documentId, s.loadSeq, s.activeCanvasId])
}

const ledgerGeneration = (): string => `${documentGeneration(useDocumentStore.getState())}|`

/** 会话记账的键：代次 + 绑的是哪一条（换绑定 = 换一本账） */
export const styleLedgerKey = (styleId: string | undefined): string => `${ledgerGeneration()}${styleId ?? ''}`

export const styleWork = {
  /** 按代次 · 绑定 · 图保存；解绑保留，撤销恢复绑定时仍能结算 */
  pending: new Map<string, Map<string, StyleProfileData>>(),
  /**
   * 欠账里哪些项补的时候**不碰用户手改的 override**（同键同图，是 `pending` 的子集）：改样式 /
   * 跟上样式库记下的那几项。不在这里的（明确绑定、新图欠的整份）补的时候照旧覆盖（§十四）
   */
  keep: new Map<string, Map<string, StyleProfileData>>(),
  /** 排着 / 跑着的库写入个数；从 enqueue 起算，finally 落账 */
  inflight: 0,
}

/**
 * Admission must not race a queued writer or this generation's latent panel debt.
 * Keep every binding's debt: undo can restore an unbound style. Older generations
 * cannot settle against this document, even when the panel and file IDs match.
 */
export function hasPendingStyleWork(panel: PanelObject): boolean {
  if (styleWork.inflight > 0) return true
  const generation = ledgerGeneration()
  const fig = figKey(panel)
  return [...styleWork.pending].some(([key, debt]) => key.startsWith(generation) && debt.has(fig))
}
