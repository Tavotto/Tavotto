/**
 * 样式写入的唯一在途 / 欠账状态。styleBinding 写，准入与同步器读同一份。
 * 不依赖动作或绑定编排，避免 actions ↔ styleBinding 成环；不另记准入副本。
 */
import { figKey } from '@/lib/styleOwned'
import type { StyleProfileData } from '@/lib/stylePresets'
import type { PanelObject } from '@/types/document'

export const styleWork = {
  /** 按代次 · 绑定 · 图保存；解绑保留，撤销恢复绑定时仍能结算 */
  pending: new Map<string, Map<string, StyleProfileData>>(),
  /** 排着 / 跑着的库写入个数；从 enqueue 起算，finally 落账 */
  inflight: 0,
}

/** Admission must not race a queued style writer or latent panel debt. */
export const hasPendingStyleWork = (panel: PanelObject): boolean =>
  styleWork.inflight > 0 || [...styleWork.pending.values()].some(debt => debt.has(figKey(panel)))
