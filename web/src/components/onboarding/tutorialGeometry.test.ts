import { describe, expect, it } from 'vitest'
import { DEMO_FIGURE } from '@/lib/onboarding/demoFigure'
import {
  CALLOUT_BUBBLE_H,
  CALLOUT_GAP,
  estimateTextBox,
  placeCallouts,
  rectsOverlap,
  type Rect,
} from './tutorialGeometry'

/**
 * 欢迎页三枚标注气泡的落点（回归：beta 0.19.0b1「修改文字」气泡压在标签行与图标题之间、
 * 引线圆点落在标题文字中间）。判据是几何而不是像素：气泡矩形与标题文字 bbox、与彼此、与卡片外沿都不相交。
 */
const F = DEMO_FIGURE
const STAGES = [
  { w: 245, h: 176 },
  { w: 324, h: 233 },
  { w: 386, h: 278 },
]
const BUBBLE_W = 164
const titleBox = estimateTextBox(F.title.text, F.title.sizePt, F.title.anchor)
const targets = [
  { key: 'title', box: titleBox },
  { key: 'legend', box: F.legend.box as [number, number, number, number] },
  { key: 'position', box: F.xlabel.box as [number, number, number, number] },
]
const toPx = (b: readonly number[], s: number): Rect => ({ x: b[0] * s, y: b[1] * s, w: b[2] * s, h: b[3] * s })

describe('placeCallouts：欢迎页标注气泡', () => {
  for (const stage of STAGES) {
    describe(`图框 ${stage.w}x${stage.h}`, () => {
      const s = stage.w / F.figure.width
      const placed = placeCallouts({ figure: F.figure, stage, targets, bubbleW: BUBBLE_W })
      const by = (k: string) => placed.find((p) => p.key === k)!

      it('气泡不与标题文字 bbox 相交（也不与图里任何被指元素相交）', () => {
        for (const p of placed) for (const t of targets) expect(rectsOverlap(p.bubble, toPx(t.box, s))).toBe(false)
        expect(rectsOverlap(by('title').bubble, toPx(titleBox, s))).toBe(false)
      })

      it('气泡在图框右侧的槽里：不压图、竖向不出图框上下沿（即不探出卡片、不碰上方标签行 / 下方按钮）', () => {
        const figure: Rect = { x: 0, y: 0, w: stage.w, h: stage.h }
        for (const p of placed) {
          expect(rectsOverlap(p.bubble, figure)).toBe(false)
          expect(p.bubble.x).toBeGreaterThanOrEqual(stage.w)
          expect(p.bubble.y).toBeGreaterThanOrEqual(0)
          expect(p.bubble.y + p.bubble.h).toBeLessThanOrEqual(stage.h)
        }
      })

      it('三枚气泡彼此不相交，间隙不小于 gap', () => {
        const sorted = [...placed].sort((a, b) => a.bubble.y - b.bubble.y)
        for (let i = 1; i < sorted.length; i++) {
          expect(sorted[i].bubble.y - (sorted[i - 1].bubble.y + CALLOUT_BUBBLE_H)).toBeGreaterThanOrEqual(CALLOUT_GAP - 1e-6)
        }
      })

      it('引线端点落在被指元素右边缘之外（不遮挡文字），且与元素同高', () => {
        for (const t of targets) {
          const p = by(t.key)
          const box = toPx(t.box, s)
          expect(p.anchor.x).toBeGreaterThan(box.x + box.w)
          expect(p.anchor.y).toBeGreaterThanOrEqual(box.y)
          expect(p.anchor.y).toBeLessThanOrEqual(box.y + box.h)
        }
      })
    })
  }

  it('竖向拥挤时互相推开而不是叠在一起', () => {
    const crowded = placeCallouts({
      figure: F.figure,
      stage: { w: 200, h: 100 },
      targets: [
        { key: 'a', box: [10, 40, 20, 5] },
        { key: 'b', box: [10, 42, 20, 5] },
        { key: 'c', box: [10, 44, 20, 5] },
      ],
      bubbleW: 100,
    })
    for (let i = 0; i < crowded.length; i++)
      for (let j = i + 1; j < crowded.length; j++) expect(rectsOverlap(crowded[i].bubble, crowded[j].bubble)).toBe(false)
  })
})
