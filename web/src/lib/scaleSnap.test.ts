/**
 * 等比缩放吸附的纯函数（ADR 0128 §三）：给定固定点、锚点、候选、容差、hold，挑出的 s 与那一对。
 */
import { describe, expect, it } from 'vitest'

import type { Manifest } from './api'
import type { CanvasObject, PanelObject } from '@/types/document'
import type { SnapLine } from './geometry'
import {
  createScaleHold,
  scaleAnchorsFor,
  scaleSnapCandidates,
  frameAnchors,
  scaleFixedPoint,
  scaledRect,
  snapScaleNearest,
  type ScaleAnchors,
} from './scaleSnap'

const line = (v: number, kind: SnapLine['kind'] = 'frame'): SnapLine => ({ v, kind, edge: null })
const ORIG = { x: 10, y: 10, w: 40, h: 20 }
const size = { w: 40, h: 20 }

// 东南角柄：固定点 = 左上角 (10, 10)，右边 x=50、下边 y=30 随 s 动
const anchorsOf = (extraX: ScaleAnchors['xs'] = [], extraY: ScaleAnchors['ys'] = []): ScaleAnchors => {
  const [xs, ys] = frameAnchors(ORIG)
  return { xs: [...xs, ...extraX], ys: [...ys, ...extraY] }
}
const base = { fixed: { x: 10, y: 10 }, size, tol: 1 }

describe('snapScaleNearest', () => {
  it('外框右边吸到别的对象的右边：s = (c − F) / (a − F)', () => {
    const hit = snapScaleNearest({
      ...base,
      s0: 1.49, // 右边此刻在 10 + 40·1.49 = 69.6
      anchors: anchorsOf(),
      cands: { xs: [line(70)], ys: [] },
    })
    expect(hit?.s).toBeCloseTo(1.5, 9) // (70 − 10) / (50 − 10)
    expect(hit?.axis).toBe('x')
    expect(hit?.anchor.frame).toBe(true)
  })

  it('子图右边吸（外框右边不对齐）：内容锚点解出的 s', () => {
    // 子图右边在页面 x = 40，距固定点 30
    const hit = snapScaleNearest({
      ...base,
      s0: 1.98,
      anchors: anchorsOf([{ v: 40, frame: false }]),
      cands: { xs: [line(70, 'axes')], ys: [] },
    })
    expect(hit?.s).toBeCloseTo(2, 9) // (70 − 10) / (40 − 10)
    expect(hit?.anchor.frame).toBe(false)
  })

  it('档位优先：外框↔内容混搭让位于更远的同类', () => {
    // 外框右边此刻 69.6：到内容线 69.9 距 0.3（混搭，第二档）；内容锚点 40 此刻 69.4…取同类
    const hit = snapScaleNearest({
      ...base,
      s0: 1.49,
      anchors: anchorsOf([{ v: 40, frame: false }]),
      cands: { xs: [line(69.9, 'axes'), line(70.4, 'frame')], ys: [] },
    })
    // 外框右边(69.6)→frame 线 70.4 距 0.8 同档 0；→axes 线 69.9 混搭
    expect(hit?.line.kind).toBe('frame')
    expect(hit?.anchor.frame).toBe(true)
  })

  it('页面 / 参考线 / 网格对内容锚点也是第一档', () => {
    const hit = snapScaleNearest({
      ...base,
      s0: 1.98,
      anchors: anchorsOf([{ v: 40, frame: false }]),
      cands: { xs: [line(69.5, 'frame'), line(70.3, 'guide')], ys: [] },
    })
    expect(hit?.line.kind).toBe('guide')
  })

  it('⇧ 等比边柄：东边柄只有 x 向锚点参与也能吸，y 向锚点同样按同一个 s', () => {
    // e 柄：固定点仍是左上角；下边 y=30 的候选线 y=50 → s = 2
    const hit = snapScaleNearest({
      ...base,
      s0: 1.98,
      anchors: anchorsOf(),
      cands: { xs: [], ys: [line(50, 'page')] },
    })
    expect(hit?.s).toBeCloseTo(2, 9) // (50 − 10) / (30 − 10)
    expect(hit?.axis).toBe('y')
  })

  it('不动的边不算锚点：左边与固定点重合，永远解不出 s', () => {
    const hit = snapScaleNearest({
      ...base,
      s0: 1,
      anchors: anchorsOf(),
      cands: { xs: [line(10.2)], ys: [] }, // 离左边 0.2，但左边不动
    })
    expect(hit).toBeNull()
  })

  it('s 太小不吸：解出的宽低于最小尺寸', () => {
    const hit = snapScaleNearest({
      ...base,
      s0: 0.11, // 右边此刻 14.4
      anchors: anchorsOf(),
      cands: { xs: [line(14, 'page')], ys: [] }, // s = 4/40 = 0.1 → w = 4、h = 2，恰在下限
    })
    expect(hit?.s).toBeCloseTo(0.1, 9)
    const tooSmall = snapScaleNearest({
      ...base,
      s0: 0.03,
      anchors: anchorsOf(),
      cands: { xs: [line(11, 'page')], ys: [] }, // 右边 s=0.025、中线 s=0.05，宽都 < 3
    })
    expect(tooSmall).toBeNull()
  })

  it('容差外不吸；候选在固定点另一侧（s < 0）不吸', () => {
    expect(
      snapScaleNearest({ ...base, s0: 1, anchors: anchorsOf(), cands: { xs: [line(52, 'page')], ys: [] } }),
    ).toBeNull()
    expect(
      snapScaleNearest({ ...base, s0: 1, anchors: anchorsOf(), cands: { xs: [line(9.5, 'page')], ys: [] } }),
    ).toBeNull()
  })

  it('吸住后保持：同一对距离 < 2×容差继续吸，超过放开', () => {
    const hold = createScaleHold()
    const cands = { xs: [line(70, 'frame'), line(71.5, 'frame')], ys: [] }
    const common = { ...base, anchors: anchorsOf(), cands, hold }
    expect(snapScaleNearest({ ...common, s0: 1.49 })?.line.v).toBe(70) // 右边 69.6 → 70
    // 拖到 71.2（离 70 有 1.2 < 2·tol；离 71.5 只有 0.3）：仍保持 70
    expect(snapScaleNearest({ ...common, s0: (71.2 - 10) / 40 })?.line.v).toBe(70)
    // 拖到 72.2（离 70 有 2.2 ≥ 2）：放开，改挑 71.5
    expect(snapScaleNearest({ ...common, s0: (72.2 - 10) / 40 })?.line.v).toBe(71.5)
  })

  it('没有 hold 时同样的位置按最近挑（对照上一条）', () => {
    const cands = { xs: [line(70, 'frame'), line(71.5, 'frame')], ys: [] }
    const hit = snapScaleNearest({ ...base, s0: (71.2 - 10) / 40, anchors: anchorsOf(), cands })
    expect(hit?.line.v).toBe(71.5)
  })
})

describe('scaleFixedPoint / scaledRect', () => {
  it('固定点与 resizeRect 的落位一致', () => {
    expect(scaleFixedPoint(ORIG, 'se')).toEqual({ x: 10, y: 10 })
    expect(scaleFixedPoint(ORIG, 'nw')).toEqual({ x: 50, y: 30 })
    expect(scaleFixedPoint(ORIG, 'e')).toEqual({ x: 10, y: 10 })
    expect(scaleFixedPoint(ORIG, 'n')).toEqual({ x: 10, y: 30 })
  })
  it('西北柄缩放后右下角不动', () => {
    const r = scaledRect(ORIG, 'nw', 2)
    expect(r).toEqual({ x: -30, y: -10, w: 80, h: 40 })
    expect(r.x + r.w).toBe(50)
    expect(r.y + r.h).toBe(30)
  })
})

describe('裁剪窗外的子图线不算', () => {
  // 完整内容 100 宽、只露出中间 50（crop.x=0.3,w=0.5）：面板 x∈[0,50]，完整内容 x∈[−30,70]
  const cropped = {
    id: 'p', type: 'panel', x: 0, y: 0, w: 50, h: 75, nativeW: 100, nativeH: 75,
    fileId: 'f', overrides: [], crop: { x: 0.3, y: 0, w: 0.5, h: 1 },
  } as unknown as PanelObject
  const manifest: Manifest = {
    stem: 'F', size_mm: [100, 75],
    elements: [
      { gid: 'axes_0', role: 'axes', label: '', bbox: [0.1, 0.2, 0.5, 0.6], draggable: false, resizable: true,
        editable: [{ prop: 'position', type: 'rect', value: [0.1, 0.2, 0.5, 0.6] }] },
    ],
  } as unknown as Manifest

  it('锚点：子图左边 −20 在窗外，中线 5 与右边 30 在窗内', () => {
    const a = scaleAnchorsFor(cropped, manifest)
    expect(a.xs.filter((x) => !x.frame).map((x) => x.v)).toEqual([5, 30])
  })

  it('候选：别的面板子图左边 −20 在窗外不出线，中线 5 与右边 30 出线', () => {
    const c = scaleSnapCandidates({
      objects: [cropped], exclude: new Set(), page: { w: 150, h: 100 }, guides: [],
      prefs: { objects: true, guides: false, grid: false, gridSize: 10 },
      manifestOf: () => manifest,
    })
    const axes = c.xs.filter((l) => l.kind === 'axes').map((l) => l.v)
    expect(axes).toEqual([5, 30]) // 中线 5 与右边 30；左边 −20 在窗外
  })
})

describe('子图 / 色条的中线以内容身份出线（Codex #1033 r4268157388）', () => {
  const axesManifest = (): Manifest =>
    ({
      stem: 'F', size_mm: [100, 75],
      elements: [
        { gid: 'axes_0', role: 'axes', label: '', bbox: [0.2, 0.2, 0.5, 0.6], draggable: false, resizable: true,
          editable: [{ prop: 'position', type: 'rect', value: [0.2, 0.2, 0.5, 0.6] }] },
      ],
    }) as unknown as Manifest
  const panelAt = (id: string, x: number) =>
    ({ id, type: 'panel', x, y: 0, w: 100, h: 75, nativeW: 100, nativeH: 75, fileId: id, overrides: [] }) as unknown as PanelObject
  const T = panelAt('t', 0)
  const P2 = panelAt('p2', 100)
  // 外框右边 321.8 的对象：与 T 外框右边（此刻 321.1）是外框↔外框，距 0.7，比子图中线（0.5）更远
  const shape = { id: 's', type: 'shape', shape: 'rect', x: 221.8, y: 0, w: 100, h: 75 } as unknown as CanvasObject
  const prefs = { objects: true, guides: false, grid: false, gridSize: 10 }

  const pick = () => {
    const cands = scaleSnapCandidates({
      objects: [T, P2, shape], exclude: new Set(['t']), page: { w: 150, h: 100 }, guides: [], prefs,
      manifestOf: () => axesManifest(),
    })
    return snapScaleNearest({
      fixed: { x: 0, y: 0 }, size: { w: 100, h: 75 }, s0: 3.2111,
      anchors: scaleAnchorsFor(T, axesManifest()), cands, tol: 1.2,
    })
  }

  it('子图中线↔子图中线（同档、距 0.5）胜过更远的外框↔外框（距 0.7）', () => {
    const hit = pick()
    expect(hit?.anchor.frame).toBe(false)
    expect(hit?.line.kind).toBe('axes')
    expect(hit?.line.edge).toBe(1)
    expect(hit?.line.v).toBeCloseTo(145, 9)
  })

  it('「吸附到对象」关掉：自身内容锚点不出，只剩外框', () => {
    const a = scaleAnchorsFor(T, axesManifest(), false)
    expect(a.xs.every((x) => x.frame)).toBe(true)
    expect(a.ys.every((x) => x.frame)).toBe(true)
  })
})

describe('Codex #1033 第二轮', () => {
  const prefs = { objects: true, guides: false, grid: false, gridSize: 10 }
  const page = { w: 150, h: 100 }

  it('旋转 90° 的形状：外框线取旋转后的外接盒（10 / 20 / 30），不是未旋转的 0 / 20 / 40', () => {
    const shape = {
      id: 's', type: 'shape', shape: 'rect', x: 0, y: 0, w: 40, h: 20, rotationDeg: 90,
    } as unknown as CanvasObject
    const c = scaleSnapCandidates({
      objects: [shape], exclude: new Set(), page, guides: [], prefs, manifestOf: () => null,
    })
    const near = (got: number[], want: number[]) => {
      expect(got).toHaveLength(want.length)
      got.forEach((v, i) => expect(v).toBeCloseTo(want[i], 9))
    }
    near(c.xs.filter((l) => l.kind === 'frame').map((l) => l.v), [10, 20, 30])
    near(c.ys.filter((l) => l.kind === 'frame').map((l) => l.v), [-10, 10, 30])
  })

  // 完整内容 100×75，只露出下半（crop.y=0.5,h=0.5）：窗口 y∈[0,37.5]，完整内容 y∈[−37.5,37.5]
  const lowerHalf = {
    id: 'p', type: 'panel', x: 0, y: 0, w: 100, h: 37.5, nativeW: 100, nativeH: 75,
    fileId: 'f', overrides: [], crop: { x: 0, y: 0.5, w: 1, h: 0.5 },
  } as unknown as PanelObject
  // 子图 top-origin y∈[0.1,0.3] → 页面 y∈[−33.75,−26.25]：整个在窗口上方；x∈[10,60] 在窗内范围
  const above: Manifest = {
    stem: 'F', size_mm: [100, 75],
    elements: [
      { gid: 'axes_0', role: 'axes', label: '', bbox: [0.1, 0.1, 0.5, 0.2], draggable: false, resizable: true,
        editable: [{ prop: 'position', type: 'rect', value: [0.1, 0.7, 0.5, 0.2] }] },
    ],
  } as unknown as Manifest

  it('与可见窗没有二维交集的子图：锚点不出线（x 落在面板范围内也不出）', () => {
    const a = scaleAnchorsFor(lowerHalf, above)
    expect(a.xs.filter((x) => !x.frame)).toEqual([])
    expect(a.ys.filter((x) => !x.frame)).toEqual([])
  })

  it('与可见窗没有二维交集的子图：候选不出线', () => {
    const c = scaleSnapCandidates({
      objects: [lowerHalf], exclude: new Set(), page, guides: [], prefs, manifestOf: () => above,
    })
    expect(c.xs.filter((l) => l.kind === 'axes')).toEqual([])
    expect(c.ys.filter((l) => l.kind === 'axes')).toEqual([])
  })
})
