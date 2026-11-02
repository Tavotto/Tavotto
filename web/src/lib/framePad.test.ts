import { describe, expect, it } from 'vitest'
import type { Manifest } from '@/lib/api'
import {
  effectiveFramePad,
  frameCanvasBox,
  inkInsetMm,
  isZeroPad,
  planFigureCorner,
  planPadEdge,
  ZERO_PAD,
} from './framePad'
import type { PageColumn } from './pageColumns'

const COLS: PageColumn[] = [
  { id: 'single', w: 85, label: { zh: '单栏', en: 'Single' } },
  { id: 'double', w: 174, label: { zh: '双栏', en: 'Double' } },
]
const manifestWithInk = (ink: number[] | undefined) =>
  ({ elements: [{ gid: 'figure', editable: [], ...(ink ? { ink_bbox: ink } : {}) }] }) as unknown as Manifest

describe('inkInsetMm', () => {
  it('把相对图幅的 ink_bbox 换成到四边的 mm', () => {
    const i = inkInsetMm(manifestWithInk([0.05, 0.1, 0.9, 0.95]), [80, 60])!
    expect(i.l).toBeCloseTo(4)
    expect(i.t).toBeCloseTo(6)
    expect(i.r).toBeCloseTo(8)
    expect(i.b).toBeCloseTo(3)
  })
  it('旧后端没有 ink_bbox → null（没有紧凑停靠点）', () => {
    expect(inkInsetMm(manifestWithInk(undefined), [80, 60])).toBeNull()
    expect(inkInsetMm(null, [80, 60])).toBeNull()
  })
  it('内容伸出图幅时距离夹到 0', () => {
    const i = inkInsetMm(manifestWithInk([-0.02, 0, 1.03, 1]), [100, 50])!
    expect(i.l).toBe(0)
    expect(i.r).toBe(0)
  })
})

describe('effectiveFramePad', () => {
  it('紧凑边换成当前内容边缘，其余边用存的 mm', () => {
    const p = effectiveFramePad(
      { framePad: { l: -1, t: 2, r: 3, b: 0 }, framePadTight: ['l'] },
      { l: 4.5, t: 0, r: 0, b: 0 },
    )
    expect(p).toEqual({ l: -4.5, t: 2, r: 3, b: 0 })
  })
  it('#1648 Codex 复核 P2：紧凑边换成 -inset 会把画布挤没时，抬到画布宽 5 mm', () => {
    const p = effectiveFramePad(
      { framePad: { l: -70, t: 0, r: -10, b: 0 }, framePadTight: ['r'] },
      { l: 0, t: 0, r: 10, b: 0 },
      [80, 60],
    )
    expect(p.r).toBe(-5)
    expect(80 + p.l + p.r).toBe(5)
    // 不传图幅尺寸时保持旧行为；非紧凑边不动
    expect(effectiveFramePad({ framePad: { l: -70, t: 0, r: 0, b: 0 }, framePadTight: ['r'] }, { l: 0, t: 0, r: 10, b: 0 }).r).toBe(-10)
    expect(p.l).toBe(-70)
  })
  it('没有内容边缘事实时紧凑边沿用存的 mm；缺省全 0', () => {
    expect(effectiveFramePad({ framePad: { l: -1, t: 0, r: 0, b: 0 }, framePadTight: ['l'] }, null).l).toBe(-1)
    expect(isZeroPad(effectiveFramePad({}, null))).toBe(true)
  })
})

describe('frameCanvasBox', () => {
  it('面板框四周扩 pad × k', () => {
    expect(frameCanvasBox({ x: 10, y: 20, w: 80, h: 60 }, { l: 2, t: 1, r: 3, b: 0 }, 1)).toEqual({
      x: 8,
      y: 19,
      w: 85,
      h: 61,
    })
  })
})

describe('planPadEdge', () => {
  const base = { pad0: ZERO_PAD, sizeMm: [80, 60] as const, columns: COLS, allowCrop: false }
  const inset = { l: 3, t: 2, r: 1.5, b: 2.5 }

  it('往里的下限算上对边已有的负留白：80 mm、左边已裁 -60，右边往里到 -60 → 夹到画布宽 5 mm', () => {
    const p = planPadEdge({
      ...base,
      sizeMm: [80, 60],
      pad0: { l: -60, t: 0, r: 0, b: 0 },
      edge: 'r',
      deltaOut: -60,
      inset: null,
      allowCrop: true,
    })
    expect(p.value).toBe(-15)
    expect(80 - 60 + p.value).toBe(5)
    // 竖向同理
    const q = planPadEdge({ ...base, pad0: { l: 0, t: 0, r: 0, b: -40 }, edge: 't', deltaOut: -90, inset: null, allowCrop: true })
    expect(60 - 40 + q.value).toBe(5)
  })

  it('#1648 Codex 复核 P2：夹取改了停靠值就不再算紧凑（80 mm、左裁 -70、右内容边距 10，右边拖到内容边缘）', () => {
    const p = planPadEdge({
      ...base,
      sizeMm: [80, 60],
      pad0: { l: -70, t: 0, r: 0, b: 0 },
      edge: 'r',
      deltaOut: -10,
      inset: { l: 4, t: 0, r: 10, b: 0 },
      allowCrop: false,
    })
    expect(p.value).toBe(-5)
    expect(p.tight).toBe(false)
    expect(p.state).toBe('trim')
  })

  it('往外拖 = 扩充画布', () => {
    const p = planPadEdge({ ...base, edge: 'r', deltaOut: 7.33, inset })
    expect(p.state).toBe('grow')
    expect(p.value).toBe(7.3)
  })
  it('画布宽吸附栏宽：图 80 + 右留白 5 = 85（单栏）', () => {
    const p = planPadEdge({ ...base, edge: 'r', deltaOut: 4.2, inset })
    expect(p.snap?.id).toBe('single')
    expect(p.value).toBeCloseTo(5)
  })
  it('往里先吸到图边、再停在内容边缘（紧凑），不按 ⌥ 越不过去', () => {
    expect(planPadEdge({ ...base, edge: 'l', deltaOut: -0.8, inset }).state).toBe('edge')
    const tight = planPadEdge({ ...base, edge: 'l', deltaOut: -2.4, inset })
    expect(tight.state).toBe('tight')
    expect(tight.value).toBe(-3)
    expect(tight.tight).toBe(true)
    const blocked = planPadEdge({ ...base, edge: 'l', deltaOut: -10, inset })
    expect(blocked.state).toBe('blocked')
    expect(blocked.value).toBe(-3)
  })
  it('按 ⌥ 越过内容边缘 = 裁剪；最多裁到图剩 5 mm', () => {
    const crop = planPadEdge({ ...base, edge: 'l', deltaOut: -10, inset, allowCrop: true })
    expect(crop.state).toBe('crop')
    expect(crop.value).toBe(-10)
    expect(planPadEdge({ ...base, edge: 'l', deltaOut: -500, inset, allowCrop: true }).value).toBe(-75)
  })
  it('没有内容边缘事实时往里不拦（收紧留白）', () => {
    const p = planPadEdge({ ...base, edge: 't', deltaOut: -6, inset: null })
    expect(p.state).toBe('trim')
    expect(p.value).toBe(-6)
  })
})

describe('planFigureCorner', () => {
  const box0 = { x: 0, y: 0, w: 84, h: 60 }
  const pad = { l: 2, t: 0, r: 2, b: 0 }
  const common = { box0, sizeMm: [80, 60] as const, pad, k: 1, columns: COLS, noSnap: false, free: false }

  it('拖右下角 = 等比改图幅，左上角不动，留白不变', () => {
    const p = planFigureCorner({ ...common, corner: 'br', dx: 40, dy: 0 })
    expect(p.sizeMm).toEqual([120, 90])
    expect(p.box).toEqual({ x: 0, y: 0, w: 124, h: 90 })
    expect(p.pos).toEqual({ x: 2, y: 0 })
  })
  it('拖左上角：右下角不动', () => {
    const p = planFigureCorner({ ...common, corner: 'tl', dx: 20, dy: 0 })
    expect(p.sizeMm).toEqual([60, 45])
    expect(p.box.x + p.box.w).toBe(84)
    expect(p.box.y + p.box.h).toBe(60)
  })
  it('吸附量的是导出宽（图幅 + 左右留白）', () => {
    const p = planFigureCorner({ ...common, corner: 'br', dx: 1.5, dy: 0 })
    expect(p.snap?.id).toBe('single')
    expect(p.sizeMm[0]).toBe(81)
    expect(planFigureCorner({ ...common, corner: 'br', dx: 1.5, dy: 0, noSnap: true }).snap).toBeNull()
  })
  it('两侧都往里裁过：图幅缩小不会让画布宽小于 5 mm（80 mm、左右各 -20，缩到 30 → 夹到 45）', () => {
    const cropped = { l: -20, t: 0, r: -20, b: 0 }
    const p = planFigureCorner({
      ...common,
      pad: cropped,
      box0: { x: 0, y: 0, w: 40, h: 60 },
      corner: 'br',
      dx: -50,
      dy: 0,
      noSnap: true,
    })
    expect(p.sizeMm[0]).toBe(45)
    expect(p.box.w).toBeCloseTo(5)
    expect(p.box.w).toBeGreaterThan(0)
    // 等比：高跟着宽走
    expect(p.sizeMm[1]).toBeCloseTo((60 * 45) / 80, 1)
  })
  it('高被上下留白顶住时（等比）宽跟着抬，取更严的那个', () => {
    const p = planFigureCorner({
      ...common,
      pad: { l: 0, t: -20, r: 0, b: -20 },
      box0: { x: 0, y: 0, w: 80, h: 20 },
      corner: 'br',
      dx: -70,
      dy: 0,
      noSnap: true,
    })
    // 画布高 = h - 40 ≥ 5 → h ≥ 45 → w ≥ 60
    expect(p.sizeMm[1]).toBeGreaterThanOrEqual(45 - 1e-6)
    expect(p.sizeMm[0]).toBeGreaterThanOrEqual(60 - 0.02)
    expect(p.box.h).toBeGreaterThanOrEqual(5 - 0.05)
  })
  it('⇧ 自由比例：宽高各自跟指针', () => {
    const p = planFigureCorner({ ...common, corner: 'br', dx: 10, dy: -20, free: true, noSnap: true })
    expect(p.sizeMm).toEqual([90, 40])
  })
})
