/**
 * 吸附反馈的 target 判据（ADR 0128 §四）：只有「别的图里的元素」才给对方框。
 */
import { describe, expect, it } from 'vitest'
import type { SnapLine } from './geometry'
import { feedbackFor } from './snapFeedback'

const self = { x: 1, y: 2, w: 3, h: 4 }
const rect = { x: 10, y: 20, w: 30, h: 40 }
const line = (extra: Partial<SnapLine>): SnapLine => ({ v: 5, kind: 'axes', edge: 2, ...extra })

describe('feedbackFor', () => {
  it('别的图的元素（带框）：target = 对方框', () => {
    for (const kind of ['axes', 'colorbar', 'legend', 'text'] as const)
      expect(feedbackFor('x', line({ kind, panelId: 'b', rect }), self, 'a')).toEqual({ axis: 'x', v: 5, self, target: rect })
  })

  it('本图的线、没有所属面板的线、没带框的线：没有 target', () => {
    expect(feedbackFor('y', line({ panelId: 'a', rect }), self, 'a').target).toBeUndefined()
    expect(feedbackFor('y', line({ rect }), self, 'a').target).toBeUndefined()
    expect(feedbackFor('y', line({ panelId: 'b' }), self, 'a').target).toBeUndefined()
  })

  it('别的对象 / 别的图的外框与整图线、页面、参考线、网格：不是元素，没有 target', () => {
    for (const kind of ['frame', 'figure', 'page', 'guide', 'grid'] as const)
      expect(feedbackFor('x', line({ kind, panelId: 'b', rect }), self, 'a').target).toBeUndefined()
  })
})
