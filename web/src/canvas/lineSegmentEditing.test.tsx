/**
 * 引线逐段 / 逐点拖（ADR 0100 修订 2026-11-19；用户：「拖动某一段，当成自由线段一样拖」）。
 *
 * 引擎给位置不是数据的线出 `line_points`（每个顶点的 figure 分数）。画布这边：
 * - 按下拖的是离指针最近的那一段：那一段两个端点同走一个位移，相邻段跟着伸缩，其余顶点不动；
 * - 选中后每个顶点一个方块手柄，拖手柄只动那一个点；
 * - shift 锁水平 / 垂直（直角引线拖竖段只左右挪）；
 * - 写的都是 `points_frac`（全部顶点）；整条平移（方向键 / 多选整组 / 对齐）也写它，不写 pos_frac；
 * - 拖回原处不写；预览是覆盖层的虚线折线，落点 == 预览。
 */
import { literal } from '@/i18n'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import type { Manifest, ManifestElement } from '@/lib/api'
import { alignEntries, axesCompanions, linePointsOf } from '@/lib/elementGeom'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useRenderStore } from '@/store/renderStore'
import { seedExactRender } from '@/test/renderFixtures'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { OverlaySvg } from './OverlaySvg'
import { inFigureMoveOf, nearestLineSegment, startLineEdit } from './interactions'

const figureEl: ManifestElement = {
  gid: 'figure',
  role: 'figure',
  label: '整图',
  bbox: [0, 0, 1, 1],
  editable: [],
  draggable: false,
}

const axesEl: ManifestElement = {
  gid: 'axes_0',
  role: 'axes',
  label: '子图',
  bbox: [0.1, 0.1, 0.8, 0.8],
  editable: [],
  draggable: false,
  resizable: true,
}

/** 直角引线：震中 (0.2,0.7) → 拐点 (0.5,0.7) → 拐点 (0.5,0.2) → 标签 (0.7,0.2)（top-origin） */
const LEAD: [number, number][] = [
  [0.2, 0.7],
  [0.5, 0.7],
  [0.5, 0.2],
  [0.7, 0.2],
]
const leadEl: ManifestElement = {
  gid: 'axes_0.lines_1',
  role: 'line',
  label: '曲线 2',
  bbox: [0.2, 0.2, 0.5, 0.5],
  editable: [],
  draggable: true,
  anchor: [0.2, 0.7],
  drag_prop: 'pos_frac',
  line_points: LEAD,
}

const manifest: Manifest = {
  stem: 'Fig1',
  size_mm: [100, 80],
  elements: [figureEl, axesEl, leadEl],
}

const panel = (): PanelObject => ({
  id: 'p1',
  type: 'panel',
  x: 10,
  y: 20,
  w: 100,
  h: 80,
  fileId: 'f1',
  fileKind: 'pdf',
  nativeW: 100,
  nativeH: 80,
  script: 'fig.py',
  overrides: [],
})

const layout = { width: mmToWorld(100), height: mmToWorld(80) }
const down = (clientX = 0, clientY = 0) =>
  ({ clientX, clientY, button: 0, stopPropagation() {} }) as unknown as React.PointerEvent
const fire = (
  type: 'pointermove' | 'pointerup',
  clientX: number,
  clientY: number,
  init: MouseEventInit = {},
) => window.dispatchEvent(new MouseEvent(type, { clientX, clientY, bubbles: true, ...init }))
const px = (mm: number) => mmToWorld(mm)
const livePanel = () => useDocumentStore.getState().doc.objects[0] as PanelObject
const overrideOf = (gid: string, prop: string) =>
  livePanel().overrides.find((o) => o.gid === gid && o.prop === prop)?.value as number[] | undefined
const pairs = (v: number[]) => v.reduce<number[][]>((out, n, i) => (i % 2 ? (out[out.length - 1].push(n), out) : [...out, [n]]), [])

let root: Root
let container: HTMLDivElement

beforeEach(async () => {
  localStorage.clear()
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700 })
  useUiStore.setState({ tool: 'select', snapEnabled: false, elementPanelId: null, cropTargetId: null, selectedGids: [] })
  useSelectionStore.getState().clear()
  useRenderStore.setState({ render: async () => {} })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_line_seg')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(panel())
  })
  seedExactRender(panel(), manifest)
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  useInteractionStore.getState().end()
  act(() => root.unmount())
  container.remove()
})

describe('拖一段：两端同走、相邻段伸缩', () => {
  it('拖竖段（第 1 段）往右 10 mm：顶点 1、2 的 x 都 +0.1，首尾不动', () => {
    startLineEdit(down(0, 0), livePanel(), leadEl, layout, { kind: 'segment', index: 1 })
    fire('pointermove', px(10), 0)
    fire('pointerup', px(10), 0)
    const v = pairs(overrideOf(leadEl.gid, 'points_frac')!)
    expect(v[0]).toEqual([0.2, 0.7])
    expect(v[1][0]).toBeCloseTo(0.6, 6)
    expect(v[1][1]).toBeCloseTo(0.7, 6)
    expect(v[2][0]).toBeCloseTo(0.6, 6)
    expect(v[2][1]).toBeCloseTo(0.2, 6)
    expect(v[3]).toEqual([0.7, 0.2])
    expect(overrideOf(leadEl.gid, 'pos_frac')).toBeUndefined()
  })

  it('shift：斜着拖竖段，只留水平分量——直角引线仍横平竖直', () => {
    startLineEdit(down(0, 0), livePanel(), leadEl, layout, { kind: 'segment', index: 1 })
    fire('pointermove', px(10), px(3), { shiftKey: true })
    fire('pointerup', px(10), px(3), { shiftKey: true })
    const v = pairs(overrideOf(leadEl.gid, 'points_frac')!)
    expect(v[1][1]).toBe(0.7)
    expect(v[2][1]).toBe(0.2)
    expect(v[1][0]).toBeCloseTo(0.6, 6)
  })

  it('预览是覆盖层的折线，落点 == 预览；拖回原处不写', () => {
    startLineEdit(down(0, 0), livePanel(), leadEl, layout, { kind: 'segment', index: 0 })
    fire('pointermove', 0, px(8))
    const preview = useInteractionStore.getState().linePreview
    expect(preview?.gid).toBe(leadEl.gid)
    expect(preview?.pts[0][1]).toBeCloseTo(0.8, 6)
    fire('pointerup', 0, px(8))
    expect(pairs(overrideOf(leadEl.gid, 'points_frac')!)[0][1]).toBeCloseTo(0.8, 6)

    const before = livePanel().overrides.length
    startLineEdit(down(0, 0), livePanel(), leadEl, layout, { kind: 'segment', index: 2 })
    fire('pointermove', px(5), 0)
    fire('pointermove', 0, 0)
    fire('pointerup', 0, 0)
    expect(livePanel().overrides.length).toBe(before)
  })

  it('第二次拖从文档里写下的顶点起算（linePointsOf 优先取 override）', () => {
    startLineEdit(down(0, 0), livePanel(), leadEl, layout, { kind: 'segment', index: 1 })
    fire('pointermove', px(10), 0)
    fire('pointerup', px(10), 0)
    expect(linePointsOf(livePanel(), leadEl)![1][0]).toBeCloseTo(0.6, 6)
    startLineEdit(down(0, 0), livePanel(), leadEl, layout, { kind: 'segment', index: 1 })
    fire('pointermove', px(10), 0)
    fire('pointerup', px(10), 0)
    expect(pairs(overrideOf(leadEl.gid, 'points_frac')!)[1][0]).toBeCloseTo(0.7, 6)
  })
})

describe('拖一个顶点', () => {
  it('只动那一个点', () => {
    startLineEdit(down(0, 0), livePanel(), leadEl, layout, { kind: 'vertex', index: 3 })
    fire('pointermove', px(5), px(-4))
    fire('pointerup', px(5), px(-4))
    const v = pairs(overrideOf(leadEl.gid, 'points_frac')!)
    expect(v.slice(0, 3)).toEqual(LEAD.slice(0, 3))
    expect(v[3][0]).toBeCloseTo(0.75, 6)
    expect(v[3][1]).toBeCloseTo(0.15, 6)
  })
})

describe('整条平移也写 points_frac', () => {
  it('方向键 / 单选拖动的分派（inFigureMoveOf）：所有顶点同一位移，不写 pos_frac', () => {
    const mv = inFigureMoveOf(livePanel(), manifest, leadEl)!
    expect(mv.commit(0.05, -0.02, false)).toBe(true)
    const v = pairs(overrideOf(leadEl.gid, 'points_frac')!)
    v.forEach((p, i) => {
      expect(p[0]).toBeCloseTo(LEAD[i][0] + 0.05, 6)
      expect(p[1]).toBeCloseTo(LEAD[i][1] - 0.02, 6)
    })
    expect(overrideOf(leadEl.gid, 'pos_frac')).toBeUndefined()
  })

  it('多选整组 / 对齐（alignEntries）：写 points_frac，按框的位移平移全部顶点', () => {
    const [entry] = alignEntries(livePanel(), manifest, [leadEl.gid])
    const patch = entry.write([entry.box[0] + 0.1, entry.box[1], entry.box[2], entry.box[3]])
    expect(patch.prop).toBe('points_frac')
    const v = pairs(patch.value as number[])
    v.forEach((p, i) => expect(p[0]).toBeCloseTo(LEAD[i][0] + 0.1, 6))
  })

  it('改过的引线随宿主子图平移（axesCompanions 认 points_frac）', () => {
    startLineEdit(down(0, 0), livePanel(), leadEl, layout, { kind: 'segment', index: 1 })
    fire('pointermove', px(10), 0)
    fire('pointerup', px(10), 0)
    const comp = axesCompanions(livePanel(), manifest, 'axes_0').find((c) => c.gid === leadEl.gid)
    expect(comp).toBeTruthy()
    const shifted = pairs(comp!.shift(0.01, 0.02).value as number[])
    expect(shifted[0][0]).toBeCloseTo(0.21, 6)
    expect(shifted[0][1]).toBeCloseTo(0.72, 6)
  })
})

describe('按下分派到最近的段', () => {
  it('按内容像素量最近：竖段中部 → 第 1 段，末段 → 第 2 段', () => {
    expect(nearestLineSegment(LEAD, 0.51, 0.45, layout)).toBe(1)
    expect(nearestLineSegment(LEAD, 0.65, 0.21, layout)).toBe(2)
    expect(nearestLineSegment(LEAD, 0.3, 0.69, layout)).toBe(0)
  })
})

describe('覆盖层：选中引线出每个顶点的方块手柄', () => {
  it('四个顶点四个手柄；没选中不出', () => {
    act(() => root.render(<OverlaySvg />))
    expect(container.querySelectorAll('[data-line-vertex]').length).toBe(0)
    act(() => {
      useUiStore.setState({ elementPanelId: 'p1', selectedGids: [leadEl.gid] })
    })
    act(() => root.render(<OverlaySvg />))
    expect(container.querySelectorAll('[data-line-vertex]').length).toBe(4)
  })
})
