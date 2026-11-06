import { afterEach, describe, expect, it, vi } from 'vitest'
import { offerKeyOf, routeKeyOf, useExportRouteStore } from './exportRouteStore'
import type { ExportRequestInput } from '@/lib/exportRequest'
import { setCurrentProjectId } from '@/lib/session'
import type { PanelChartState, PanelObject } from '@/types/document'
import chartVectors from '../../../tests/golden/chart_model_vectors.json'

const doc = (overrides: unknown[] = []) =>
  ({ page: { w: 100, h: 80 }, objects: [{ id: 'p', type: 'panel', fileId: 'a.png', overrides, x: 0, y: 0, w: 10, h: 10 }] }) as never

const input = (over: Partial<ExportRequestInput> = {}): ExportRequestInput => ({
  scope: 'canvas',
  formats: ['pdf'],
  filename: 'x',
  ppi: 300,
  documentId: 'd',
  doc: doc(),
  ...over,
})

function chartPanel(): PanelObject {
  const vector = chartVectors.vectors.find((v) => v.expect === 'ok')!
  return { id: 'p', type: 'panel', fileId: 'scene.svg', fileKind: 'svg', overrides: [],
    x: 0, y: 0, w: 100, h: 80, nativeW: 100, nativeH: 80,
    chart: structuredClone({ model: vector.model, binding: vector.binding }) as unknown as PanelChartState }
}

function withPanel(panel: PanelObject, scope: 'original' | 'canvas' = 'original'): ExportRequestInput {
  return input({ scope, figureId: panel.fileId, panel, formats: ['pdf', 'eps'],
    doc: { page: { w: 100, h: 80 }, objects: [panel] } as ExportRequestInput['doc'] })
}

afterEach(() => {
  setCurrentProjectId(null)
  vi.unstubAllGlobals()
  useExportRouteStore.getState().clear()
})

describe('routeKeyOf：影响来源的输入变了键就变', () => {
  it('编辑 / 格式 / 范围变键；文件名与 PPI 不变键', () => {
    const k = routeKeyOf(input())
    expect(routeKeyOf(input({ filename: 'other', ppi: 600 }))).toBe(k)
    expect(routeKeyOf(input({ formats: ['pdf', 'eps'] }))).not.toBe(k)
    expect(routeKeyOf(input({ doc: doc([{ gid: 'g', prop: 'p', value: 1 }]) }))).not.toBe(k)
    expect(routeKeyOf(input({ scope: 'original', figureId: 'a.png' }))).not.toBe(k)
  })
  it('缺chart/ref时原有key字节不变', () => {
    expect(routeKeyOf(input())).toBe(JSON.stringify([null, 'canvas', ['pdf'], [['a.png', [], 0, null]]]))
    const p = { ...chartPanel(), chart: undefined }
    expect(routeKeyOf(withPanel(p))).toBe(JSON.stringify([null, 'original', ['pdf','eps'], ['scene.svg', ['scene.svg', [], 0, null]]]))
    expect(routeKeyOf(input({ scope: 'original' }))).toBe(JSON.stringify([null, 'original', ['pdf'], ['', null]]))
  })

  it.each(['original', 'canvas'] as const)('%s 绑定/模型/引用替换均失效；无关文件名与PPI不变', (scope) => {
    const panel = chartPanel()
    const plain = { ...panel, chart: undefined }
    const initial = routeKeyOf(withPanel(plain, scope))
    const bound = routeKeyOf(withPanel(panel, scope))
    expect(bound).not.toBe(initial)
    const source = { schema: 1 as const, sourceId: panel.fileId, bytesSha256: 'a'.repeat(64), sizeBytes: 100,
      sceneDigest: panel.chart!.model.scene_digest }
    const referenced = { ...panel, chartSource: source }
    const key = routeKeyOf(withPanel(referenced, scope))
    expect(key).not.toBe(bound)
    expect(routeKeyOf(input({ ...withPanel(referenced, scope), filename: 'different', ppi: 600 }))).toBe(key)
    expect(routeKeyOf(withPanel({ ...referenced, chartSource: { ...source, bytesSha256: 'b'.repeat(64) } }, scope))).not.toBe(key)
    const edited = structuredClone(referenced)
    edited.chart!.model.axes[0].xlim[1] += 1
    expect(routeKeyOf(withPanel(edited, scope))).not.toBe(key)
    const invalidRef = { ...panel, chartSource: null as unknown as PanelObject['chartSource'] }
    expect(routeKeyOf(withPanel(invalidRef, scope))).not.toBe(bound)
    expect(offerKeyOf(withPanel(plain, scope))).not.toBe(offerKeyOf(withPanel(panel, scope)))
    expect(offerKeyOf(withPanel(referenced, scope))).not.toBe(offerKeyOf(withPanel(panel, scope)))
    expect(offerKeyOf(withPanel(invalidRef, scope))).not.toBe(offerKeyOf(withPanel(panel, scope)))
    expect(offerKeyOf(withPanel(edited, scope))).not.toBe(offerKeyOf(withPanel(referenced, scope)))
  })

  it('原图未发送的不匹配panel.chart不改变键；实际全patch仍进入native身份', () => {
    const panel = chartPanel(), changed = structuredClone(panel)
    changed.chart!.model.axes[0].xlim[1] += 1
    expect(routeKeyOf({ ...withPanel(panel), figureId: 'other.svg' }))
      .toBe(routeKeyOf({ ...withPanel(changed), figureId: 'other.svg' }))
    changed.chart = panel.chart
    changed.overrides = [{ gid: 'axes_0.lines_0', prop: 'color', value: 'red' }]
    expect(routeKeyOf(withPanel(changed))).not.toBe(routeKeyOf(withPanel(panel)))
  })

  it('相同fileId多实例的chart/ref事实保持各自身份', () => {
    const a = chartPanel(), b = { ...structuredClone(a), id: 'p2', chart: undefined }
    const i = withPanel(a, 'canvas')
    i.doc = { ...i.doc, objects: [a, b] }
    const key = routeKeyOf(i)
    expect(routeKeyOf({ ...i, doc: { ...i.doc, objects: [b, a] } })).not.toBe(key)
    expect(routeKeyOf({ ...i, doc: { ...i.doc, objects: [a, { ...b, chart: a.chart }] } })).not.toBe(key)
  })
})

describe('refresh', () => {
  const reply = (body: unknown, ok = true) =>
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(body), { status: ok ? 200 : 500 })))

  it('带回 route，并连同它回答的键存下；键对不上就取不到', async () => {
    const route = { scope: 'canvas', formats: ['pdf', 'eps'], panels: {} }
    reply({ ok: true, route })
    const i = input({ formats: ['pdf', 'eps'] })
    await useExportRouteStore.getState().refresh(i)
    const s = useExportRouteStore.getState()
    expect(s.routeFor(routeKeyOf(i))).toEqual(route)
    expect(s.routeFor(routeKeyOf(input({ formats: ['pdf'] })))).toBeNull()
  })

  it('请求里带的是用户勾的格式（含此刻 EPS 不可用被丢掉的那一个）', async () => {
    reply({ ok: true, route: { scope: 'canvas', formats: [], panels: {} } })
    await useExportRouteStore.getState().refresh(input({ formats: ['pdf', 'eps'] }))
    const body = JSON.parse(String((fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0][1].body))
    expect(body.formats).toEqual(['pdf', 'eps'])
  })

  it('仅formats变化保留同来源offer，格式计划必须失配；来源/范围/项目/clear仍作废', async () => {
    const panel = chartPanel(), initial = { ...withPanel(panel), formats: ['pdf'] }
    const route = { scope: 'original', formats: ['pdf'], panels: { [panel.fileId]: {
      source: 'declarative', embeds_disk_bitmap: false, formats: { pdf: { how: 'convert' } },
      offers: { eps: { how: 'convert' } },
    } } }
    reply({ ok: true, route })
    await useExportRouteStore.getState().refresh(initial)
    const epsSelected = withPanel(panel)
    expect(routeKeyOf(epsSelected)).not.toBe(routeKeyOf(initial))
    expect(offerKeyOf(epsSelected)).toBe(offerKeyOf(initial))
    expect(useExportRouteStore.getState().routeFor(routeKeyOf(epsSelected))).toBeNull()
    const expected = { scope: 'original', eps: { [panel.fileId]: { how: 'convert' } } }
    expect(useExportRouteStore.getState().offersFor(offerKeyOf(epsSelected))).toEqual(expected)
    const edited = structuredClone(panel)
    edited.overrides.push({ gid: 'axes_0.lines_0', prop: 'color', value: 'red' })
    expect(useExportRouteStore.getState().offersFor(offerKeyOf(withPanel(edited)))).toBeNull()
    expect(useExportRouteStore.getState().offersFor(offerKeyOf(withPanel(panel, 'canvas')))).toBeNull()
    setCurrentProjectId('different')
    expect(useExportRouteStore.getState().offersFor(offerKeyOf(epsSelected))).toBeNull()
    setCurrentProjectId(null)
    useExportRouteStore.getState().invalidate()
    expect(useExportRouteStore.getState().offersFor(offerKeyOf(epsSelected))).toBeNull()
    await useExportRouteStore.getState().refresh(initial)
    useExportRouteStore.getState().clear()
    expect(useExportRouteStore.getState().offersFor(offerKeyOf(initial))).toBeNull()
  })

  it('格式刷新在飞时保留已确认offer；失败终局和旧后端缺offer不冒充确认', async () => {
    const panel = chartPanel(), initial = { ...withPanel(panel), formats: ['pdf'] }
    const route = { scope: 'original', formats: ['pdf'], panels: { [panel.fileId]: {
      source: 'declarative', embeds_disk_bitmap: false, formats: {}, offers: { eps: { how: 'convert' } },
    } } }
    reply({ ok: true, route })
    await useExportRouteStore.getState().refresh(initial)
    let release: (r: Response) => void = () => {}
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((r) => { release = r })))
    const pending = useExportRouteStore.getState().refresh(withPanel(panel))
    expect(useExportRouteStore.getState().offersFor(offerKeyOf(initial))).not.toBeNull()
    expect(useExportRouteStore.getState().routeFor(routeKeyOf(withPanel(panel)))).toBeNull()
    release(new Response('{}', { status: 500 }))
    await pending
    expect(useExportRouteStore.getState().offersFor(offerKeyOf(initial))).toBeNull()
  })

  it('旧后端（响应没有 route）/ ok:false / 网络失败：都是「没有 route」，且清掉旧答案', async () => {
    const i = input()
    for (const body of [{ ok: true }, { ok: false, error: { code: 'x' } }]) {
      reply({ ok: true, route: { scope: 'canvas', formats: ['pdf'], panels: {} } })
      await useExportRouteStore.getState().refresh(i)
      expect(useExportRouteStore.getState().routeFor(routeKeyOf(i))).not.toBeNull()
      reply(body)
      await useExportRouteStore.getState().refresh(i)
      expect(useExportRouteStore.getState().routeFor(routeKeyOf(i))).toBeNull()
      expect(useExportRouteStore.getState().offersFor(offerKeyOf(i))).toBeNull()
    }
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('offline') }))
    await useExportRouteStore.getState().refresh(i)
    expect(useExportRouteStore.getState().routeFor(routeKeyOf(i))).toBeNull()
  })

  it('后发的请求赢：慢的旧响应不覆盖新答案', async () => {
    let release: (r: Response) => void = () => {}
    const slow = new Promise<Response>((r) => { release = r })
    const fast = new Response(JSON.stringify({ ok: true, route: { scope: 'canvas', formats: ['pdf'], panels: { new: {} } } }))
    const f = vi.fn().mockReturnValueOnce(slow).mockResolvedValueOnce(fast)
    vi.stubGlobal('fetch', f)
    const first = useExportRouteStore.getState().refresh(input())
    await useExportRouteStore.getState().refresh(input())
    release(new Response(JSON.stringify({ ok: true, route: { scope: 'canvas', formats: ['pdf'], panels: { old: {} } } })))
    await first
    expect(Object.keys(useExportRouteStore.getState().route!.panels)).toEqual(['new'])
  })

  it('未绑定SVG的迟到拒绝不覆盖新绑定的转换offer，validate发送真实chart/ref', async () => {
    const panel = chartPanel()
    panel.chartSource = { schema: 1, sourceId: panel.fileId, bytesSha256: 'a'.repeat(64), sizeBytes: 100,
      sceneDigest: panel.chart!.model.scene_digest }
    const plain = { ...panel, chart: undefined, chartSource: undefined }
    let release: (r: Response) => void = () => {}
    const slow = new Promise<Response>((resolve) => { release = resolve })
    const converted = { scope: 'original', formats: ['pdf','eps'], panels: { [panel.fileId]: {
      source: 'declarative', embeds_disk_bitmap: false, formats: {}, offers: { eps: { how: 'convert' } },
    } } }
    const fetcher = vi.fn().mockReturnValueOnce(slow).mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, route: converted })))
    vi.stubGlobal('fetch', fetcher)
    const first = useExportRouteStore.getState().refresh(withPanel(plain))
    expect(useExportRouteStore.getState().routeFor(routeKeyOf(withPanel(panel)))).toBeNull()
    await useExportRouteStore.getState().refresh(withPanel(panel))
    const sent = JSON.parse(String(fetcher.mock.calls[1][1].body))
    expect(sent.original.chart).toEqual(panel.chart)
    expect(sent.original.chart_source).toEqual(panel.chartSource)
    expect(sent.formats).toEqual(['pdf','eps'])
    release(new Response(JSON.stringify({ ok: true, route: { ...converted, panels: { [panel.fileId]: {
      source: 'disk', formats: {}, offers: { eps: { how: 'unavailable', reason: 'eps_needs_script' } },
    } } } })))
    await first
    expect(useExportRouteStore.getState().routeFor(routeKeyOf(withPanel(panel)))).toEqual(converted)
    expect(useExportRouteStore.getState().offersFor(offerKeyOf(withPanel(panel))))
      .toEqual({ scope: 'original', eps: { [panel.fileId]: { how: 'convert' } } })
    const replacement = { ...panel, chartSource: { ...panel.chartSource, bytesSha256: 'b'.repeat(64) } }
    expect(useExportRouteStore.getState().routeFor(routeKeyOf(withPanel(replacement)))).toBeNull()
    expect(useExportRouteStore.getState().offersFor(offerKeyOf(withPanel(replacement)))).toBeNull()
  })
})

describe('换项目', () => {
  const routeBody = (tag: string) => ({ ok: true, route: { scope: 'canvas', formats: ['pdf'], panels: { [tag]: {} } } })

  it('项目 id 进键：同名文件、同编辑、同格式的两个项目，键不同', () => {
    setCurrentProjectId('A')
    const a = routeKeyOf(input())
    setCurrentProjectId('B')
    expect(routeKeyOf(input())).not.toBe(a)
  })

  it('A 的答案不被 B 复用（即便 store 没被清，键也对不上；清了就更没有）', async () => {
    setCurrentProjectId('A')
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(routeBody('fromA')))))
    await useExportRouteStore.getState().refresh(input())
    setCurrentProjectId('B')
    expect(useExportRouteStore.getState().routeFor(routeKeyOf(input()))).toBeNull()
    useExportRouteStore.getState().clear()
    setCurrentProjectId('A')
    expect(useExportRouteStore.getState().routeFor(routeKeyOf(input()))).toBeNull()
  })

  it('A 发出的请求在切到 B 之后才回来：不落进 B', async () => {
    setCurrentProjectId('A')
    let release: (r: Response) => void = () => {}
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((r) => { release = r })))
    const pending = useExportRouteStore.getState().refresh(input())
    setCurrentProjectId('B')
    useExportRouteStore.getState().clear() // projectStore.resetForNewProject 里做的那一步
    release(new Response(JSON.stringify(routeBody('fromA'))))
    await pending
    expect(useExportRouteStore.getState().route).toBeNull()
    expect(useExportRouteStore.getState().key).toBeNull()
  })

  it('即使没人调 clear，换代也让迟到响应作废（A→B→A 同 id 也已换代）', async () => {
    setCurrentProjectId('A')
    let release: (r: Response) => void = () => {}
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((r) => { release = r })))
    const pending = useExportRouteStore.getState().refresh(input())
    setCurrentProjectId('B')
    setCurrentProjectId('A')
    release(new Response(JSON.stringify(routeBody('fromA'))))
    await pending
    expect(useExportRouteStore.getState().route).toBeNull()
  })
})
