/**
 * 拖动形状时**带着装在里面的内容走**（2026-09-24，用户的流程图：拖框时框里的字与
 * 连着框的箭头留在原地）。判据住在 `lib/elementGeom.patchContents`，要钉住的事实：
 *   1. 包围盒落在框里（带容差）、且比框小的文字与形状整体跟着走；探出框外的、比框大的不动；
 *   2. 箭头按端点判：落在框里的那一端跟着走，两端都在则整根平移；容差外的不动；
 *   3. 每件内容写它自己的 override（pos_frac / endpoints_frac），全部进同一次 commit
 *      ——一条撤销、一次权威渲染；
 *   4. 按住 ⌘ / Ctrl = 只拖它自己（拖动途中按下也算，以最后一帧为准）；
 *   5. 预览：整体平移的内容 SVG 跟手，只有一端跟随的箭头画虚线；取消一条都不落；
 *   6. 锁定的、隐藏的不动；文字若已被挪过而渲染没回来，基准取文档里那条 override；
 *   7. 多选整组拖动时选区里的形状同样带着内容走。
 */
import { literal } from '@/i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MATPLOTLIB_SVG } from '@/lib/__fixtures__/matplotlibSvg'
import type { EngineRenderOptions, Manifest, ManifestElement } from '@/lib/api'
import { alignEntries, PATCH_CARRY_TOL_PT } from '@/lib/elementGeom'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { flushPreviewFrame, resetPreview } from '@/store/svgPreviewStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { startElementDrag, startElementGroupMove } from './interactions'

const engineRender = vi.fn()

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: (id: string, patches: unknown[], opts?: EngineRenderOptions) =>
    engineRender(id, patches, opts),
}))

/* -------------------------------- 测试数据 -------------------------------- */

const SIZE: [number, number] = [100, 100]
/** 容差换成 figure 分数（图幅 100mm 见方，两个方向一样） */
const TOL = (PATCH_CARRY_TOL_PT * 25.4) / 72 / SIZE[0]

const shape = (gid: string, bbox: [number, number, number, number]): ManifestElement => ({
  gid,
  role: 'patch',
  label: gid,
  bbox,
  editable: [],
  draggable: true,
  // 形状的锚点是包围盒左下角（y 向下：底边 = y + h）
  anchor: [bbox[0], bbox[1] + bbox[3]],
  drag_prop: 'pos_frac',
})

const text = (gid: string, bbox: [number, number, number, number]): ManifestElement => ({
  gid,
  role: 'text',
  label: gid,
  bbox,
  editable: [],
  draggable: true,
  anchor: [bbox[0] + bbox[2] / 2, bbox[1] + bbox[3] / 2],
  drag_prop: 'pos_frac',
})

const arrow = (gid: string, a: [number, number], b: [number, number]): ManifestElement => ({
  gid,
  role: 'arrow_patch',
  label: gid,
  bbox: [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.abs(b[0] - a[0]), Math.abs(b[1] - a[1])],
  editable: [],
  draggable: false,
  arrow_endpoints: [a, b],
})

// 被拖的框 P：x ∈ [0.2, 0.5]，y ∈ [0.2, 0.4]
const boxP = shape('axes_0.patches_1', [0.2, 0.2, 0.3, 0.2])
const outer = shape('axes_0.patches_0', [0.1, 0.1, 0.6, 0.6]) // 装着 P 的外层框：比 P 大，不跟
const card = shape('axes_0.patches_2', [0.3, 0.3, 0.1, 0.05]) // P 里的小卡片
// 与 P 一样大、错开不到容差的形状（阴影 / 叠放的第二个框）：落在「框里」的容差内，
// 但不比 P 小——它不是 P 的内容，否则两个一样大的框会互相带着走
const twin = shape('axes_0.patches_4', [0.202, 0.202, 0.3, 0.2])
const label = text('axes_0.texts_1', [0.25, 0.25, 0.1, 0.04]) // P 里的字
const farText = text('axes_0.texts_2', [0.75, 0.75, 0.1, 0.04]) // 外面的字
const straddle = text('axes_0.texts_3', [0.45, 0.25, 0.1, 0.04]) // 探出 P 右缘 0.05 ≫ 容差
const lockedText = text('axes_0.texts_4', [0.22, 0.34, 0.05, 0.03])
const hiddenText: ManifestElement = {
  ...text('axes_0.texts_6', [0.36, 0.22, 0.05, 0.03]),
  editable: [{ prop: 'visible', type: 'bool', value: false }],
}
const oneEnd = arrow('axes_0.texts_5.arrow', [0.5, 0.3], [0.7, 0.3]) // 尾在 P 右缘上
const bothEnds = arrow('axes_0.arrows_0', [0.25, 0.37], [0.45, 0.37]) // 整根在 P 里
const nearEdge = arrow('axes_0.texts_7.arrow', [0.9, 0.3], [0.5 + TOL * 0.8, 0.3]) // 头在容差内
const offEdge = arrow('axes_0.texts_8.arrow', [0.9, 0.35], [0.5 + TOL * 1.5, 0.35]) // 头在容差外
// 第二个框（多选用）：x ∈ [0.6, 0.8]，y ∈ [0.2, 0.3]；oneEnd 的头落在它里面
const boxR = shape('axes_0.patches_3', [0.6, 0.2, 0.2, 0.15])

const manifest: Manifest = {
  stem: 'Flow',
  size_mm: SIZE,
  elements: [
    { gid: 'figure', role: 'figure', label: '整图', bbox: [0, 0, 1, 1], editable: [], draggable: false },
    outer,
    boxP,
    card,
    boxR,
    twin,
    label,
    farText,
    straddle,
    lockedText,
    hiddenText,
    oneEnd,
    bothEnds,
    nearEdge,
    offEdge,
  ],
}

const panelOf = (overrides: PanelObject['overrides'] = []): PanelObject =>
  ({
    id: 'p1',
    type: 'panel',
    x: 0,
    y: 0,
    w: SIZE[0],
    h: SIZE[1],
    fileId: 'Flow.pdf',
    fileKind: 'pdf',
    nativeW: SIZE[0],
    nativeH: SIZE[1],
    script: 'flow.py',
    overrides,
    lockedGids: [lockedText.gid],
  }) as unknown as PanelObject

const layout = { width: mmToWorld(SIZE[0]), height: mmToWorld(SIZE[1]) }

const livePanel = (): PanelObject => {
  const p = useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1')
  if (p?.type !== 'panel') throw new Error('测试面板没了')
  return p
}

const overrideOf = (gid: string, prop: string) =>
  livePanel().overrides.find((o) => o.gid === gid && o.prop === prop)?.value as
    | number[]
    | undefined

const down = (clientX = 0, clientY = 0) =>
  ({ clientX, clientY, button: 0, stopPropagation() {} }) as unknown as React.PointerEvent

const fire = (
  type: 'pointermove' | 'pointerup' | 'pointercancel',
  clientX: number,
  clientY: number,
  mods: { metaKey?: boolean; ctrlKey?: boolean } = {},
) => window.dispatchEvent(new MouseEvent(type, { clientX, clientY, bubbles: true, ...mods }))

function dragTo(x: number, y: number, mods: { metaKey?: boolean; ctrlKey?: boolean } = {}) {
  const steps = 10
  for (let i = 1; i <= steps; i++) {
    fire('pointermove', (x * i) / steps, (y * i) / steps, mods)
    flushPreviewFrame()
  }
}

const tf = (gid: string) =>
  document.querySelector(`[data-element-svg="p1"] [id="${gid}"]`)?.getAttribute('transform') ?? null

/** 屏幕像素 → 内容分数（zoom = 1） */
const dfxOf = (px: number) => px / layout.width
const dfyOf = (px: number) => px / layout.height

/* -------------------------------- 环境搭建 -------------------------------- */

async function setup(overrides: PanelObject['overrides'] = []) {
  engineRender.mockReset()
  engineRender.mockResolvedValue({ rev: 2, manifest, svg: MATPLOTLIB_SVG, warnings: [] })
  resetPreview()
  localStorage.clear()
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700 })
  useUiStore.setState({ tool: 'select', snapEnabled: false, elementPanelId: 'p1', selectedGids: [] })
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_patch_carry')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(panelOf(overrides))
  })
  useRenderStore.getState().patch(renderKeyOf(livePanel()), {
    fileId: 'Flow.pdf',
    manifest,
    svg: MATPLOTLIB_SVG,
    rev: 1,
    status: 'ready',
    lastPatches: JSON.stringify(overrides),
  })
  useRenderStore.setState({ latest: { 'Flow.pdf': renderKeyOf(livePanel()) } })
  document.body.innerHTML = `<div data-element-svg="p1">${MATPLOTLIB_SVG}</div>`
  // 预览平移要的只是带 gid 的 <g>；必须补进面板容器里的那棵 svg
  const root = document.querySelector('[data-element-svg="p1"] svg')!
  for (const el of manifest.elements) {
    if (el.gid === 'figure') continue
    const g = document.createElementNS('http://www.w3.org/2000/svg', 'g')
    g.setAttribute('id', el.gid)
    root.appendChild(g)
  }
  useDocumentStore.setState({ past: [], future: [] })
}

beforeEach(() => {
  resetPreview()
})

afterEach(() => {
  resetPreview()
  useInteractionStore.getState().end()
  document.body.innerHTML = ''
})

/* ============================== 判据 ============================== */

describe('拖框：装在里面的内容跟着走', () => {
  it('框里的字与小卡片整体平移；外面的、探出框的、比框大的不动', async () => {
    await setup()
    startElementDrag(down(0, 0), livePanel(), boxP, layout)
    dragTo(40, 20)
    fire('pointerup', 40, 20)

    const [dfx, dfy] = [dfxOf(40), dfyOf(20)]
    expect(overrideOf(boxP.gid, 'pos_frac')![0]).toBeCloseTo(boxP.anchor![0] + dfx, 4)
    expect(overrideOf(boxP.gid, 'pos_frac')![1]).toBeCloseTo(boxP.anchor![1] + dfy, 4)
    for (const el of [label, card]) {
      const v = overrideOf(el.gid, 'pos_frac')!
      expect(v[0]).toBeCloseTo(el.anchor![0] + dfx, 4)
      expect(v[1]).toBeCloseTo(el.anchor![1] + dfy, 4)
    }
    for (const el of [farText, straddle, outer, boxR, twin]) {
      expect(overrideOf(el.gid, 'pos_frac')).toBeUndefined()
    }
  })

  it('箭头按端点：框里那一端跟着走，两端都在则整根平移；容差内算、容差外不算', async () => {
    await setup()
    startElementDrag(down(0, 0), livePanel(), boxP, layout)
    dragTo(40, 20)
    fire('pointerup', 40, 20)

    const [dfx, dfy] = [dfxOf(40), dfyOf(20)]
    const one = overrideOf(oneEnd.gid, 'endpoints_frac')!
    expect(one[0]).toBeCloseTo(0.5 + dfx, 4) // 尾在框上：跟
    expect(one[1]).toBeCloseTo(0.3 + dfy, 4)
    expect(one.slice(2)).toEqual([0.7, 0.3]) // 头在框外：不动
    const both = overrideOf(bothEnds.gid, 'endpoints_frac')!
    expect(both[0]).toBeCloseTo(0.25 + dfx, 4)
    expect(both[2]).toBeCloseTo(0.45 + dfx, 4)
    expect(both[3]).toBeCloseTo(0.37 + dfy, 4)
    const near = overrideOf(nearEdge.gid, 'endpoints_frac')!
    expect(near.slice(0, 2)).toEqual([0.9, 0.3])
    expect(near[2]).toBeCloseTo(0.5 + TOL * 0.8 + dfx, 4)
    expect(overrideOf(offEdge.gid, 'endpoints_frac')).toBeUndefined()
  })

  it('锁定的与隐藏的不动', async () => {
    await setup()
    startElementDrag(down(0, 0), livePanel(), boxP, layout)
    dragTo(40, 20)
    fire('pointerup', 40, 20)
    expect(overrideOf(lockedText.gid, 'pos_frac')).toBeUndefined()
    expect(overrideOf(hiddenText.gid, 'pos_frac')).toBeUndefined()
  })

  it('字已被挪过而渲染没回来：基准取文档里那条 override，不退回 manifest 的旧锚点', async () => {
    const moved: [number, number] = [label.anchor![0] + 0.01, label.anchor![1] + 0.01]
    await setup([{ gid: label.gid, prop: 'pos_frac', value: moved }])
    startElementDrag(down(0, 0), livePanel(), boxP, layout)
    dragTo(40, 0)
    fire('pointerup', 40, 0)
    const v = overrideOf(label.gid, 'pos_frac')!
    expect(v[0]).toBeCloseTo(moved[0] + dfxOf(40), 4)
    expect(v[1]).toBeCloseTo(moved[1], 4)
  })

  it('拖的是字不是框：什么都不带', async () => {
    await setup()
    startElementDrag(down(0, 0), livePanel(), label, layout)
    dragTo(40, 20)
    fire('pointerup', 40, 20)
    expect(livePanel().overrides.map((o) => o.gid)).toEqual([label.gid])
  })
})

/* ============================== 出口 ============================== */

describe('按住 ⌘ / Ctrl = 只拖它自己', () => {
  it('⌘：只写框自己的 pos_frac', async () => {
    await setup()
    startElementDrag(down(0, 0), livePanel(), boxP, layout)
    dragTo(40, 20, { metaKey: true })
    fire('pointerup', 40, 20, { metaKey: true })
    expect(livePanel().overrides.map((o) => o.gid)).toEqual([boxP.gid])
  })

  it('Ctrl 同义；途中松开以最后一帧为准（又带上了）', async () => {
    await setup()
    startElementDrag(down(0, 0), livePanel(), boxP, layout)
    dragTo(40, 20, { ctrlKey: true })
    // 按着 Ctrl 时字的预览归位（没有位移）
    expect([null, 'translate(0,0)']).toContain(tf(label.gid))
    fire('pointermove', 41, 20)
    flushPreviewFrame()
    fire('pointerup', 41, 20)
    expect(overrideOf(label.gid, 'pos_frac')![0]).toBeCloseTo(label.anchor![0] + dfxOf(41), 4)
  })
})

/* ============================== 预览与历史 ============================== */

describe('预览、取消与历史', () => {
  it('整体平移的内容 SVG 跟手；只有一端跟随的箭头画虚线', async () => {
    await setup()
    startElementDrag(down(0, 0), livePanel(), boxP, layout)
    dragTo(40, 20)
    expect(tf(boxP.gid)).toMatch(/^translate\(/)
    expect(tf(label.gid)).toMatch(/^translate\(/)
    expect(tf(bothEnds.gid)).toMatch(/^translate\(/)
    expect(tf(farText.gid)).toBeNull()
    // 单端跟随的箭头不平移 SVG（形状变了），交给覆盖层的虚线
    expect(tf(oneEnd.gid)).toBeNull()
    const dashed = useInteractionStore.getState().carriedArrows ?? []
    expect(dashed.map((d) => d.gid).sort()).toEqual([nearEdge.gid, oneEnd.gid].sort())
    const d = dashed.find((x) => x.gid === oneEnd.gid)!
    expect(d.a[0]).toBeCloseTo(0.5 + dfxOf(40), 4)
    expect(d.b).toEqual([0.7, 0.3])
    fire('pointerup', 40, 20)
    expect(useInteractionStore.getState().carriedArrows).toBeNull()
  })

  it('取消：一条都不落，虚线收掉', async () => {
    await setup()
    startElementDrag(down(0, 0), livePanel(), boxP, layout)
    dragTo(40, 20)
    fire('pointercancel', 40, 20)
    expect(livePanel().overrides).toHaveLength(0)
    expect(useDocumentStore.getState().past).toHaveLength(0)
    expect(engineRender).not.toHaveBeenCalled()
    expect(useInteractionStore.getState().carriedArrows).toBeNull()
  })

  it('一次拖动 = 一条撤销 = 一次权威渲染', async () => {
    await setup()
    startElementDrag(down(0, 0), livePanel(), boxP, layout)
    dragTo(40, 20)
    fire('pointerup', 40, 20)
    expect(engineRender).toHaveBeenCalledTimes(1)
    expect(useDocumentStore.getState().past).toHaveLength(1)
    useDocumentStore.getState().undo()
    expect(livePanel().overrides).toHaveLength(0)
  })
})

/* ============================== 多选 ============================== */

describe('多选整组拖动', () => {
  it('选区里的每个框都带着自己的内容；两端分属两个被选框的箭头整根平移', async () => {
    await setup()
    const entries = alignEntries(livePanel(), manifest, [boxP.gid, boxR.gid])
    expect(entries.map((e) => e.key)).toEqual([boxP.gid, boxR.gid])
    startElementGroupMove(down(0, 0), livePanel(), entries, layout)
    dragTo(40, 20)
    fire('pointerup', 40, 20)

    const [dfx, dfy] = [dfxOf(40), dfyOf(20)]
    const one = overrideOf(oneEnd.gid, 'endpoints_frac')!
    expect(one[0]).toBeCloseTo(0.5 + dfx, 4)
    expect(one[2]).toBeCloseTo(0.7 + dfx, 4)
    expect(one[3]).toBeCloseTo(0.3 + dfy, 4)
    expect(overrideOf(label.gid, 'pos_frac')![0]).toBeCloseTo(label.anchor![0] + dfx, 4)
    expect(useDocumentStore.getState().past).toHaveLength(1)
  })

  it('⌘ 同样只动选区本身', async () => {
    await setup()
    const entries = alignEntries(livePanel(), manifest, [boxP.gid, boxR.gid])
    startElementGroupMove(down(0, 0), livePanel(), entries, layout)
    dragTo(40, 20, { metaKey: true })
    fire('pointerup', 40, 20, { metaKey: true })
    expect(livePanel().overrides.map((o) => o.gid).sort()).toEqual([boxP.gid, boxR.gid].sort())
  })
})
