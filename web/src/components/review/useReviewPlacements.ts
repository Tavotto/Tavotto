import { useMemo } from 'react'
import type { ReviewComment } from '@/lib/api'
import { owningPanel } from '@/lib/reviewGids'
import { placeComment, rectsOverlap, type Placement } from '@/lib/reviewPlacement'
import { useDocumentStore } from '@/store/documentStore'
import { useRenderStore } from '@/store/renderStore'
import { useReviewStore } from '@/store/reviewStore'
import { useWorkspaceStore } from '@/store/workspaceStore'

export interface PlacedComment {
  comment: ReviewComment
  /** 1 起的序号（画布圆点上的数字，与列表同序） */
  n: number
  placement: Placement
}

/** 当前分享的每条意见在**当前画布**上的落位（判据只有 `lib/reviewPlacement.placeComment`）。画布圆点与列表共用。 */
export function useReviewPlacements(): PlacedComment[] {
  const detail = useReviewStore((s) => s.detail)
  const objects = useDocumentStore((s) => s.doc.objects)
  const byKey = useRenderStore((s) => s.byKey)
  const latest = useRenderStore((s) => s.latest)
  const page = useDocumentStore((s) => s.doc.page)
  const layout = useWorkspaceStore((s) => s.mode !== 'fast_edit')
  return useMemo(() => {
    if (!detail) return []
    return detail.comments.map((comment, i) => ({
      comment,
      n: i + 1,
      placement: placeComment(comment, detail.latest_version, (gid) => {
        const o = owningPanel(objects, { byKey, latest }, gid)
        // 排版的快照就是页面图：面板整个在页面外的元素不在快照里，不在画布硬画
        return !!o && (!layout || rectsOverlap(o, { x: 0, y: 0, w: page.w, h: page.h }))
      }),
    }))
  }, [detail, objects, byKey, latest, page, layout])
}
