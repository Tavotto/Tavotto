/**
 * Paired-frame adapter（native-manifest/2）与 DOM 预览 F basis 的移植测试。
 *
 * 移植自原型 `E:/0 - Project/svg-review-20261106/n5-tick-clipping-review/prototype/
 * web-tests/chartFrame.test.ts`（PLAN.md §2 第 1 步 / §7；产品落地名
 * `web/src/lib/chartFrame.test.ts`）。相对原型的调整点：
 *
 * - import/风格对齐产品 `web/src/lib/figureFrame.test.ts`：命名导入展开、
 *   describe 分组、常量与工厂命名放开。
 * - 末尾追加「computed frame 闭式判据（同源对）」金向量 describe：与
 *   `web/src/types/cropBounds.golden.test.ts` 同法，直接 import
 *   `tests/golden/computed_frame_vectors.json`（后端那一侧是
 *   `tests/test_chart_frame_contract.py`，两侧各读同一份向量，不读对方源码）。
 * - 原型用的被测 API 逐一对照补丁后源码核名未变：
 *   `@/lib/chartFrame`（applyComputedFrameDraft/computedFramePatch/
 *   sourceFrameOverride/validComputedFrame/PairedFrameAuthority）、
 *   `@/lib/figureFrame`（frameGeometryPatch/frameSwitchPatch/ManifestFrame）、
 *   `@/types/document`（panelFullSize/panelRotation/rotateVec/FigureDocument/
 *   PanelObject）、`@/store/svgPreviewStore`（beginPreview/cancelPreview/
 *   flushPreviewFrame/previewScale/previewTransform/resetPreview）、
 *   `@/lib/svgPreviewCoordinates`。
 */
import { afterEach, describe, expect, it } from 'vitest'

import frameGolden from '../../../tests/golden/computed_frame_vectors.json'
import {
  applyComputedFrameDraft,
  computedFramePatch,
  sourceFrameOverride,
  validComputedFrame,
  type PairedFrameAuthority,
} from '@/lib/chartFrame'
import { frameGeometryPatch, frameSwitchPatch, type ManifestFrame } from '@/lib/figureFrame'
import { svgPreviewCoordinates } from '@/lib/svgPreviewCoordinates'
import {
  beginPreview,
  cancelPreview,
  flushPreviewFrame,
  previewScale,
  previewTransform,
  resetPreview,
} from '@/store/svgPreviewStore'
import {
  panelFullSize,
  panelRotation,
  rotateVec,
  type FigureDocument,
  type PanelObject,
} from '@/types/document'

const MM_PER_PT = 25.4 / 72

const authority: PairedFrameAuthority = {
  inputIdentity: 'exact-source-model-patches',
  renderRevision: 'paired-render',
  frame: {
    version: 1,
    source: 'computed-svg',
    source_viewport_pt: [100, 100],
    rect_pt: [-21, -1, 122, 109],
    edit_basis: 'source-viewport-pt',
  },
}

const panel = (): PanelObject => ({
  type: 'panel',
  id: 'chart',
  fileId: 'chart.svg',
  fileKind: 'svg',
  x: 10,
  y: 24,
  w: 55,
  h: 45,
  nativeW: 100 * MM_PER_PT,
  nativeH: 100 * MM_PER_PT,
  overrides: [
    { gid: 'axes_0.xlabel', prop: 'pos_frac', value: [0.2, 0.7] },
    { gid: 'axes_0.lines_0', prop: 'linewidth', value: 5 },
  ],
})

/** 图内 S 分数点（native 是包住它的那块 F）在页面上的位置；换框前后必须重合。 */
function point(p: PanelObject, native: [number, number, number, number], u: number, v: number) {
  const [l, t, w, h] = native
  const c = p.crop ?? { x: 0, y: 0, w: 1, h: 1 }
  const full = panelFullSize(p)
  let dx = ((u - l) / w - c.x - c.w / 2) * full.w
  let dy = ((v - t) / h - c.y - c.h / 2) * full.h
  if (p.flipH) dx = -dx
  if (p.flipV) dy = -dy
  const [x, y] = rotateVec(dx, dy, panelRotation(p))
  return [p.x + p.w / 2 + x, p.y + p.h / 2 + y]
}

describe('paired computed frame adapter', () => {
  it.each([0, 90, 180, 270] as const)(
    'paired reframe rotation %i keeps source page anchors and exact S edit list',
    rotation => {
      for (const flipH of [false, true])
        for (const flipV of [false, true])
          for (const crop of [undefined, { x: 0.1, y: 0.1, w: 0.8, h: 0.8 }]) {
            const before = { ...panel(), rotation, flipH, flipV, crop }
            const after = { ...before, ...computedFramePatch(before, authority, authority.inputIdentity) }
            expect(after.overrides).toBe(before.overrides)
            for (const [x, y] of [
              [10, 70],
              [90, 70],
              [20, 30],
            ]) {
              const a = point(before, [0, 0, 100, 100], x, y)
              const b = point(after, authority.frame.rect_pt, x, y)
              expect(b[0]).toBeCloseTo(a[0], 8)
              expect(b[1]).toBeCloseTo(a[1], 8)
            }
          }
    },
  )

  it('new F-relative edit converts once to S; colors and restored full array stay untouched', () => {
    const f = authority.frame.rect_pt
    const edit = {
      gid: 'axes_0.xlabel',
      prop: 'pos_frac',
      value: [(20 - f[0]) / f[2], (70 - f[1]) / f[3]],
    }
    const result = sourceFrameOverride(edit, authority, authority.inputIdentity)
    expect((result.value as number[])[0]).toBeCloseTo(0.2, 10)
    expect((result.value as number[])[1]).toBeCloseTo(0.7, 10)
    const color = { gid: 'axes_0.lines_0', prop: 'color', value: 'red' }
    expect(sourceFrameOverride(color, authority, authority.inputIdentity)).toBe(color)
  })

  it('original genuine savefig wrapper remains equivalent to shared geometry', () => {
    const before = panel()
    const frame: ManifestFrame = {
      source: 'savefig',
      active: false,
      figsize_mm: [100 * MM_PER_PT, 100 * MM_PER_PT],
      savefig_mm: [-21 * MM_PER_PT, -MM_PER_PT, 122 * MM_PER_PT, 109 * MM_PER_PT],
    }
    before.overrides.push({ gid: 'figure', prop: 'frame', value: 'figsize' })
    expect(frameSwitchPatch(before, frame)).toEqual(
      frameGeometryPatch(
        { ...before, overrides: before.overrides.slice(0, -1) },
        { basis_mm: frame.figsize_mm, frame_mm: frame.savefig_mm },
      ),
    )
  })

  it('single existing transaction draft keeps page by default and rejects stale input before mutation', () => {
    const doc: FigureDocument = {
      schema: 2,
      name: 'layout',
      page: { w: 140, h: 95 },
      guides: [{ axis: 'x', pos: 20 }],
      objects: [panel(), { ...panel(), id: 'other', x: 80 }],
    }
    const before = structuredClone(doc)
    expect(() => applyComputedFrameDraft(doc, 0, 'chart', authority, 'stale')).toThrow()
    expect(doc).toEqual(before)
    applyComputedFrameDraft(doc, 0, 'chart', authority, authority.inputIdentity)
    expect(doc.page).toEqual(before.page)
    expect(doc.objects[1]).toEqual(before.objects[1])
    expect(doc.objects[0].x).toBeLessThan(0) // a negative frame is not proof of painted page overflow
  })

  it('explicit page expansion is one draft mutation and translates guides/all objects together', () => {
    const a = panel()
    a.x = 5
    const doc: FigureDocument = {
      schema: 2,
      name: 'layout',
      page: { w: 140, h: 95 },
      guides: [
        { axis: 'x', pos: 20 },
        { axis: 'y', pos: 25 },
      ],
      objects: [a, { ...panel(), id: 'other', x: 80 }],
    }
    const result = applyComputedFrameDraft(doc, 0, 'chart', authority, authority.inputIdentity, {
      expandPage: true,
    })
    expect(result.dx).toBeCloseTo(6.55, 9)
    expect(doc.page.w).toBeCloseTo(146.55, 9)
    expect(doc.objects[1].x - 80).toBeCloseTo(result.dx, 9)
    expect(doc.guides[0].pos - 20).toBeCloseTo(result.dx, 9)
    expect(doc.guides[1].pos).toBe(25)
  })

  it('bad computed provenance has no frame authority', () => {
    expect(validComputedFrame({ ...authority.frame, source: 'savefig' })).toBe(false)
    expect(validComputedFrame({ ...authority.frame, extra: 1 })).toBe(false)
    expect(validComputedFrame({ ...authority.frame, rect_pt: [0, 0, 90, 90] })).toBe(false)
  })
})

afterEach(() => {
  resetPreview()
  document.body.innerHTML = ''
})

describe('actual DOM preview translation and scale use F lengths plus S anchor origin, cancel restores', () => {
  it('native render-frame marker drives F basis with S origin', () => {
    document.body.innerHTML =
      '<div data-element-svg="chart"><svg viewBox="-21 -1 122 109"><g data-tavotto-native-viewport-pt="100 100" data-tavotto-native-render-frame-pt="-21 -1 122 109"><g id="label"/></g></svg></div>'
    const svg = document.querySelector('svg')!
    const target = document.querySelector('#label')!
    expect(svgPreviewCoordinates(svg, target)).toEqual({
      space: 'native-pt',
      width: 122,
      height: 109,
      unitsPerPt: 1,
      originX: -21,
      originY: -1,
    })
    beginPreview({ panelId: 'chart', renderKey: 'same', sizeMm: [122 * MM_PER_PT, 109 * MM_PER_PT] })
    previewTransform('label', 0.1, 0.2)
    flushPreviewFrame()
    expect(target.getAttribute('transform')).toBe('translate(12.200000000000001,21.8)')
    previewScale('label', 2, (20 + 21) / 122, (70 + 1) / 109)
    flushPreviewFrame()
    // The original previewScale replaces translation; pivot stays at S=(20,70).
    expect(target.getAttribute('transform')).toBe('matrix(2,0,0,2,-20,-70)')
    cancelPreview()
    expect(target.getAttribute('transform')).toBeNull()
  })
})

describe('computed render frame 闭式判据（同源对）', () => {
  /** 与 `web/src/types/cropBounds.golden.test.ts` 同法：直接读同一份金向量；
   * 后端那一侧是 `tests/test_chart_frame_contract.py`，两侧不读对方源码。 */
  it('向量里该收的收、该拒的拒', () => {
    for (const vector of frameGolden.accept) {
      expect(validComputedFrame(vector.frame), vector.id).toBe(true)
    }
    for (const vector of frameGolden.reject) {
      expect(validComputedFrame(vector.frame), vector.id).toBe(false)
    }
  })

  it('非有限数 JSON 表达不了，单列', () => {
    const base = frameGolden.accept[0].frame as Record<string, unknown>
    expect(validComputedFrame({ ...base, rect_pt: [0, 0, Number.POSITIVE_INFINITY, 100] })).toBe(
      false,
    )
    expect(validComputedFrame({ ...base, rect_pt: [Number.NaN, 0, 100, 100] })).toBe(false)
  })
})
