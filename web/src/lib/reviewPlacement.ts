import type { ReviewComment, ReviewShareSummary } from '@/lib/api'

/**
 * 意见在「当前画布」上的落位（ADR 0187 §五）——**判据只有这一处**，画布圆点、列表的「原位置已变化」都读它。
 *
 * - `overall`：整体意见，没有位置，不画圆点、也不说位置变了；
 * - `element`：元素 `gid` 在当前画布里还在 → 圆点跟着元素现在的位置走（哪个版本留的都一样）；
 * - `region`：区域意见，且就是最新版留的 → 画面比例坐标在当前画布上仍成立，画在原处；
 * - `moved`：元素没了，或是旧版本留的区域意见 → 不在画布上硬画，列表里用该版本快照标原位置。
 */
export type Placement = 'overall' | 'element' | 'region' | 'moved'

export function placeComment(
  c: Pick<ReviewComment, 'v' | 'anchor'>,
  latestVersion: number,
  hasGid: (gid: string) => boolean,
): Placement {
  const a = c.anchor
  if (a.kind === 'overall') return 'overall'
  if (a.kind === 'element') return hasGid(a.gid) ? 'element' : 'moved'
  return c.v === latestVersion ? 'region' : 'moved'
}

/** 分享记录是否还活着（能更新 / 能复制链接）：已停止或已过期的只剩意见副本 */
export const shareLive = (s: Pick<ReviewShareSummary, 'revoked' | 'expired'>): boolean => !s.revoked && !s.expired

/** 「审阅」页签是否出现：只看本机有没有这个 target 的未删除分享记录（引擎 `/api/review/for`） */
export const reviewTabVisible = (enabled: boolean, share: ReviewShareSummary | null): boolean => enabled && share !== null

/**
 * 通知轨胶囊是否出现（照联系作者那只）：有未读、总数比用户 × 掉时多、对话框没开、轨上还有位置。
 * × 之后只有未读数再增加才重现。
 */
export function reviewCapsuleShown(o: {
  enabled: boolean
  total: number
  dismissedAt: number
  dialogOpen: boolean
  slotsFree: boolean
}): boolean {
  return o.enabled && o.total > 0 && o.total > o.dismissedAt && !o.dialogOpen && o.slotsFree
}

/** 两个 mm 矩形有没有重叠：排版里元素所在的面板完全在页面外 = 不在快照里，意见走「原位置已变化」 */
export const rectsOverlap = (
  a: { x: number; y: number; w: number; h: number },
  b: { x: number; y: number; w: number; h: number },
): boolean => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h
