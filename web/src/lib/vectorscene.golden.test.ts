import { describe, expect, it } from 'vitest'
import vectors from '../../../tests/golden/vectorscene_vectors.json'
import { elementRectOnPage } from '@/canvas/elementGeometry'
import type { Rect4 } from '@/lib/axesLayout'
import type { PanelObject } from '@/types/document'

/**
 * ADR 0208：后端 `engine/vectorscene.panel_rect_to_page` 与画布上真正画元素框的 `elementRectOnPage` 逐数相同
 * （裁剪、90° 旋转、翻转）。向量由 `scripts/gen_vectorscene_vectors.py` 生成。
 */
type Case = { panel: Record<string, unknown>; rects: { x: number; y: number; w: number; h: number }[] }
const { boxes, cases } = vectors as unknown as { boxes: Rect4[]; cases: Case[] }

describe('vectorscene ↔ elementRectOnPage（golden）', () => {
  cases.forEach((c, i) => {
    it(`panel ${i}`, () => {
      const panel = c.panel as unknown as PanelObject
      boxes.forEach((b, k) => {
        const got = elementRectOnPage(panel, b)
        const want = c.rects[k]
        for (const key of ['x', 'y', 'w', 'h'] as const) expect(got[key]).toBeCloseTo(want[key], 9)
      })
    })
  })
})
