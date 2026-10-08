/**
 * 换取景 / 重置裁剪（`applyCropDraft`，属性页「重置裁剪」与「适应」都走它）在翻转过的面板上（#833）：
 * 完整图在页面上的落位保持不动。画布画这张图是先在内容空间翻转、再旋转；只认旋转的话，重置裁剪后整张图
 * 跳到镜像那一边。
 *
 * 期望值按画面直接推（不经被测的换算）：水平翻转时内容 x 越大画得越靠左，取景窗的内容左缘画在面板右缘上，
 * 所以完整图在页面上横跨 [右缘 − (1 − crop.x)·整宽, 右缘 + crop.x·整宽]；垂直翻转同理。
 */
import { describe, expect, it } from 'vitest'
import type { PanelObject } from '@/types/document'
import { applyCropDraft } from './actions'

// 完整图 100 × 80 mm；取景窗左右、上下都不对称，翻没翻转一眼可辨
const panelWith = (flip: Partial<PanelObject>, crop: { x: number; y: number; w: number; h: number }): PanelObject =>
  ({
    id: 'p', type: 'panel', x: 10, y: 20, w: 100 * crop.w, h: 80 * crop.h, fileId: 'F.pdf', fileKind: 'pdf',
    nativeW: 100, nativeH: 80, overrides: [], crop, ...flip,
  }) as unknown as PanelObject

describe('applyCropDraft：翻转面板上重置裁剪，完整图不挪', () => {
  it('flipH：取景窗 x 0.1–0.7（右缘 70）→ 完整图 x −20 … 80', () => {
    const o = panelWith({ flipH: true }, { x: 0.1, y: 0.1, w: 0.6, h: 0.7 })
    applyCropDraft(o, undefined)
    expect(o.crop).toBeUndefined()
    expect(o.x).toBeCloseTo(70 - 0.9 * 100, 9)
    expect(o.w).toBeCloseTo(100, 9)
    // 纵向没翻：上缘 = 20 − 0.1 × 80
    expect(o.y).toBeCloseTo(20 - 0.1 * 80, 9)
  })

  it('flipV：取景窗 y 0.1–0.8（下缘 76）→ 完整图 y 4 … 84', () => {
    const o = panelWith({ flipV: true }, { x: 0.1, y: 0.1, w: 0.6, h: 0.7 })
    applyCropDraft(o, undefined)
    expect(o.y).toBeCloseTo(76 - 0.9 * 80, 9)
    expect(o.h).toBeCloseTo(80, 9)
    expect(o.x).toBeCloseTo(10 - 0.1 * 100, 9)
  })
})
