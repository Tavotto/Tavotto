import { useMemo } from 'react'
import type { ReviewComment } from '@/lib/api'
import { owningPanel } from '@/lib/reviewGids'
import { placeComment, type Placement } from '@/lib/reviewPlacement'
import { useDocumentStore } from '@/store/documentStore'
import { useRenderStore } from '@/store/renderStore'
import { useReviewStore } from '@/store/reviewStore'

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
  return useMemo(() => {
    if (!detail) return []
    return detail.comments.map((comment, i) => ({
      comment,
      n: i + 1,
      placement: placeComment(comment, detail.latest_version, (gid) => !!owningPanel(objects, { byKey, latest }, gid)),
    }))
  }, [detail, objects, byKey, latest])
}
