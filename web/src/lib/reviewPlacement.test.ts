import { describe, expect, it } from 'vitest'
import { placeComment, reviewCapsuleShown, reviewTabVisible, shareLive } from './reviewPlacement'
import type { ReviewAnchor, ReviewShareSummary } from './api'

const el = (gid: string): ReviewAnchor => ({ kind: 'element', gid, box: [0, 0, 0.1, 0.1] })
const region: ReviewAnchor = { kind: 'region', box: [0.1, 0.1, 0.2, 0.2] }
const has = (...gids: string[]) => (g: string) => gids.includes(g)

describe('placeComment：原位置已变化的判据', () => {
  it('整体意见没有位置', () => {
    expect(placeComment({ v: 1, anchor: { kind: 'overall' } }, 2, has(), true)).toBe('overall')
  })
  it('元素还在：圆点跟着元素走，不管哪个版本留的', () => {
    expect(placeComment({ v: 1, anchor: el('legend') }, 3, has('legend'), true)).toBe('element')
    expect(placeComment({ v: 3, anchor: el('legend') }, 3, has('legend'), true)).toBe('element')
  })
  it('元素没了：不在画布画，列表标原位置已变化', () => {
    expect(placeComment({ v: 3, anchor: el('legend') }, 3, has('axis'), true)).toBe('moved')
  })
  it('区域意见：最新版留的、画布没动过才画在原处；旧版本留的算原位置已变化', () => {
    expect(placeComment({ v: 2, anchor: region }, 2, has(), true)).toBe('region')
    expect(placeComment({ v: 1, anchor: region }, 2, has(), true)).toBe('moved')
  })
  it('分享后本地画布改过（changed=true）：最新版的区域意见也不再画在旧坐标上；元素意见不受影响', () => {
    expect(placeComment({ v: 2, anchor: region }, 2, has(), false)).toBe('moved')
    expect(placeComment({ v: 2, anchor: el('legend') }, 2, has('legend'), false)).toBe('element')
    expect(placeComment({ v: 2, anchor: { kind: 'overall' } }, 2, has(), false)).toBe('overall')
  })
})

const share = (over: Partial<ReviewShareSummary> = {}): ReviewShareSummary => ({
  share_id: 's1', review_url: 'u', kind: 'figure', target_id: 'f', title: 't', note: '', latest_version: 1,
  created_at: '', updated_at: '', expires_at: '', revoked: false, expired: false, comment_count: 0, open_count: 0, unread: 0,
  ...over,
})

describe('「审阅」页签出现条件', () => {
  it('只看这个 target 有没有分享记录（且功能开着）', () => {
    expect(reviewTabVisible(true, share())).toBe(true)
    expect(reviewTabVisible(true, null)).toBe(false)
    expect(reviewTabVisible(false, share())).toBe(false)
  })
  it('已停止 / 已过期的记录页签仍在，但不算 live', () => {
    expect(reviewTabVisible(true, share({ revoked: true }))).toBe(true)
    expect(shareLive(share({ revoked: true }))).toBe(false)
    expect(shareLive(share({ expired: true }))).toBe(false)
    expect(shareLive(share())).toBe(true)
  })
})

describe('通知轨胶囊重现逻辑', () => {
  const base = { enabled: true, total: 2, dismissedAt: 0, dialogOpen: false, slotsFree: true }
  it('有未读就出现', () => expect(reviewCapsuleShown(base)).toBe(true))
  it('× 之后同样的未读数不再出现，增加了才重现', () => {
    expect(reviewCapsuleShown({ ...base, dismissedAt: 2 })).toBe(false)
    expect(reviewCapsuleShown({ ...base, total: 3, dismissedAt: 2 })).toBe(true)
  })
  it('弹窗开着 / 功能关着 / 没有未读 / 轨上没位置都不出现', () => {
    expect(reviewCapsuleShown({ ...base, dialogOpen: true })).toBe(false)
    expect(reviewCapsuleShown({ ...base, enabled: false })).toBe(false)
    expect(reviewCapsuleShown({ ...base, total: 0 })).toBe(false)
    expect(reviewCapsuleShown({ ...base, slotsFree: false })).toBe(false)
  })
})

import { rectsOverlap } from './reviewPlacement'
describe('rectsOverlap：页面外的面板不在快照里', () => {
  const page = { x: 0, y: 0, w: 100, h: 80 }
  it('有重叠 / 完全在外 / 只贴边', () => {
    expect(rectsOverlap({ x: 90, y: 70, w: 40, h: 30 }, page)).toBe(true)
    expect(rectsOverlap({ x: 500, y: 0, w: 40, h: 30 }, page)).toBe(false)
    expect(rectsOverlap({ x: 100, y: 0, w: 40, h: 30 }, page)).toBe(false)
  })
})
