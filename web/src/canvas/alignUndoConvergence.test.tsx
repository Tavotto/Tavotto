/**
 * 对齐 → 撤销 → 乱序返回之后，文档 / SVG / manifest / 选择框收敛到同一状态
 * （issue #131 的用户可见症状：「撤销无法回到正确位置、恢复后画面仍不正确」）。
 *
 * 三条事故链在这里合并验证：
 *   - 对齐渲染完成的那一刻，对齐前那一版被 prune 清掉 → 撤销只剩重渲染，
 *     而重渲染期间画布继续显示对齐后的样子 = 用户眼里「撤销没反应」；
 *   - 对齐的响应晚于撤销的响应回来 → `latest` 被拽回对齐后那一版；
 *   - 权威没就位时照旧画选择框 → 文档在 A、框在 C、SVG 在 B。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import type { EngineRenderOptions, Manifest, ManifestElement } from '@/lib/api'
import { alignSelectedPanelElements } from '@/store/alignAction'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import {
  exactPanelManifest,
  panelDisplayView,
  renderKey,
  renderKeyOf,
  useRenderStore,
} from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'
import { syncEngine } from '@/hooks/useEngineSync'
import { emptyProject, type PanelObject } from '@/types/document'
import { OverlaySvg } from './OverlaySvg'

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

const text = (
  gid: string,
  bbox: [number, number, number, number],
): ManifestElement =>
  ({
    gid,
    role: 'text',
    label: gid,
    bbox,
    editable: [],
    draggable: true,
    anchor: [bbox[0], bbox[1]],
    drag_prop: 'pos_frac',
  }) as ManifestElement

const figureEl = {
  gid: 'figure',
  role: 'figure',
  label: '整图',
  bbox: [0, 0, 1, 1],
  editable: [],
  draggable: false,
} as unknown as ManifestElement

/** 对齐前：t1 在 0.10，t2 在 0.40 */
const MANIFEST_A: Manifest = {
  stem: 'f1',
  size_mm: [100, 80],
  elements: [figureEl, text('t1', [0.10, 0.10, 0.2, 0.05]), text('t2', [0.40, 0.30, 0.2, 0.05])],
}
/** 对齐后：t2 也到了 0.10 */
const MANIFEST_C: Manifest = {
  stem: 'f1',
  size_mm: [100, 80],
  elements: [figureEl, text('t1', [0.10, 0.10, 0.2, 0.05]), text('t2', [0.10, 0.30, 0.2, 0.05])],
}

const panel = (): PanelObject =>
  ({
    id: 'p1',
    type: 'panel',
    x: 0,
    y: 0,
    w: 100,
    h: 80,
    fileId: 'f1',
    fileKind: 'pdf',
    nativeW: 100,
    nativeH: 80,
    script: 'fig.py',
    overrides: [],
  }) as unknown as PanelObject

const doc = () => useDocumentStore.getState().doc
const livePanel = () => doc().objects.find((o) => o.id === 'p1') as PanelObject

let container: HTMLDivElement
let root: Root
/** 选择框：ElementBoxes 画的带 fill-opacity 的矩形 */
const boxes = () => container.querySelectorAll('rect[fill-opacity]')

beforeEach(async () => {
  localStorage.clear()
  engineRender.mockReset()
  useRenderStore.getState().clear()
  useViewportStore.setState({
    zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700,
  })
  useUiStore.setState({
    tool: 'select', snapEnabled: false, elementPanelId: 'p1',
    cropTargetId: null, selectedGids: [],
  })
  useSelectionStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_conv')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(panel())
  })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  useInteractionStore.getState().end()
})

const draw = () => act(() => root.render(<OverlaySvg />))

/** 精确渲染 A，并把它设成当前权威 */
async function renderA() {
  engineRender.mockResolvedValue({ rev: 1, manifest: MANIFEST_A, svg: '<svg>A</svg>' })
  await useRenderStore.getState().render('f1', [])
}

describe('对齐 → 撤销：文档、画面、权威、选择框收敛到同一版', () => {
  it('撤销后立刻复用缓存里的精确旧变体，不用白跑一次引擎', async () => {
    await renderA()
    useUiStore.setState({ selectedGids: ['t1', 't2'] })

    // 对齐 → C，并把 C 也画出来
    expect(alignSelectedPanelElements('p1', 'left').ok).toBe(true)
    const afterAlign = structuredClone(livePanel().overrides)
    expect(afterAlign).toHaveLength(1)
    engineRender.mockResolvedValue({ rev: 2, manifest: MANIFEST_C, svg: '<svg>C</svg>' })
    await useRenderStore.getState().render('f1', afterAlign)

    // 同步器跑一轮：文档现在只挂着 C 这一个变体
    syncEngine(doc().objects, 'p1')
    engineRender.mockClear()

    // 撤销
    useDocumentStore.getState().undo()
    expect(livePanel().overrides).toEqual([])

    // A 那一格必须还在（recent 缓存），且当场就是权威
    const st = useRenderStore.getState()
    expect(exactPanelManifest(st, livePanel())).toBe(MANIFEST_A)
    const view = panelDisplayView(st, livePanel())
    expect(view.kind).toBe('exact')
    expect(view.svg).toContain('A')

    // 同步器不该为这一版再发一次渲染——它已经画过了
    syncEngine(doc().objects, 'p1')
    expect(engineRender).not.toHaveBeenCalled()
  })

  it('对齐的响应晚于撤销的响应回来，最终仍然停在 A', async () => {
    await renderA()
    useUiStore.setState({ selectedGids: ['t1', 't2'] })
    expect(alignSelectedPanelElements('p1', 'left').ok).toBe(true)
    const cPatches = structuredClone(livePanel().overrides)

    // C 的渲染挂在半空
    const gate: Record<string, (v: unknown) => void> = {}
    engineRender.mockImplementation(
      (_id: string, patches: unknown[]) =>
        new Promise((resolve) => {
          gate[JSON.stringify(patches)] = resolve as (v: unknown) => void
        }),
    )
    const pc = useRenderStore.getState().render('f1', cPatches)

    // 用户等不及，直接撤销；A 的渲染随后发出并先回来
    useDocumentStore.getState().undo()
    const pa = useRenderStore.getState().render('f1', [])
    gate['[]']({ rev: 3, manifest: MANIFEST_A, svg: '<svg>A2</svg>' })
    await pa

    // C 姗姗来迟
    gate[JSON.stringify(cPatches)]({ rev: 2, manifest: MANIFEST_C, svg: '<svg>C</svg>' })
    await pc

    const st = useRenderStore.getState()
    // 文档是 A
    expect(livePanel().overrides).toEqual([])
    // 权威是 A 那一格，manifest 是 A 的
    expect(exactPanelManifest(st, livePanel())).toBe(MANIFEST_A)
    // 显示也是 A —— 晚到的 C 只入库，没把 latest 拽回去
    const view = panelDisplayView(st, livePanel())
    expect(view.kind).toBe('exact')
    expect(view.svg).toContain('A2')
    expect(st.latest.f1).toBe(renderKey('f1', []))
    // C 的结果照旧留着（同文件的另一个副本可能还等着它）
    expect(st.byKey[renderKey('f1', cPatches)]?.manifest).toBe(MANIFEST_C)
  })
})

describe('选择框：权威没就位时一个都不画，就位后回到精确位置', () => {
  it('对齐 → 撤销全程 selectedGids 不丢', async () => {
    await renderA()
    useUiStore.setState({ selectedGids: ['t1', 't2'] })
    alignSelectedPanelElements('p1', 'left')
    expect(useUiStore.getState().selectedGids).toEqual(['t1', 't2'])
    useDocumentStore.getState().undo()
    expect(useUiStore.getState().selectedGids).toEqual(['t1', 't2'])
  })

  it('新变体还在渲染时不画框（绝不画在上一版的位置上）', async () => {
    await renderA()
    useUiStore.setState({ selectedGids: ['t2'] })
    draw()
    expect(boxes().length).toBeGreaterThan(0) // 权威在，框在

    // 改一个值：变体键变了，新那一版还没画出来
    useDocumentStore.getState().commit(literal('改字号'), (d) => {
      const p = d.objects.find((o) => o.id === 'p1') as PanelObject
      p.overrides.push({ gid: 't2', prop: 'fontsize', value: 22 })
    })
    draw()
    // 画布仍显示上一张（fallback），但一个选择框都不画
    expect(panelDisplayView(useRenderStore.getState(), livePanel()).kind).toBe('fallback')
    expect(boxes().length).toBe(0)
    // 选区没被清掉
    expect(useUiStore.getState().selectedGids).toEqual(['t2'])
  })

  it('精确渲染回来之后框自己复位到新位置', async () => {
    await renderA()
    useUiStore.setState({ selectedGids: ['t2'] })
    useDocumentStore.getState().commit(literal('改字号'), (d) => {
      const p = d.objects.find((o) => o.id === 'p1') as PanelObject
      p.overrides.push({ gid: 't2', prop: 'fontsize', value: 22 })
    })
    draw()
    expect(boxes().length).toBe(0)

    engineRender.mockResolvedValue({ rev: 2, manifest: MANIFEST_C, svg: '<svg>C</svg>' })
    await act(async () => {
      await useRenderStore.getState().render('f1', livePanel().overrides)
    })
    draw()

    expect(useRenderStore.getState().byKey[renderKeyOf(livePanel())]?.manifest).toBe(MANIFEST_C)
    expect(boxes().length).toBeGreaterThan(0)
  })
})
