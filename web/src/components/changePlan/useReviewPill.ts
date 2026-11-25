import { effectiveStatus } from '@/lib/changePlanApply'
import { shareLive } from '@/lib/reviewPlacement'
import { useChangePlanStore } from '@/store/changePlanStore'
import { useDocumentStore } from '@/store/documentStore'
import { useReviewStore } from '@/store/reviewStore'
import { useWorkspaceStore } from '@/store/workspaceStore'
import { usePlanSource } from './usePlanFigure'
import type { PlacedComment } from '../review/useReviewPlacements'

export type PillMode = 'run' | 'update' | 'updated'

export interface ReviewPill {
  mode: PillMode
  panelId: string
  shareId: string
  /** 未处理意见的编号（意见 id → 审阅页签上的序号）；`run` 时交给方案请求，卡上的编号与页签一致 */
  numbers: Record<string, number>
}

/**
 * 审阅页签悬浮胶囊此刻该是什么（ADR 0206 §二）；不该出现 = null：
 * - `run`「交给 Agent 修改」：这张图有未处理意见时。**只在单图编辑里**——排版级审阅（多张图）本期不做；
 * - `update`「更新到同一链接」：这张图有一份已应用的方案、新版还没发到链接（画布相对最新版有改动）、链接还活着；
 * - `updated`「链接已更新」：应用过、链接已是最新版、没有新的未处理意见。
 */
export function useReviewPill(placed: PlacedComment[]): ReviewPill | null {
  const share = useReviewStore((s) => s.share)
  const changed = useReviewStore((s) => s.changed)
  const panelId = useWorkspaceStore((s) => (s.mode === 'fast_edit' ? s.activePanelId : null))
  const panel = useDocumentStore((s) => {
    if (!panelId) return undefined
    const o = s.doc.objects.find((x) => x.id === panelId)
    return o?.type === 'panel' ? o : undefined
  })
  const overrides = panel?.overrides ?? null
  const source = usePlanSource(panel)
  const applied = useChangePlanStore((s) =>
    s.order.some((k) => {
      const e = s.entries[k]
      return !!e && e.panelId === panelId && effectiveStatus(e, overrides, source) === 'applied'
    }),
  )
  if (!share || !panelId) return null
  const open = placed.filter((p) => !p.comment.resolved)
  const base = { panelId, shareId: share.share_id, numbers: Object.fromEntries(open.map((p) => [p.comment.id, p.n])) }
  if (applied && changed && shareLive(share)) return { ...base, mode: 'update' }
  if (open.length > 0) return { ...base, mode: 'run' }
  if (applied && !changed) return { ...base, mode: 'updated' }
  return null
}
