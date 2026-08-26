/**
 * 图内元素的**路径式**选中 / 命中 / 框选。
 *
 * 背景（修之前）：曲线、fill_between、多边形沿用「bbox 矩形」三件套——选中画
 * 一个与图形对不上的矩形、命中吃整个空白包围盒。两条交叉曲线的选中框长得
 * 一模一样，而点在 bbox 的空白角上就会选中一条离得很远的线。
 *
 * 现在（与图内箭头同一套语义）：
 * - manifest 带 `geometry`（引擎算好的真实路径，figure 分数、y 向下）
 * - pickElement 按路径命中（填充算内部，空心只在描边附近）
 * - 框选按路径与框相交
 * - OverlaySvg 画 `<path>` 描示，不再是 `fill-opacity` 的矩形
 * - 文字 / 图例 / 子图 / 组选择**继续**用 bbox（它们本来就是矩形语义）
 *
 * jsdom 说明：这里断言的是结构与数值（选出的 gid、渲染出的 SVG 元素、
 * 路径点的换算），不依赖任何真实的 CSS 命中测试。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import type { ElementGeometry, Manifest, ManifestElement } from '@/lib/api'
import { geomHitsRect } from '@/lib/pathGeom'
import { literal } from '@/i18n'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useRenderStore } from '@/store/renderStore'
import { seedExactRender } from '@/test/renderFixtures'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { OverlaySvg } from './OverlaySvg'
import { pickElement } from './interactions'

/* ------------------------------ 测试用 manifest ------------------------------ */

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
  editable: [{ prop: 'position', type: 'rect', value: [0.1, 0.1, 0.8, 0.8] }],
  draggable: false,
  resizable: true,
}

/** 第二个子图（组选择的对照：组框仍是包围盒矩形） */
const axes1El: ManifestElement = {
  gid: 'axes_1',
  role: 'axes',
  label: '子图 2',
  bbox: [0.05, 0.05, 0.06, 0.06],
  editable: [{ prop: 'position', type: 'rect', value: [0.05, 0.89, 0.06, 0.06] }],
  draggable: false,
  resizable: true,
}

const textEl: ManifestElement = {
  gid: 'axes_0.texts_0',
  role: 'text',
  label: '文字',
  bbox: [0.62, 0.62, 0.2, 0.1],
  editable: [],
  draggable: true,
  anchor: [0.7, 0.66],
  drag_prop: 'pos_frac',
}

/** 对角曲线 y = x：bbox 是 0.6×0.6 的一大块，真实的线只是那条对角线 */
const lineGeom: ElementGeometry = {
  kind: 'polyline',
  paths: [{ points: [[0.2, 0.2], [0.8, 0.8]], closed: false }],
  fill: false,
  stroke: true,
  clip: [0.1, 0.1, 0.8, 0.8],
}

const lineEl: ManifestElement = {
  gid: 'axes_0.lines_0',
  role: 'line',
  label: '曲线',
  bbox: [0.2, 0.2, 0.6, 0.6],
  geometry: lineGeom,
  editable: [],
  draggable: false,
}

/** 断开成两段的填充（fill_between 撞上 NaN 就是这个形状） */
const fillEl: ManifestElement = {
  gid: 'axes_0.fill_0',
  role: 'fill',
  label: '填充区域',
  bbox: [0.15, 0.15, 0.7, 0.2],
  geometry: {
    kind: 'multi_path',
    paths: [
      { points: [[0.15, 0.15], [0.35, 0.15], [0.35, 0.25], [0.15, 0.25]], closed: true },
      { points: [[0.65, 0.15], [0.85, 0.15], [0.85, 0.25], [0.65, 0.25]], closed: true },
    ],
    fill: true,
    stroke: false,
  },
  editable: [],
  draggable: false,
}

/** 独立形状（ax.fill 的 Polygon）：闭合三角 */
const patchEl: ManifestElement = {
  gid: 'axes_0.patches_0',
  role: 'patch',
  label: '形状 1',
  bbox: [0.3, 0.55, 0.3, 0.25],
  geometry: {
    kind: 'path',
    paths: [{ points: [[0.3, 0.8], [0.6, 0.8], [0.45, 0.55]], closed: true }],
    fill: true,
    stroke: true,
  },
  editable: [],
  draggable: false,
}

/** 散点：**有意**只有 bbox（记录在案的降级） */
const scatterEl: ManifestElement = {
  gid: 'axes_0.scatter_1',
  role: 'scatter',
  label: '散点',
  bbox: [0.15, 0.85, 0.7, 0.05],
  editable: [],
  draggable: false,
}

const manifest: Manifest = {
  stem: 'Fig1',
  size_mm: [100, 100],
  elements: [figureEl, axesEl, axes1El, lineEl, fillEl, patchEl, textEl, scatterEl],
}

const panel = (over: Partial<PanelObject> = {}): PanelObject => ({
  id: 'p1',
  type: 'panel',
  x: 10,
  y: 20,
  w: 100,
  h: 100,
  fileId: 'f1',
  fileKind: 'pdf',
  nativeW: 100,
  nativeH: 100,
  script: 'fig.py',
  overrides: [],
  ...over,
})

const px = (mm: number) => mmToWorld(mm)

/* ------------------------------ 命中 ------------------------------ */

describe('pickElement：曲线按真实路径命中', () => {
  it('点在线上命中曲线（哪怕它整个压在子图上）', () => {
    expect(pickElement(manifest, 0.5, 0.5)?.gid).toBe('axes_0.lines_0')
  })

  it('点在 bbox 的空白角上不再命中曲线，落回底下的子图', () => {
    // (0.78, 0.35)：曲线 bbox 的右上角，到那条对角线约 30mm
    expect(pickElement(manifest, 0.78, 0.35)?.gid).toBe('axes_0')
  })

  it('bbox 空白角下面压着别的元素时，点击归那个元素', () => {
    // (0.7, 0.66)：曲线 bbox 内、同时在文字 bbox 里
    expect(pickElement(manifest, 0.7, 0.66)?.gid).toBe('axes_0.texts_0')
  })

  it('填充区域：内部命中；两块之间的空档不命中', () => {
    expect(pickElement(manifest, 0.25, 0.2)?.gid).toBe('axes_0.fill_0')
    expect(pickElement(manifest, 0.5, 0.185)?.gid).toBe('axes_0')
  })

  it('多边形：内部与描边附近命中，外面不命中', () => {
    expect(pickElement(manifest, 0.45, 0.75)?.gid).toBe('axes_0.patches_0')  // 内部
    expect(pickElement(manifest, 0.45, 0.548)?.gid).toBe('axes_0.patches_0') // 贴着顶点
    expect(pickElement(manifest, 0.32, 0.58)?.gid).toBe('axes_0')            // bbox 左上空白角
  })

  it('散点仍按 bbox 命中（有意的降级，不是遗漏）', () => {
    expect(pickElement(manifest, 0.5, 0.87)?.gid).toBe('axes_0.scatter_1')
  })
})

/* ------------------------------ 框选 ------------------------------ */

describe('框选：按真实路径与选择带相交', () => {
  it('框穿过曲线算圈中', () => {
    expect(geomHitsRect(lineGeom, { x: 0.45, y: 0.45, w: 0.1, h: 0.1 })).toBe(true)
  })

  it('框只落在曲线 bbox 的空白角里，不算圈中', () => {
    // y=x 那条线的空白角在右上（大 x、小 y）
    expect(geomHitsRect(lineGeom, { x: 0.7, y: 0.28, w: 0.08, h: 0.08 })).toBe(false)
  })

  it('填充的两块各自独立：框碰到左边那块的边，右边那块不该被算上', () => {
    const left = { x: 0.32, y: 0.18, w: 0.06, h: 0.05 }   // 跨过左块的右边界
    expect(geomHitsRect(fillEl.geometry!, left)).toBe(true)
    expect(geomHitsRect({ ...fillEl.geometry!, paths: [fillEl.geometry!.paths[1]] }, left)).toBe(
      false,
    )
  })

  it('框整个落在填充内部**不**算圈中（框选是圈墨迹，不是戳进去）', () => {
    expect(geomHitsRect(fillEl.geometry!, { x: 0.2, y: 0.18, w: 0.05, h: 0.04 })).toBe(false)
  })
})

/* ------------------------------ 覆盖层描示 ------------------------------ */

describe('OverlaySvg：路径式选中描示', () => {
  let container: HTMLDivElement
  let root: Root

  const rects = () => container.querySelectorAll('rect[fill-opacity]')
  const paths = () => [...container.querySelectorAll('path')].filter((p) => p.getAttribute('d'))

  beforeEach(async () => {
    localStorage.clear()
    useViewportStore.setState({
      zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700,
    })
    useUiStore.setState({
      tool: 'select', snapEnabled: false, elementPanelId: null,
      cropTargetId: null, selectedGids: [],
    })
    useSelectionStore.getState().clear()
    useRenderStore.setState({ render: async () => {} })
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_path_sel')
    useDocumentStore.getState().commit(literal('加面板'), (d) => {
      d.objects.push(panel())
    })
    seedExactRender(panel(), manifest)
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
  })

  afterEach(() => {
    act(() => root.unmount())
    container.remove()
    useInteractionStore.getState().end()
  })

  const show = (gids: string[]) => {
    useUiStore.setState({ elementPanelId: 'p1', selectedGids: gids })
    act(() => root.render(<OverlaySvg />))
  }

  it('选中曲线：画一条沿真实路径的 <path>，没有带底色的矩形选中框', () => {
    show(['axes_0.lines_0'])
    expect(rects().length).toBe(0)
    const d = paths().map((p) => p.getAttribute('d')!).find((s) => s.startsWith('M'))
    expect(d).toBeTruthy()
    // 面板在 (10,20)、内容 100×100mm：分数 (0.2,0.2) → mm (30,40)
    expect(d).toContain(`M${px(30).toFixed(2)},${px(40).toFixed(2)}`)
    expect(d).toContain(`L${px(90).toFixed(2)},${px(100).toFixed(2)}`)
    expect(d).not.toContain('Z')
  })

  it('曲线的描示带上引擎给的裁剪框（数据伸出子图的那一截不该被描出来）', () => {
    show(['axes_0.lines_0'])
    const outline = paths().find((p) => p.getAttribute('clip-path'))
    expect(outline).toBeTruthy()
    expect(container.querySelectorAll('clipPath rect').length).toBe(1)
  })

  it('选中多边形：闭合路径（带 Z）+ 一层很淡的填充，让人看出「这一整块」', () => {
    show(['axes_0.patches_0'])
    expect(rects().length).toBe(0)
    const outline = paths().find((p) => p.getAttribute('d')?.includes('Z'))
    expect(outline).toBeTruthy()
    expect(outline!.getAttribute('fill')).toBe('var(--color-accent)')
    expect(outline!.getAttribute('fill-rule')).toBe('evenodd')
  })

  it('断开的填充画成多条子路径，不会被连成一块', () => {
    show(['axes_0.fill_0'])
    const d = paths().find((p) => p.getAttribute('d')?.includes('Z'))!.getAttribute('d')!
    expect(d.match(/M/g)?.length).toBe(2)
  })

  it('文字 / 子图**继续**用矩形框（它们本来就是矩形语义，别为了统一硬转路径）', () => {
    show(['axes_0.texts_0'])
    expect(rects().length).toBe(1)
    show(['axes_0'])
    expect(rects().length).toBe(1)
  })

  it('多选子图的组包围框仍是矩形虚线框（组的语义就是包围盒，别硬转路径）', () => {
    show(['axes_0', 'axes_1'])
    // 组框的指纹：strokeDasharray="4 2" 的无底色 rect
    const dashed = [...container.querySelectorAll('rect')].filter(
      (r) => r.getAttribute('stroke-dasharray') === '4 2',
    )
    expect(dashed.length).toBe(1)
    expect(paths().some((p) => p.getAttribute('d')?.startsWith('M'))).toBe(false)
  })

  it('曲线与子图一起选中时，曲线仍走路径、子图仍走矩形', () => {
    show(['axes_0', 'axes_0.lines_0'])
    expect(rects().length).toBe(1)                                  // 子图那一份
    expect(paths().some((p) => p.getAttribute('d')?.startsWith('M'))).toBe(true)
  })

  it('hover 也走路径（描示更淡）', () => {
    useUiStore.setState({ elementPanelId: 'p1', selectedGids: [] })
    useInteractionStore.getState().setHoverGid('axes_0.lines_0')
    act(() => root.render(<OverlaySvg />))
    const outline = paths().find((p) => p.getAttribute('d')?.startsWith('M'))
    expect(outline).toBeTruthy()
    expect(Number(outline!.getAttribute('stroke-opacity'))).toBeLessThan(1)
    expect(rects().length).toBe(0)
  })

  it('拖动中：路径跟着同一个乐观位移走（只挪框不挪路径 = 拖动全程对不上）', () => {
    useUiStore.setState({ elementPanelId: 'p1', selectedGids: ['axes_0.lines_0'] })
    useInteractionStore.getState().setGidDrag({ gid: 'axes_0.lines_0', dfx: 0.1, dfy: -0.05 })
    act(() => root.render(<OverlaySvg />))
    const d = paths().find((p) => p.getAttribute('d')?.startsWith('M'))!.getAttribute('d')!
    // (0.2,0.2) + (0.1,-0.05) → (0.3,0.15) → mm (40, 35)
    expect(d).toContain(`M${px(40).toFixed(2)},${px(35).toFixed(2)}`)
  })

  it('面板整体旋转后，路径描示与选中框绕同一个中心转过去', () => {
    useDocumentStore.getState().commit(literal('转 90°'), (d) => {
      const p = d.objects[0]
      if (p.type === 'panel') p.rotation = 90
    })
    seedExactRender(useDocumentStore.getState().doc.objects[0] as PanelObject, manifest)
    show(['axes_0.lines_0'])
    const g = [...container.querySelectorAll('g')].find((el) =>
      el.getAttribute('transform')?.startsWith('rotate(90'),
    )
    expect(g, '旋转面板的图内描示必须整组 rotate').toBeTruthy()
    expect(g!.querySelector('path[d]')).toBeTruthy()
  })
})
