/**
 * 显示回退与几何权威的分家（issue #131）。
 *
 * 事故形状：面板改了字号 → 变体键当场变成 B，B 的渲染还没回来，
 * `panelRender` 按设计退回 `latest[fileId]`（A 的 manifest）让画布别闪白。
 * 那一步是对的；错的是**几何写操作也读同一个对象**——对齐拿 A 的墨迹 bbox
 * 配 B 的锚点算落点，算出来的位置对不上任何一个变体。
 *
 * 这里钉的是判据本身：
 *   显示可以退回；**权威只认 `byKey[renderKeyOf(panel)]` 这一格，且必须与
 *   当前 overrides 逐字对应**。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { EngineRenderOptions, Manifest, ManifestElement } from '@/lib/api'
import {
  exactPanelManifest,
  exactPanelRender,
  panelDisplayView,
  panelRender,
  renderKey,
  renderKeyOf,
  useRenderStore,
} from './renderStore'
import type { PanelObject } from '@/types/document'

const engineRender = vi.fn()

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: (id: string, patches: unknown[], opts?: EngineRenderOptions) =>
    engineRender(id, patches, opts),
}))

/** 一个可拖动文字：anchor + drag_prop 才进得了对齐条目 */
const text = (gid: string, bbox: [number, number, number, number]): ManifestElement =>
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

const manifest = (stem: string, elements: ManifestElement[] = []): Manifest => ({
  stem,
  size_mm: [80, 60],
  elements,
})

const panel = (id: string, fileId: string, overrides: unknown[] = []): PanelObject =>
  ({
    id,
    type: 'panel',
    x: 0,
    y: 0,
    w: 40,
    h: 30,
    fileId,
    fileKind: 'pdf',
    nativeW: 40,
    nativeH: 30,
    script: 'fig.py',
    overrides,
  }) as unknown as PanelObject

const FONT_A = [{ gid: 'axes_0.title', prop: 'fontsize', value: 8 }]
const FONT_B = [{ gid: 'axes_0.title', prop: 'fontsize', value: 22 }]

beforeEach(() => {
  engineRender.mockReset()
  useRenderStore.getState().clear()
})

/** A 已经画好；面板已经改成 B，B 还在路上 */
async function renderedAThenMovedToB() {
  engineRender.mockResolvedValue({
    rev: 2,
    // A：8pt 的标题，墨迹窄
    manifest: manifest('Fig1', [text('axes_0.title', [0.30, 0.10, 0.10, 0.04])]),
    svg: '<svg>A</svg>',
  })
  await useRenderStore.getState().render('Fig1.pdf', FONT_A)

  const b = panel('p1', 'Fig1.pdf', FONT_B)
  useRenderStore.getState().patch(renderKeyOf(b), {
    fileId: 'Fig1.pdf',
    wantPatches: JSON.stringify(FONT_B),
    status: 'rendering',
  })
  return b
}

describe('exactPanelRender：只认自己那一格，且必须对得上当前 overrides', () => {
  it('B 还没画出来时权威为空——哪怕 A 的 manifest 就在隔壁', async () => {
    const b = await renderedAThenMovedToB()
    const st = useRenderStore.getState()

    // 显示层照旧退回 A，画布不闪白（这条行为必须保住）
    expect(panelRender(st, b)?.manifest?.stem).toBe('Fig1')
    expect(panelRender(st, b)?.svg).toContain('A')

    // 几何权威必须为空
    expect(exactPanelRender(st, b)).toBeNull()
    expect(exactPanelManifest(st, b)).toBeNull()
  })

  it('B 画回来之后权威就位，且正是 B 那一格', async () => {
    const b = await renderedAThenMovedToB()
    engineRender.mockResolvedValue({
      rev: 3,
      manifest: manifest('Fig1', [text('axes_0.title', [0.22, 0.08, 0.26, 0.09])]),
      svg: '<svg>B</svg>',
    })
    await useRenderStore.getState().render('Fig1.pdf', FONT_B)

    const st = useRenderStore.getState()
    const exact = exactPanelRender(st, b)
    expect(exact).not.toBeNull()
    expect(st.byKey[renderKeyOf(b)]).toBe(exact)
    // 权威的 bbox 是 B 的宽墨迹，不是 A 的窄墨迹
    expect(exactPanelManifest(st, b)?.elements[0].bbox[2]).toBeCloseTo(0.26, 6)
  })

  it('脚本改过（markStale）之后，曾经成功的那份 manifest 不得重新当权威', async () => {
    engineRender.mockResolvedValue({
      rev: 1,
      manifest: manifest('Fig1', [text('axes_0.title', [0.3, 0.1, 0.1, 0.04])]),
      svg: '<svg>A</svg>',
    })
    await useRenderStore.getState().render('Fig1.pdf', FONT_A)
    const p = panel('p1', 'Fig1.pdf', FONT_A)
    expect(exactPanelRender(useRenderStore.getState(), p)).not.toBeNull()

    useRenderStore.getState().markStale(['Fig1.pdf'])

    const st = useRenderStore.getState()
    // 显示还能接着用旧图（stale 角标由别处表达）
    expect(panelRender(st, p)?.svg).toContain('A')
    // 但它已经不是权威了：脚本变了，墨迹框可能整个不一样
    expect(exactPanelRender(st, p)).toBeNull()
  })

  it('只登记了 wantPatches、还没有任何成功结果时权威为空', () => {
    const p = panel('p1', 'Fig1.pdf', FONT_B)
    useRenderStore.getState().patch(renderKeyOf(p), {
      fileId: 'Fig1.pdf',
      wantPatches: JSON.stringify(FONT_B),
      status: 'rendering',
    })
    expect(exactPanelRender(useRenderStore.getState(), p)).toBeNull()
  })
})

describe('同一文件的两个副本：显示可以互相垫底，几何绝不互相借用', () => {
  it('panel A 的权威为空时不得借 panel B 的 manifest', async () => {
    // 副本 B 先画好，latest[fileId] 因此指向 B 的变体
    engineRender.mockResolvedValue({
      rev: 1,
      manifest: manifest('Fig1', [text('axes_0.title', [0.60, 0.50, 0.30, 0.10])]),
      svg: '<svg>copyB</svg>',
    })
    await useRenderStore.getState().render('Fig1.pdf', FONT_B)

    // 副本 A 的变体还在路上
    const copyA = panel('pA', 'Fig1.pdf', FONT_A)
    useRenderStore.getState().patch(renderKeyOf(copyA), {
      fileId: 'Fig1.pdf',
      wantPatches: JSON.stringify(FONT_A),
      status: 'rendering',
    })

    const st = useRenderStore.getState()
    expect(st.latest['Fig1.pdf']).toBe(renderKey('Fig1.pdf', FONT_B))

    // 显示层可以临时挂 B 的图（产品选择保留这个回退）
    const view = panelDisplayView(st, copyA)
    expect(view.kind).toBe('fallback')
    expect(view.svg).toContain('copyB')
    // 但来源必须说清楚，且**没有 manifest 可用**
    expect(view.sourceKey).toBe(renderKey('Fig1.pdf', FONT_B))
    expect(view.currentKey).toBe(renderKeyOf(copyA))
    if (view.kind === 'fallback') expect(view.manifest).toBeUndefined()

    // 几何权威：空。B 的 bbox 不许流进 A 的写路径
    expect(exactPanelRender(st, copyA)).toBeNull()
  })

  it('两个副本各自画好后，各自的权威是各自那一格', async () => {
    engineRender.mockImplementation(async (_id: string, patches: { value: number }[]) => ({
      rev: 1,
      manifest: manifest('Fig1', [
        text('axes_0.title', [patches[0].value / 100, 0.1, 0.1, 0.04]),
      ]),
      svg: `<svg>${patches[0].value}</svg>`,
    }))
    await useRenderStore.getState().render('Fig1.pdf', FONT_A)
    await useRenderStore.getState().render('Fig1.pdf', FONT_B)

    const st = useRenderStore.getState()
    const copyA = panel('pA', 'Fig1.pdf', FONT_A)
    const copyB = panel('pB', 'Fig1.pdf', FONT_B)
    expect(exactPanelManifest(st, copyA)?.elements[0].bbox[0]).toBeCloseTo(0.08, 6)
    expect(exactPanelManifest(st, copyB)?.elements[0].bbox[0]).toBeCloseTo(0.22, 6)
  })
})

describe('panelDisplayView：来源永远说得出口', () => {
  it('自己那份画好了就是 exact，来源键等于当前键', async () => {
    engineRender.mockResolvedValue({ rev: 1, manifest: manifest('Fig1'), svg: '<svg>own</svg>' })
    await useRenderStore.getState().render('Fig1.pdf', FONT_A)
    const p = panel('p1', 'Fig1.pdf', FONT_A)
    const view = panelDisplayView(useRenderStore.getState(), p)
    expect(view.kind).toBe('exact')
    expect(view.sourceKey).toBe(view.currentKey)
  })

  it('一张图都还没有时是 empty，不编造来源', () => {
    const p = panel('p1', 'Fig1.pdf', FONT_A)
    const view = panelDisplayView(useRenderStore.getState(), p)
    expect(view.kind).toBe('empty')
    expect(view.svg).toBeNull()
    expect(view.sourceKey).toBeNull()
  })
})

describe('乱序返回：晚到的旧变体不得把显示拽回去', () => {
  it('先发的 C 晚于后发的 A 返回时，latest 仍然停在 A', async () => {
    const A = [{ gid: 'g', prop: 'x', value: 1 }]
    const C = [{ gid: 'g', prop: 'x', value: 3 }]
    const gate: Record<string, (v: unknown) => void> = {}
    engineRender.mockImplementation(
      (_id: string, patches: { value: number }[]) =>
        new Promise((resolve) => {
          gate[`v${patches[0].value}`] = resolve as (v: unknown) => void
        }),
    )

    // C 先发出
    const pc = useRenderStore.getState().render('Fig1.pdf', C)
    // A 后发出
    const pa = useRenderStore.getState().render('Fig1.pdf', A)

    // A 先回来（撤销之后重渲染的那一版）
    gate.v1({ rev: 5, manifest: manifest('Fig1-A'), svg: '<svg>A</svg>' })
    await pa
    expect(useRenderStore.getState().latest['Fig1.pdf']).toBe(renderKey('Fig1.pdf', A))

    // C 姗姗来迟：入库可以（同文件的另一个副本可能还等着它），
    // 但绝不能把文件级的「最近那张」拽回旧变体
    gate.v3({ rev: 4, manifest: manifest('Fig1-C'), svg: '<svg>C</svg>' })
    await pc
    const st = useRenderStore.getState()
    expect(st.byKey[renderKey('Fig1.pdf', C)]?.manifest?.stem).toBe('Fig1-C')
    expect(st.latest['Fig1.pdf']).toBe(renderKey('Fig1.pdf', A))
  })
})

describe('有界的近期变体保留：撤销回到刚才那一版不用白跑引擎', () => {
  it('刚画好的上一版在变体不再被引用后仍留在缓存里', async () => {
    engineRender.mockImplementation(async (_id: string, patches: { value: number }[]) => ({
      rev: 1,
      manifest: manifest(`Fig1-${patches[0].value}`),
      svg: `<svg>${patches[0].value}</svg>`,
    }))
    await useRenderStore.getState().render('Fig1.pdf', FONT_A)
    await useRenderStore.getState().render('Fig1.pdf', FONT_B)

    // 文档现在只挂着 B 这一个变体
    useRenderStore.getState().prune(new Set([renderKey('Fig1.pdf', FONT_B)]))

    // 撤销会回到 A：那一格必须还在，且立刻就是权威
    const back = panel('p1', 'Fig1.pdf', FONT_A)
    const st = useRenderStore.getState()
    expect(st.byKey[renderKeyOf(back)]).toBeDefined()
    expect(exactPanelManifest(st, back)?.stem).toBe('Fig1-8')
  })

  it('保留是有上限的：超出档数的老变体照旧清掉', async () => {
    engineRender.mockImplementation(async (_id: string, patches: { value: number }[]) => ({
      rev: 1,
      manifest: manifest(`Fig1-${patches[0].value}`),
      svg: `<svg>${'x'.repeat(64)}</svg>`,
    }))
    const variants = Array.from({ length: 12 }, (_, i) => [
      { gid: 'g', prop: 'fontsize', value: i },
    ])
    for (const v of variants) await useRenderStore.getState().render('Fig1.pdf', v)

    const live = new Set([renderKey('Fig1.pdf', variants[11])])
    useRenderStore.getState().prune(live)

    const kept = Object.keys(useRenderStore.getState().byKey)
    expect(kept.length).toBeLessThanOrEqual(4)
    // 留下的必须是最近的那几个，而不是最早的
    expect(kept).toContain(renderKey('Fig1.pdf', variants[11]))
    expect(kept).not.toContain(renderKey('Fig1.pdf', variants[0]))
  })

  it('reset / clear 之后缓存一并释放，不留跨文件残影', async () => {
    engineRender.mockResolvedValue({ rev: 1, manifest: manifest('Fig1'), svg: '<svg/>' })
    await useRenderStore.getState().render('Fig1.pdf', FONT_A)
    await useRenderStore.getState().render('Fig1.pdf', FONT_B)
    useRenderStore.getState().reset('Fig1.pdf')
    useRenderStore.getState().prune(new Set())
    expect(Object.keys(useRenderStore.getState().byKey)).toHaveLength(0)
  })
})
