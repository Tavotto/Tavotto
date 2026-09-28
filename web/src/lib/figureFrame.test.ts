/**
 * ADR 0098 §三（用户 2026-09-26 选 C）：升级前的面板迁移与「改用脚本保存时的图幅」的换算。
 *
 * 换算的判据是**内容在页面上不动**：同一个图内的点（figsize 里的 mm）在切换前后落在页面上
 * 同一处——旋转、翻转、裁剪、非等比缩放各走一遍。
 */
import { describe, expect, it } from 'vitest'
import type { Manifest } from '@/lib/api'
import {
  FIGURE_FRAME_VERSION,
  LEGACY_FRAME_OVERRIDE,
  frameSwitchAvailable,
  frameSwitchPatch,
  hasLegacyFrame,
  migrateFigureFrames,
  type ManifestFrame,
} from '@/lib/figureFrame'
import {
  migrateToProject,
  panelFullSize,
  panelRotation,
  rotateVec,
  type CanvasObject,
  type PanelObject,
} from '@/types/document'

const FRAME: ManifestFrame = {
  source: 'savefig',
  active: false,
  figsize_mm: [80, 57.6],
  // 左边伸出 1.5 mm、上边多裁掉 2.1 mm、右边与下边各有增减
  savefig_mm: [-1.5, 2.1, 76.0, 58.0],
}

function panel(extra: Partial<PanelObject> = {}): PanelObject {
  return {
    id: 'p1',
    type: 'panel',
    fileId: 'Fig1.pdf',
    fileKind: 'pdf',
    nativeW: 80,
    nativeH: 57.6,
    x: 30,
    y: 20,
    w: 60,
    h: 43.2,
    script: 'fig1.py',
    overrides: [{ gid: 'axes_0', prop: 'facecolor', value: '#eeeeee' }, { ...LEGACY_FRAME_OVERRIDE }],
    figureFrame: FIGURE_FRAME_VERSION,
    ...extra,
  }
}

/**
 * 图内一点（figsize 里的 mm，top-origin）在页面上的位置。`native` 是这张面板的原生图幅在
 * figsize 里的那一块（切换前是 figsize 本身，之后是脚本的图幅）。
 */
function pageOf(p: PanelObject, native: [number, number, number, number], u: number, v: number) {
  const [nx, ny, nw, nh] = native
  const crop = p.crop ?? { x: 0, y: 0, w: 1, h: 1 }
  const full = panelFullSize(p)
  // 可见块中心为原点的内容坐标
  let dx = ((u - nx) / nw - (crop.x + crop.w / 2)) * full.w
  let dy = ((v - ny) / nh - (crop.y + crop.h / 2)) * full.h
  if (p.flipH) dx = -dx
  if (p.flipV) dy = -dy
  const [px, py] = rotateVec(dx, dy, panelRotation(p))
  return [p.x + p.w / 2 + px, p.y + p.h / 2 + py]
}

const POINTS: [number, number][] = [
  [10, 10],
  [40, 30],
  [70, 50],
]

function expectContentFixed(before: PanelObject) {
  const patch = frameSwitchPatch(before, FRAME)
  const after: PanelObject = { ...before, ...patch }
  const [sx, sy, fw, fh] = FRAME.savefig_mm
  for (const [u, v] of POINTS) {
    const a = pageOf(before, [0, 0, 80, 57.6], u, v)
    const b = pageOf(after, [sx, sy, fw, fh], u, v)
    expect(b[0]).toBeCloseTo(a[0], 6)
    expect(b[1]).toBeCloseTo(a[1], 6)
  }
  return after
}

describe('迁移：升级前的面板', () => {
  const legacy = (extra: Partial<PanelObject>) => {
    const p = panel(extra)
    delete p.figureFrame
    return p
  }

  it('有图内修改的 PDF 面板补一条 figsize、打上记号', () => {
    const [out] = migrateFigureFrames([legacy({ overrides: [{ gid: 'axes_0', prop: 'facecolor', value: '#eee' }] })]) as PanelObject[]
    expect(out.figureFrame).toBe(FIGURE_FRAME_VERSION)
    expect(out.overrides.at(-1)).toEqual(LEGACY_FRAME_OVERRIDE)
  })

  it('没有图内修改的 PDF 面板画的是磁盘原件：只打记号，不补（补了反而把它换成引擎的 figsize 图）', () => {
    const [out] = migrateFigureFrames([legacy({ overrides: [] })]) as PanelObject[]
    expect(out.figureFrame).toBe(FIGURE_FRAME_VERSION)
    expect(out.overrides).toEqual([])
  })

  it('runtime 面板的样子一向来自引擎：补', () => {
    const [out] = migrateFigureFrames([legacy({ fileKind: 'runtime', overrides: [] })]) as PanelObject[]
    expect(hasLegacyFrame(out)).toBe(true)
  })

  it('位图面板与带记号的面板不动；没有要改的返回原数组', () => {
    const objects: CanvasObject[] = [legacy({ fileKind: 'raster', overrides: [] })]
    const out = migrateFigureFrames(objects)
    expect((out[0] as PanelObject).overrides).toEqual([])
    const marked: CanvasObject[] = [panel({ overrides: [{ gid: 'a', prop: 'b', value: 1 }] })]
    expect(migrateFigureFrames(marked)).toBe(marked)
  })

  it('读档入口（schema 2 / 3）都迁；迁过的再读一次不再补', () => {
    const p = legacy({ overrides: [{ gid: 'axes_0', prop: 'facecolor', value: '#eee' }] })
    const pd = migrateToProject({ schema: 2, name: 'x', page: { w: 100, h: 100 }, objects: [p], guides: [] })!
    const once = pd.canvases[0].objects[0] as PanelObject
    expect(once.overrides.filter((o) => o.prop === 'frame')).toHaveLength(1)
    const again = migrateToProject(JSON.parse(JSON.stringify(pd)))!
    expect((again.canvases[0].objects[0] as PanelObject).overrides.filter((o) => o.prop === 'frame')).toHaveLength(1)
  })
})

describe('切换：内容在页面上不动、外框变', () => {
  it('普通面板', () => {
    const after = expectContentFixed(panel())
    expect(after.nativeW).toBe(76)
    expect(after.nativeH).toBe(58)
    expect(after.w / after.nativeW).toBeCloseTo(60 / 80, 9)
    expect(after.overrides).toEqual([{ gid: 'axes_0', prop: 'facecolor', value: '#eeeeee' }])
    expect(after.crop).toBeUndefined()
  })

  it('旋转 90° / 翻转 / 非等比', () => {
    expectContentFixed(panel({ rotation: 90, w: 43.2, h: 60 }))
    expectContentFixed(panel({ flipH: true, flipV: true }))
    expectContentFixed(panel({ w: 70, h: 43.2, aspectLocked: false }))
    expectContentFixed(panel({ rotation: 270, flipH: true, w: 43.2, h: 60 }))
  })

  it('裁剪过的面板：可见范围不变（与新图幅的交集），外框只按交集收', () => {
    const crop = { x: 0.1, y: 0.2, w: 0.5, h: 0.5 }
    const after = expectContentFixed(panel({ crop, w: 30, h: 21.6 }))
    expect(after.w).toBeCloseTo(30, 9)
    expect(after.crop).toBeDefined()
  })

  it('分数类 override 换到新图幅里，图内的元素留在原处', () => {
    const p = panel({
      overrides: [
        { gid: 'axes_0.title', prop: 'pos_frac', value: [0.5, 0.05] },
        { gid: 'axes_0', prop: 'position', value: [0.2, 0.2, 0.7, 0.6] },
        { gid: 'arrow_0', prop: 'endpoints_frac', value: [0.1, 0.1, 0.9, 0.9] },
        { ...LEGACY_FRAME_OVERRIDE },
      ],
    })
    const [sx, sy, fw, fh] = FRAME.savefig_mm
    const [gw, gh] = FRAME.figsize_mm
    const out = frameSwitchPatch(p, FRAME).overrides
    const title = out[0].value as number[]
    expect(title[0] * fw + sx).toBeCloseTo(0.5 * gw, 9)
    expect(title[1] * fh + sy).toBeCloseTo(0.05 * gh, 9)
    const pos = out[1].value as number[]
    // bottom-origin：图幅底边在 figsize 里（mm，自下而上）= gh − sy − fh
    expect(pos[0] * fw + sx).toBeCloseTo(0.2 * gw, 9)
    expect(pos[1] * fh + (gh - sy - fh)).toBeCloseTo(0.2 * gh, 9)
    expect(pos[2] * fw).toBeCloseTo(0.7 * gw, 9)
    const ends = out[2].value as number[]
    expect(ends[2] * fw + sx).toBeCloseTo(0.9 * gw, 9)
    expect(out.some((o) => o.prop === 'frame')).toBe(false)
  })

  it('改过图幅的面板：那条 size_mm 换成同一个 figsize 对应的图幅尺寸', () => {
    const p = panel({ overrides: [{ gid: 'figure', prop: 'size_mm', value: [80, 57.6] }, { ...LEGACY_FRAME_OVERRIDE }] })
    expect(frameSwitchPatch(p, FRAME).overrides[0].value).toEqual([76, 58])
  })
})

describe('什么时候给切换', () => {
  const man = (frame?: ManifestFrame): Manifest => ({ stem: 'Fig1', size_mm: [80, 57.6], elements: [], frame })
  it('只有带着 figsize、且引擎说有另一个图幅、且没生效时', () => {
    expect(frameSwitchAvailable(panel(), man(FRAME))).toBe(FRAME)
    expect(frameSwitchAvailable(panel(), man())).toBeNull()
    expect(frameSwitchAvailable(panel(), man({ ...FRAME, active: true }))).toBeNull()
    expect(frameSwitchAvailable(panel({ overrides: [] }), man(FRAME))).toBeNull()
    expect(frameSwitchAvailable(panel(), null)).toBeNull()
  })
})
