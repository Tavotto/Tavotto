/**
 * 图内拖动手势的生命周期：它**不许比自己的依据活得更久**（QA 2026-09-24 STATE 节，#583）。
 *
 *   STATE-04-B1  松手后权威渲染还在路上时撤销：文档回到拖动前，画布上的预览位移必须跟着撤；
 *                拖动那一版晚到也不许把它带回来。成因：撤销后面板键回到缓存里的旧变体，那份
 *                SVG 字符串正是画布上挂着的显示退路，`reattachPreview` 的 effect 不跑。
 *   STATE-02-B1  拖动中按 Esc：先取消这次手势（DOM 还原、0 override / 0 历史 / 0 渲染），
 *                之后再按 Esc 退出图内编辑态、松手，都不许再写文档。
 *   STATE-07-B1  拖动中改视图倍率：下一次 pointermove 不跳位（以倍率变化那一刻重建基准）。
 *
 * 这里**真渲染 PanelView**（撤销那条的缺陷就在 PanelView 的 effect 依赖上，手写 innerHTML
 * 的用例看不见）；DOM 几何另有真浏览器用例 `e2e/drag-gesture-lifecycle.spec.ts`。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { literal } from '@/i18n'
import { seedExactRender } from '@/test/renderFixtures'
import { MATPLOTLIB_SVG } from '@/lib/__fixtures__/matplotlibSvg'
import type { EngineRenderOptions, Manifest, ManifestElement } from '@/lib/api'
import { runUndoRedo, useKeyboard } from '@/hooks/useKeyboard'
import { setOverride } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { flushPreviewFrame, previewSession, resetPreview } from '@/store/svgPreviewStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { startElementDrag } from './interactions'
import { PanelView } from './PanelView'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const engineRender = vi.fn()
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: (id: string, patches: unknown[], opts?: EngineRenderOptions) =>
    engineRender(id, patches, opts),
}))

const textEl: ManifestElement = {
  gid: 'axes_0.title',
  role: 'title',
  label: '标题',
  bbox: [0.2, 0.05, 0.4, 0.08],
  editable: [],
  draggable: true,
  anchor: [0.4, 0.09],
  drag_prop: 'pos_frac',
}
const manifest: Manifest = {
  stem: 'Fig1',
  size_mm: [101.6, 76.2],
  elements: [
    { gid: 'figure', role: 'figure', label: '整图', bbox: [0, 0, 1, 1], editable: [], draggable: false },
    textEl,
  ],
}
const panelOf = (): PanelObject =>
  ({
    id: 'p1',
    type: 'panel',
    x: 0,
    y: 0,
    w: 101.6,
    h: 76.2,
    fileId: 'Fig1.pdf',
    fileKind: 'pdf',
    nativeW: 101.6,
    nativeH: 76.2,
    script: 'fig.py',
    overrides: [],
  }) as unknown as PanelObject
const layout = { width: mmToWorld(101.6), height: mmToWorld(76.2) }

const livePanel = (): PanelObject => {
  const p = useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1')
  if (p?.type !== 'panel') throw new Error('测试面板没了')
  return p
}
const down = (clientX = 0, clientY = 0) =>
  ({ clientX, clientY, button: 0, stopPropagation() {} }) as unknown as React.PointerEvent
const fire = (type: 'pointermove' | 'pointerup' | 'pointercancel', clientX: number, clientY: number) =>
  window.dispatchEvent(new MouseEvent(type, { clientX, clientY, bubbles: true }))
const esc = () =>
  window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
const titleNode = () => container.querySelector('[data-element-svg="p1"] [id="axes_0.title"]')
const tf = () => titleNode()?.getAttribute('transform') ?? null

/** 把 useKeyboard 挂上（它在 effect 里往 window 注册 keydown） */
function Keys() {
  useKeyboard()
  return null
}

let container: HTMLDivElement
let root: Root
const show = async () => {
  await act(async () =>
    root.render(
      <>
        <Keys />
        {useUiStore.getState().elementPanelId ? <PanelView obj={livePanel()} /> : null}
      </>,
    ),
  )
}

/** 按下标题，走 20 步到 (200, 100)；每步都落一帧预览 */
function dragTitle() {
  startElementDrag(down(0, 0), livePanel(), textEl, layout)
  for (let i = 1; i <= 20; i++) {
    fire('pointermove', i * 10, i * 5)
    flushPreviewFrame()
  }
}

let resolveRender: (v: unknown) => void = () => {}
let baseTf: string | null = null

beforeEach(async () => {
  engineRender.mockReset()
  // 权威渲染一直在路上，直到用例自己放行
  engineRender.mockImplementation(
    () =>
      new Promise((r) => {
        resolveRender = r
      }),
  )
  resetPreview()
  localStorage.clear()
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700 })
  useUiStore.setState({ tool: 'select', snapEnabled: false, elementPanelId: 'p1', selectedGids: [] })
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_gesture_lifecycle')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(panelOf())
  })
  useRenderStore.getState().patch(renderKeyOf(panelOf()), {
    fileId: 'Fig1.pdf',
    manifest,
    svg: MATPLOTLIB_SVG,
    rev: 1,
    status: 'ready',
    lastPatches: '[]',
  })
  useRenderStore.setState({ latest: { 'Fig1.pdf': renderKeyOf(panelOf()) } })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await show()
  baseTf = tf()
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  resetPreview()
  useInteractionStore.getState().end()
})

describe('STATE-04：权威渲染在途时撤销', () => {
  it('撤销之后画布立刻回到原位，预览会话收尾', async () => {
    expect(titleNode(), '真渲染出来的 SVG 里应当有这个元素').not.toBeNull()
    dragTitle()
    fire('pointerup', 200, 100)
    await show()
    expect(tf(), '松手后等权威：预览应当挂着').toMatch(/^translate\(/)
    expect(engineRender).toHaveBeenCalledTimes(1)

    runUndoRedo(false)
    await show()
    expect(livePanel().overrides, '文档应当已撤销').toEqual([])
    expect(tf(), '撤销后文档已无 override，画布却仍挂着拖动位移').toBe(baseTf)
    expect(previewSession(), '等图的预览会话应当随撤销收尾').toBeNull()
  })

  it('拖动那一版的回包在撤销之后才到：画布仍在原位', async () => {
    dragTitle()
    fire('pointerup', 200, 100)
    await show()
    runUndoRedo(false)
    await show()
    await act(async () => {
      resolveRender({ rev: 2, manifest, svg: `${MATPLOTLIB_SVG}<!-- dragged -->`, warnings: [] })
    })
    await show()
    expect(tf(), '拖动那一版回包之后，撤销掉的位移又回到了画布上').toBe(baseTf)
  })

  it('松手后改了别的（拖动那条还在文档里）：预览继续挂着，不先弹回原位', async () => {
    dragTitle()
    fire('pointerup', 200, 100)
    await show()
    const applied = tf()
    expect(applied).toMatch(/^translate\(/)
    // 与这次拖动无关的另一条 override：面板键变了，但新一版权威渲染照样带着拖动
    setOverride('p1', 'axes_0.title', 'color', '#ff0000', true)
    await show()
    expect(livePanel().overrides.map((o) => o.prop).sort()).toEqual(['color', 'pos_frac'])
    expect(tf(), '拖动那条还在文档里，预览却被撤了（会先弹回原位再跳回来）').toBe(applied)
    expect(previewSession(), '会话应当还在等图').not.toBeNull()
  })

  it('拖动前就有的无关 override 在等图期间被改掉 / 删掉：拖动那条还在，预览继续挂着', async () => {
    setOverride('p1', 'axes_0.title', 'color', '#00ff00', true)
    await show()
    engineRender.mockClear()
    dragTitle()
    fire('pointerup', 200, 100)
    await show()
    const applied = tf()
    expect(applied).toMatch(/^translate\(/)
    // 改掉拖动之前就在的那条（不是这次手势写的）
    setOverride('p1', 'axes_0.title', 'color', '#0000ff', true)
    await show()
    expect(tf(), '改的是拖动之前就有的 override，拖动的预览却被撤了').toBe(applied)
    // 再把它整条删掉
    useDocumentStore.getState().commit(literal('删颜色'), (d) => {
      const o = d.objects.find((x) => x.id === 'p1') as PanelObject
      o.overrides = o.overrides.filter((x) => x.prop !== 'color')
    })
    await show()
    expect(tf(), '删的是拖动之前就有的 override，拖动的预览却被撤了').toBe(applied)
    expect(previewSession(), '会话应当还在等图').not.toBeNull()
    // 撤掉拖动本身（连撤两步：删颜色、改颜色，再撤拖动）才还原
    runUndoRedo(false)
    runUndoRedo(false)
    await show()
    expect(tf()).toBe(applied)
    runUndoRedo(false)
    await show()
    expect(livePanel().overrides.map((o) => o.prop)).toEqual(['color'])
    expect(tf(), '撤掉拖动之后预览应当还原').toBe(baseTf)
  })
})

describe('STATE-02：拖动中按 Esc', () => {
  it('第一下 Esc 取消手势：DOM 还原、interaction 回 none，编辑态与选中不动', async () => {
    useUiStore.setState({ selectedGids: ['axes_0.title'] })
    await show()
    dragTitle()
    expect(tf()).toMatch(/^translate\(/)
    const past = useDocumentStore.getState().past.length
    esc()
    await show()
    expect(tf(), 'Esc 之后预览位移应当还原').toBe(baseTf)
    expect(useInteractionStore.getState().kind).toBe('none')
    expect(useUiStore.getState().elementPanelId, '这一下只取消手势，不退编辑态').toBe('p1')
    expect(useUiStore.getState().selectedGids, '这一下只取消手势，不清选中').toEqual(['axes_0.title'])
    // 迟到的 move / up 不复活
    fire('pointermove', 260, 130)
    flushPreviewFrame()
    fire('pointerup', 260, 130)
    await show()
    expect(tf()).toBe(baseTf)
    expect(livePanel().overrides).toEqual([])
    expect(useDocumentStore.getState().past.length, '取消不进历史').toBe(past)
    expect(engineRender, '取消不渲染').not.toHaveBeenCalled()
  })

  it('Esc 取消后再按两下退出图内编辑态，松手仍然 0 override / 0 历史 / 0 渲染', async () => {
    useUiStore.setState({ selectedGids: ['axes_0.title'] })
    await show()
    const past = useDocumentStore.getState().past.length
    dragTitle()
    esc()
    esc()
    esc()
    await show()
    expect(useUiStore.getState().elementPanelId, '三下 Esc 应当已退出图内编辑态').toBeNull()
    fire('pointermove', 290, 145)
    fire('pointerup', 290, 145)
    await show()
    expect(livePanel().overrides).toEqual([])
    expect(useDocumentStore.getState().past.length).toBe(past)
    expect(engineRender).not.toHaveBeenCalled()
  })

  it('取消之后立刻正常拖一次：一条历史一次渲染，落点从原锚点起算', async () => {
    dragTitle()
    esc()
    await show()
    const past = useDocumentStore.getState().past.length
    dragTitle()
    fire('pointerup', 200, 100)
    await show()
    expect(useDocumentStore.getState().past.length).toBe(past + 1)
    expect(engineRender).toHaveBeenCalledTimes(1)
    const ov = livePanel().overrides.find((o) => o.prop === 'pos_frac')
    const [fx, fy] = ov!.value as [number, number]
    expect(fx).toBeCloseTo(0.4 + 200 / layout.width, 6)
    expect(fy).toBeCloseTo(0.09 + 100 / layout.height, 6)
  })

  it('没有拖动时 Esc 照旧：先清选中', async () => {
    useUiStore.setState({ selectedGids: ['axes_0.title'] })
    esc()
    expect(useUiStore.getState().selectedGids).toEqual([])
    expect(useUiStore.getState().elementPanelId).toBe('p1')
  })
})

describe('STATE-07：拖动中改视图倍率', () => {
  it('倍率变化后的下一次 move 连续：只加上新增位移按新倍率换算的那一段', async () => {
    startElementDrag(down(0, 0), livePanel(), textEl, layout)
    fire('pointermove', 60, 0)
    const before = useInteractionStore.getState().gidDrag!
    expect(before.dfx).toBeCloseTo(60 / layout.width, 9)
    act(() => useViewportStore.setState({ zoom: 1.25 }))
    fire('pointermove', 61, 0)
    const after = useInteractionStore.getState().gidDrag!
    // 没有重建基准时这里是 61 / (W × 1.25)：比倍率变化前还小，元素往回跳 ≈ 60×(1−1/1.25)
    expect(after.dfx, '倍率变化后下一次 move 跳位了').toBeCloseTo(
      60 / layout.width + 1 / (layout.width * 1.25),
      9,
    )
    expect(after.dfy).toBe(0)
    // 再变一次（缩回去）：两段各按各的倍率累计
    act(() => useViewportStore.setState({ zoom: 1 }))
    fire('pointermove', 71, 0)
    expect(useInteractionStore.getState().gidDrag!.dfx).toBeCloseTo(
      60 / layout.width + 1 / (layout.width * 1.25) + 10 / layout.width,
      9,
    )
    fire('pointerup', 71, 0)
    await show()
    const ov = livePanel().overrides.find((o) => o.prop === 'pos_frac')
    expect((ov!.value as number[])[0], '提交值与最后一帧预览一致').toBeCloseTo(
      0.4 + 60 / layout.width + 1 / (layout.width * 1.25) + 10 / layout.width,
      9,
    )
  })
})

describe('同步中继续拖动：原生多DOM只暂存意图，正式落点等待已挂载权威', () => {
  const ids = ['title.left', 'title.right']
  const splitSvg = (revision = 1) => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="50 20 100 100" width="200pt" height="100pt"
    data-tavotto-svg-targets='${JSON.stringify({ [textEl.gid]: { transform: ids, style: ids } })}'>
    <g data-tavotto-native-viewport-pt="200 100"><g id="title.left" transform="translate(1,2)"><path d="M0 0L10 0"/></g></g>
    <rect id="unbound-decoration" x="70" y="20" width="20" height="20"/>
    <g data-tavotto-native-viewport-pt="200 100"><g id="title.right" transform="scale(1)"><path d="M0 0L10 0"/></g></g>
    <!-- revision ${revision} --></svg>`
  const targetNodes = () => ids.map((id) => container.querySelector(`[id="${id}"]`)!)
  const transforms = () => targetNodes().map((node) => node.getAttribute('transform'))
  const hitRect = (left: number) => ({ left, right: left + 20, top: 20, bottom: 40 }) as DOMRect
  const mockTargetRects = () => targetNodes().forEach((node, index) => {
    vi.spyOn(node, 'getBoundingClientRect').mockReturnValue(hitRect(index ? 140 : 10))
  })
  const pendingDown = (x: number, y = 30) => {
    const syncing = container.querySelector('[data-authority="syncing"]')
    expect(syncing, '必须从真正显示同步中的产品命中层起手').not.toBeNull()
    act(() => syncing!.dispatchEvent(new MouseEvent('pointerdown', { clientX: x, clientY: y, bubbles: true })))
  }
  const firstCommit = async (svg = splitSvg()) => {
    seedExactRender(livePanel(), manifest, { svg, rev: 2 })
    useUiStore.setState({ selectedGids: [textEl.gid] })
    await show()
    startElementDrag(down(), livePanel(), textEl, layout)
    fire('pointermove', 20, 0)
    flushPreviewFrame()
    fire('pointerup', 20, 0)
    await show()
    return { overrides: structuredClone(livePanel().overrides), past: useDocumentStore.getState().past.length }
  }
  const authority = async (revision: number, nextManifest = manifest, svg = splitSvg(revision)) => {
    await act(async () => { seedExactRender(livePanel(), nextManifest, { svg, rev: revision }) })
    await show()
  }
  const anchor = () => livePanel().overrides.find((p) => p.prop === 'pos_frac')!.value as number[]

  afterEach(() => { act(() => useUiStore.setState({ elementPanelId: null, selectedGids: [] })) })

  it('没有主gid仍可在第二目标再次起拖，连续意图逐版权威到达才各自提交', async () => {
    const first = await firstCommit()
    expect(titleNode()).toBeNull()
    mockTargetRects()
    const originalAnchor = [...anchor()]
    pendingDown(150)
    expect(useInteractionStore.getState().kind).toBe('element')
    fire('pointermove', 190, 30)
    fire('pointerup', 190, 30)
    pendingDown(20)
    fire('pointermove', 20, 60)
    fire('pointerup', 20, 60)
    expect(livePanel().overrides, '两次后续拖动都不能借旧manifest落笔').toEqual(first.overrides)
    expect(useDocumentStore.getState().past.length).toBe(first.past)
    await authority(3)
    expect(anchor()[0]).toBeCloseTo(originalAnchor[0] + 40 / layout.width, 8)
    expect(anchor()[1]).toBeCloseTo(originalAnchor[1], 8)
    expect(useDocumentStore.getState().past.length).toBe(first.past + 1)
    await authority(4)
    expect(anchor()[0]).toBeCloseTo(originalAnchor[0] + 40 / layout.width, 8)
    expect(anchor()[1]).toBeCloseTo(originalAnchor[1] + 30 / layout.height, 8)
    expect(useDocumentStore.getState().past.length).toBe(first.past + 2)
  })

  it('后续手势取消会还原全部目标，晚到权威不补写已经取消的意图', async () => {
    const first = await firstCommit()
    mockTargetRects()
    const before = transforms()
    pendingDown(150)
    fire('pointermove', 190, 30)
    fire('pointercancel', 190, 30)
    expect(transforms()).toEqual(before)
    expect(livePanel().overrides).toEqual(first.overrides)
    await authority(3)
    expect(livePanel().overrides).toEqual(first.overrides)
    expect(useDocumentStore.getState().past.length).toBe(first.past)
    expect(transforms()).toEqual(['translate(1,2)', 'scale(1)'])
  })

  it('按住期间权威换上真实新DOM时只重挂预览，松手才按新版权威提交一次', async () => {
    const first = await firstCommit()
    mockTargetRects()
    pendingDown(150)
    fire('pointermove', 190, 30)
    await authority(3)
    expect(livePanel().overrides).toEqual(first.overrides)
    expect(useDocumentStore.getState().past.length).toBe(first.past)
    fire('pointerup', 190, 30)
    await show()
    expect(anchor()[0]).toBeCloseTo((first.overrides[0].value as number[])[0] + 40 / layout.width, 8)
    expect(useDocumentStore.getState().past.length).toBe(first.past + 1)
    expect(transforms().every((tf) => tf?.startsWith(`translate(${40 / layout.width * 200},0)`))).toBe(true)
  })

  it('权威到达时科学元素已被移除，排队位移作废且新DOM零改动', async () => {
    const first = await firstCommit()
    mockTargetRects()
    pendingDown(150)
    fire('pointermove', 190, 30)
    fire('pointerup', 190, 30)
    const svg = '<svg xmlns="http://www.w3.org/2000/svg"><rect id="replacement" transform="translate(7,9)"/></svg>'
    await authority(3, { ...manifest, elements: manifest.elements.filter((el) => el.gid !== textEl.gid) }, svg)
    expect(livePanel().overrides).toEqual(first.overrides)
    expect(useDocumentStore.getState().past.length).toBe(first.past)
    expect(container.querySelector('[id="replacement"]')!.getAttribute('transform')).toBe('translate(7,9)')
    expect(useInteractionStore.getState().kind).toBe('none')
  })

  it('分散目标的空白和未绑定装饰不接受排队拖动，不能把union大框当成元素', async () => {
    const first = await firstCommit()
    mockTargetRects()
    pendingDown(80)
    expect(useInteractionStore.getState().kind).toBe('none')
    fire('pointermove', 120, 30)
    fire('pointerup', 120, 30)
    expect(livePanel().overrides).toEqual(first.overrides)
    expect(container.querySelector('[id="unbound-decoration"]')!.getAttribute('transform')).toBeNull()
    await authority(3)
    expect(useDocumentStore.getState().past.length).toBe(first.past)
  })

  it.each([
    { name: '空目标', mutate: (svg: SVGSVGElement) => svg.setAttribute('data-tavotto-svg-targets', JSON.stringify({ [textEl.gid]: { transform: [], style: [] } })) },
    { name: '坏镜像', mutate: (svg: SVGSVGElement) => svg.setAttribute('data-tavotto-svg-targets', '{') },
    { name: '目标实际被删除', mutate: (svg: SVGSVGElement) => svg.querySelector('[id="title.left"]')!.remove() },
  ])('$name不能起手，也不能由仍存在的旧主gid旁路', async ({ mutate }) => {
    const first = await firstCommit()
    mockTargetRects()
    const svg = container.querySelector('[data-element-svg="p1"] svg') as SVGSVGElement
    const oldMain = document.createElementNS('http://www.w3.org/2000/svg', 'g')
    oldMain.id = textEl.gid
    vi.spyOn(oldMain, 'getBoundingClientRect').mockReturnValue(hitRect(140))
    svg.appendChild(oldMain)
    mutate(svg)
    pendingDown(150)
    expect(useInteractionStore.getState().kind).toBe('none')
    fire('pointermove', 190, 30)
    fire('pointerup', 190, 30)
    expect(livePanel().overrides).toEqual(first.overrides)
    await authority(3)
    expect(useDocumentStore.getState().past.length).toBe(first.past)
  })

  it('普通SVG没有镜像仍使用原单gid命中与等待权威的提交行为', async () => {
    const first = await firstCommit(MATPLOTLIB_SVG)
    vi.spyOn(titleNode()!, 'getBoundingClientRect').mockReturnValue(hitRect(140))
    pendingDown(150)
    expect(useInteractionStore.getState().kind).toBe('element')
    fire('pointermove', 190, 30)
    fire('pointerup', 190, 30)
    expect(livePanel().overrides).toEqual(first.overrides)
    await authority(3, manifest, `${MATPLOTLIB_SVG}<!-- authority -->`)
    expect(anchor()[0]).toBeCloseTo((first.overrides[0].value as number[])[0] + 40 / layout.width, 8)
    expect(useDocumentStore.getState().past.length).toBe(first.past + 1)
  })
})
