/**
 * 点选误差线换 SD / SEM / 95% CI（`figureOriginStore` + 宿主 + 属性页那一行 `ErrorKindRow`，ADR 0178）。
 * 主语：**这一块面板**（同一个 id）上画的是哪个脚本的图；换成功 = 面板的 `fileId` 变成新脚本的图、旧脚本不删、一条历史；
 * 其它任何结局（认不回画法 / 换了项目 / 图被改过 / 被删）= 面板与旧脚本一个字节不变。
 * 数据页的草稿（`dataFigureStore` 的 `session` / `candidates`）在整个过程里不被碰。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TooltipProvider } from '@/components/ui/Tooltip'
import {
  aiDataFigureCommit,
  aiDataFigureDiscard,
  aiDataFigurePropose,
  type AiDataCommitResult,
  type AiDataProposeItem,
  type CapturedFigureDescriptor,
  type RuntimeAssetInfo,
} from '@/lib/api'
import { ErrorKindRow } from '@/components/inspector/ErrorKindRow'
import { makeOrigin, type FigureOrigin } from '@/lib/dataFigureOrigin'
import { setCurrentProjectId } from '@/lib/session'
import '@/lib/dataFigureHost'
import { addRuntimePanel } from '@/store/actions'
import { useDataFigureStore } from '@/store/dataFigureStore'
import { useDocumentStore } from '@/store/documentStore'
import { useFigureOriginStore } from '@/store/figureOriginStore'
import { errorBarSelectPending, cancelErrorBarSelect } from '@/store/figureSwitchSelect'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { useScriptRunStore, type ScriptRunState } from '@/store/scriptRunStore'
import { useTafFlagStore } from '@/store/tafFlagStore'
import { emptyProject, type PanelObject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  aiDataFigurePropose: vi.fn(),
  aiDataFigureCommit: vi.fn(),
  aiDataFigureDiscard: vi.fn(async () => ({ ok: true })),
}))
const refresh = vi.hoisted(() => vi.fn(async () => {}))
vi.mock('@/store/liveSync', () => ({ refreshProjectNow: refresh }))
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

const propose = vi.mocked(aiDataFigurePropose)
const commit = vi.mocked(aiDataFigureCommit)
const discard = vi.mocked(aiDataFigureDiscard)

const SCRIPT1 = 'tavotto_figures/a.py'
const SCRIPT2 = 'tavotto_figures/a_2.py'
const desc = (script: string, id: string, fp: string): CapturedFigureDescriptor => ({
  asset_id: id,
  script,
  entry: script,
  stem: id,
  capture_source: 'savefig',
  execution_profile: 'safe',
  original_artifact: null,
  size_mm: [80, 60],
  source_fingerprint: fp,
  can_writeback_artifact: false,
  can_writeback_source: false,
})
const D1 = desc(SCRIPT1, 'runtime:a1', 'F1')
const D2 = desc(SCRIPT2, 'runtime:a2', 'F2')
const asset = (d: CapturedFigureDescriptor, status: RuntimeAssetInfo['status'] = 'fresh'): RuntimeAssetInfo => ({
  id: d.asset_id, script: d.script, stem: d.stem, entry: d.entry, status, cached: true, size_mm: d.size_mm, capture_source: 'savefig', descriptor: d,
})

const cand = (over: Partial<AiDataProposeItem> = {}): AiDataProposeItem => ({
  id: 'i1', title_code: 'points_mean', why_code: 'w', recommended: true, unplotted: { count: 0, code: null }, thumbnail_url: '/t/1', thumbnail_status: 'pending',
  error: 'sd', error_source: 'computed', ...over,
})
const answers = { y: 'resp', purpose: 'compare_groups' }
const baseOrigin = (over: Partial<Parameters<typeof makeOrigin>[0]> = {}): FigureOrigin =>
  makeOrigin({ script: SCRIPT1, item: cand(), file: { path: 'a.csv', sheet: null }, grouped: false, headerRow: 2, answers, spec: { width_single_mm: 85 }, fontRange: { min_pt: 6 }, pj: 'pa', ...over })!

const doc = () => useDocumentStore.getState().doc
const panels = () => doc().objects.filter((o): o is PanelObject => o.type === 'panel')
const runResult = (d: CapturedFigureDescriptor) =>
  vi.fn(async (script: string) => {
    useScriptRunStore.setState((s) => ({ byScript: { ...s.byScript, [script]: { phase: 'captured_one', descriptors: [d] } as unknown as ScriptRunState } }))
  })
const flush = async () => act(async () => { await new Promise((r) => setTimeout(r, 0)) })

let root: Root
let hostEl: HTMLDivElement
async function mountRow(panel: PanelObject) {
  hostEl = document.createElement('div')
  document.body.appendChild(hostEl)
  root = createRoot(hostEl)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <ErrorKindRow panel={panel} />
      </TooltipProvider>,
    )
  })
}
const row = () => document.querySelector('[data-error-kind]')

beforeEach(async () => {
  for (const f of [propose, commit, discard, refresh]) f.mockReset()
  discard.mockResolvedValue({ ok: true })
  refresh.mockResolvedValue(undefined)
  setCurrentProjectId('pa')
  useDataFigureStore.getState().clear()
  useFigureOriginStore.getState().clear()
  cancelErrorBarSelect()
  useRenderStore.getState().clear()
  useTafFlagStore.setState({ enabled: true })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_switch')
  addRuntimePanel(D1)
  useRuntimeAssetStore.setState({ assets: [asset(D1)] })
  useScriptRunStore.setState({ byScript: {}, run: runResult(D2) })
  // 换类型的响应：同一种画法、误差类型是点选的那一种
  propose.mockImplementation(async (body) => ({
    kind: 'candidates', session: 'SW', dropped: [], render: 'lazy',
    items: [cand({ id: 'k1', error: ((body.answers ?? {}) as { error?: 'sd' | 'sem' | 'ci95' }).error ?? 'sd' })],
  }) as never)
  commit.mockResolvedValue({ task: { outcome: 'completed' }, script: SCRIPT2, stem: 'a_2', data_check: 'verified' } as AiDataCommitResult)
})
afterEach(() => {
  act(() => root?.unmount())
  hostEl?.remove()
  document.body.innerHTML = ''
})

const register = (o: FigureOrigin = baseOrigin()) => useFigureOriginStore.getState().register({ ...o, fingerprint: 'F1' })

describe('哪些图登记成「可换误差类型」', () => {
  it('算出来的 SD / SEM / 95% CI、单文件：登记；取自现成误差列 / 合画 / 没有误差线 / 种类说不清：不登记', () => {
    expect(baseOrigin()?.kind).toBe('sd')
    expect(baseOrigin({ item: cand({ error: 'sem' }) }).kind).toBe('sem')
    expect(makeOrigin({ script: 's.py', item: cand({ error_source: 'column' }), file: { path: 'a.csv', sheet: null }, grouped: false, headerRow: null, answers: {}, spec: null, fontRange: null, pj: 'pa' })).toBeNull()
    expect(makeOrigin({ script: 's.py', item: cand(), file: { path: 'a.csv', sheet: null }, grouped: true, headerRow: null, answers: {}, spec: null, fontRange: null, pj: 'pa' })).toBeNull()
    expect(makeOrigin({ script: 's.py', item: cand({ error: undefined, error_source: undefined }), file: { path: 'a.csv', sheet: null }, grouped: false, headerRow: null, answers: {}, spec: null, fontRange: null, pj: 'pa' })).toBeNull()
    expect(makeOrigin({ script: 's.py', item: cand({ error: 'unknown' }), file: { path: 'a.csv', sheet: null }, grouped: false, headerRow: null, answers: {}, spec: null, fontRange: null, pj: 'pa' })).toBeNull()
  })

  it('数据页提交成功：来历按脚本路径记下（candidates 清掉之前）；合画 / 现成误差列的不记', async () => {
    const { aiDataOverview } = await import('@/lib/api')
    void aiDataOverview
    const st = useDataFigureStore
    st.setState({
      selected: { path: 'a.csv', sheet: null }, group: null, session: 'SX', phase: 'ready', answers: { y: 'resp' }, headerRows: { 'a.csv': 3 },
      candidates: [{ ...cand(), thumb: '/t' }],
      thumbs: { i1: 'ready' },
    })
    commit.mockResolvedValue({ task: { outcome: 'completed' }, script: 'tavotto_figures/n.py', stem: 'n', data_check: 'verified' } as AiDataCommitResult)
    await st.getState().draw('i1')
    await flush()
    const o = useFigureOriginStore.getState().figures['tavotto_figures/n.py']
    expect(o).toMatchObject({ kind: 'sd', file: { path: 'a.csv', sheet: null }, headerRow: 3, answers: { y: 'resp' }, fingerprint: null })
    // 现成误差列的
    st.setState({ selected: { path: 'a.csv', sheet: null }, group: null, session: 'SY', phase: 'ready', drawingId: null, candidates: [{ ...cand({ error_source: 'column' }), thumb: '/t' }], thumbs: { i1: 'ready' } })
    commit.mockResolvedValue({ task: { outcome: 'completed' }, script: 'tavotto_figures/m.py', stem: 'm', data_check: 'verified' } as AiDataCommitResult)
    await st.getState().draw('i1')
    await flush()
    expect(useFigureOriginStore.getState().figures['tavotto_figures/m.py']).toBeUndefined()
  })

  it('面板第一次出现在文档里：记下它的来源指纹；之后脚本变了也不改记录', async () => {
    useFigureOriginStore.getState().register(baseOrigin())
    expect(useFigureOriginStore.getState().figures[SCRIPT1]?.fingerprint).toBe('F1') // 文档里已有面板：登记后立即对上
    useFigureOriginStore.getState().bindFingerprint(SCRIPT1, 'ZZ')
    expect(useFigureOriginStore.getState().figures[SCRIPT1]?.fingerprint).toBe('F1')
  })
})

describe('属性页那一行：出现条件', () => {
  it('算的、没改过：出现，当前值是登记的类型', async () => {
    register()
    await mountRow(panels()[0]!)
    expect(row()).not.toBeNull()
    expect(row()!.textContent).toContain('标准差')
  })

  it('开关关着：不出现', async () => {
    register()
    useTafFlagStore.setState({ enabled: false })
    await mountRow(panels()[0]!)
    expect(row()).toBeNull()
  })

  it('没有来历（现成误差列 / 合画 / 不是数据页画的图都不会有）：不出现', async () => {
    await mountRow(panels()[0]!)
    expect(row()).toBeNull()
  })

  it('改过：任何一条用户手改的 override、基线、脚本变了（指纹 / 状态）都不出现', async () => {
    register()
    const p = panels()[0]!
    // ① 用户手改
    useDocumentStore.getState().commit({ key: 'x' } as never, (d) => {
      ;(d.objects.find((o) => o.id === p.id) as PanelObject).overrides = [{ gid: 'axes_0.errorbar_1', prop: 'linewidth', value: 2 } as never]
    })
    await mountRow(panels()[0]!)
    expect(row()).toBeNull()
    act(() => root.unmount())
    hostEl.remove()
    // ② 手改清掉了，但脚本被写回改过（overrides 为空）：素材清单的指纹变了
    useDocumentStore.getState().commit({ key: 'x' } as never, (d) => {
      ;(d.objects.find((o) => o.id === p.id) as PanelObject).overrides = []
    })
    useRuntimeAssetStore.setState({ assets: [asset({ ...D1, source_fingerprint: 'CHANGED' })] })
    await mountRow(panels()[0]!)
    expect(row()).toBeNull()
    act(() => root.unmount())
    hostEl.remove()
    // ③ 素材不是「新鲜」的（脚本在磁盘上被改过）
    useRuntimeAssetStore.setState({ assets: [asset(D1, 'possibly_stale')] })
    await mountRow(panels()[0]!)
    expect(row()).toBeNull()
  })

  it('移动 / 缩放整张图不算改过', async () => {
    register()
    const p = panels()[0]!
    useDocumentStore.getState().commit({ key: 'x' } as never, (d) => {
      const o = d.objects.find((x) => x.id === p.id)!
      o.x += 10
      o.w *= 1.5
      o.h *= 1.5
    })
    await mountRow(panels()[0]!)
    expect(row()).not.toBeNull()
  })

  it('绑定了样式的画布上，样式自己写的 override 不算手改；手改过的算', async () => {
    register()
    const p = panels()[0]!
    const key = `${p.id}@${p.fileId}`
    useDocumentStore.getState().commit({ key: 'x' } as never, (d) => {
      ;(d.objects.find((o) => o.id === p.id) as PanelObject).overrides = [{ gid: 'g', prop: 'fontsize', value: 7 } as never]
      d.style = { id: 's', snapshot: {} as never, owned: { [key]: { g: { fontsize: { value: 7, base: 9 } } } } } as never
    })
    await mountRow(panels()[0]!)
    expect(row()).not.toBeNull()
    act(() => root.unmount())
    hostEl.remove()
    // 用户把它改成别的值：不再是样式写的
    useDocumentStore.getState().commit({ key: 'x' } as never, (d) => {
      ;(d.objects.find((o) => o.id === p.id) as PanelObject).overrides = [{ gid: 'g', prop: 'fontsize', value: 8 } as never]
    })
    await mountRow(panels()[0]!)
    expect(row()).toBeNull()
  })
})

describe('换类型', () => {
  it('成功：同一块面板（id / 位置 / 尺寸框不变）换成新脚本的图、旧脚本不删、一条历史；请求只改 error，其余照旧', async () => {
    register()
    const before = panels()[0]!
    const past = useDocumentStore.getState().past.length
    await useFigureOriginStore.getState().switchKind(SCRIPT1, 'sem')
    const after = panels()
    expect(after).toHaveLength(1)
    expect(after[0]).toMatchObject({ id: before.id, fileId: 'runtime:a2', script: SCRIPT2, x: before.x, y: before.y, w: before.w, h: before.h, overrides: [] })
    expect(after[0]!.source?.fingerprint).toBe('F2')
    expect(useDocumentStore.getState().past.length).toBe(past + 1)
    // 请求：同一张表、同一份 answers 只换 error、表头行 / 规范 / 字号范围原样
    expect(propose).toHaveBeenCalledTimes(1)
    expect(propose.mock.calls[0]![0]).toMatchObject({
      table: 'a.csv', render: 'lazy', answers: { y: 'resp', purpose: 'compare_groups', error: 'sem' }, header_rows: { 'a.csv': 2 }, spec: { width_single_mm: 85 }, font_range: { min_pt: 6 },
    })
    // 新的一批会话用完就放弃；旧脚本的来历还在、新脚本有自己的来历（可以再换回去）
    expect(discard).toHaveBeenCalledWith('SW', 'pa')
    const o = useFigureOriginStore.getState().figures
    expect(o[SCRIPT1]).toMatchObject({ kind: 'sd', fingerprint: 'F1' })
    expect(o[SCRIPT2]).toMatchObject({ kind: 'sem', fingerprint: 'F2' })
    expect(useFigureOriginStore.getState().switching).toBeNull()
  })

  it('撤销回到旧图（同一块面板、旧的 fileId）', async () => {
    register()
    const id = panels()[0]!.id
    await useFigureOriginStore.getState().switchKind(SCRIPT1, 'ci95')
    expect(panels()[0]!.fileId).toBe('runtime:a2')
    useDocumentStore.getState().undo()
    expect(panels()[0]).toMatchObject({ id, fileId: 'runtime:a1', script: SCRIPT1 })
    useDocumentStore.getState().redo()
    expect(panels()[0]).toMatchObject({ id, fileId: 'runtime:a2' })
  })

  it('认不回同一种画法 / 新一批里不是点选的那种误差：不换，静默', async () => {
    register()
    propose.mockResolvedValue({ kind: 'candidates', session: 'SW', dropped: [], render: 'lazy', items: [cand({ title_code: 'other_drawing', error: 'sem' })] } as never)
    await useFigureOriginStore.getState().switchKind(SCRIPT1, 'sem')
    expect(commit).not.toHaveBeenCalled()
    expect(panels()[0]).toMatchObject({ fileId: 'runtime:a1', script: SCRIPT1 })
    expect(discard).toHaveBeenCalledWith('SW', 'pa') // 会话照样放弃
    expect(useFigureOriginStore.getState().switching).toBeNull()
    propose.mockResolvedValue({ kind: 'candidates', session: 'SW2', dropped: [], render: 'lazy', items: [cand({ error: 'sd' })] } as never)
    await useFigureOriginStore.getState().switchKind(SCRIPT1, 'sem')
    expect(commit).not.toHaveBeenCalled()
  })

  it('提交没成（后端报错 / 没落盘 / 核对不过）：不换', async () => {
    register()
    commit.mockRejectedValueOnce(new Error('boom'))
    await useFigureOriginStore.getState().switchKind(SCRIPT1, 'sem')
    expect(panels()[0]!.fileId).toBe('runtime:a1')
    commit.mockResolvedValueOnce({ task: { outcome: 'failed' } } as AiDataCommitResult)
    await useFigureOriginStore.getState().switchKind(SCRIPT1, 'sem')
    expect(panels()[0]!.fileId).toBe('runtime:a1')
    commit.mockResolvedValueOnce({ task: { outcome: 'completed' }, script: SCRIPT2, stem: 's', data_check: 'mismatch' } as AiDataCommitResult)
    await useFigureOriginStore.getState().switchKind(SCRIPT1, 'sem')
    expect(panels()[0]!.fileId).toBe('runtime:a1')
    expect(useFigureOriginStore.getState().switching).toBeNull()
  })

  it('运行新脚本没出图：不换', async () => {
    register()
    useScriptRunStore.setState({ run: vi.fn(async () => {}) })
    await useFigureOriginStore.getState().switchKind(SCRIPT1, 'sem')
    expect(panels()[0]!.fileId).toBe('runtime:a1')
  })

  it('换项目中途放弃：不运行、不换，也不在新项目里留 switching', async () => {
    register()
    let release!: () => void
    refresh.mockReturnValue(new Promise<void>((r) => (release = r)))
    const run = vi.fn(async () => {})
    useScriptRunStore.setState({ run })
    const p = useFigureOriginStore.getState().switchKind(SCRIPT1, 'sem')
    await flush()
    await flush()
    expect(useFigureOriginStore.getState().switching).toMatchObject({ kind: 'sem' })
    setCurrentProjectId('pb')
    useDataFigureStore.getState().clear() // 换项目会 clear
    release()
    await p
    expect(run).not.toHaveBeenCalled()
    expect(panels()[0]!.fileId).toBe('runtime:a1')
    expect(useFigureOriginStore.getState().switching).toBeNull()
    expect(useFigureOriginStore.getState().figures).toEqual({})
  })

  it('等脚本跑的时候图被改了：复核不过，不换', async () => {
    register()
    const run = vi.fn(async (script: string) => {
      await runResult(D2)(script)
      useDocumentStore.getState().commit({ key: 'x' } as never, (d) => {
        ;(d.objects.find((o) => o.type === 'panel') as PanelObject).overrides = [{ gid: 'g', prop: 'fontsize', value: 12 } as never]
      })
    })
    useScriptRunStore.setState({ run })
    await useFigureOriginStore.getState().switchKind(SCRIPT1, 'sem')
    expect(panels()[0]).toMatchObject({ fileId: 'runtime:a1' })
    expect(panels()[0]!.overrides).toHaveLength(1) // 用户的改动原样还在
    expect(useFigureOriginStore.getState().figures[SCRIPT2]).toBeUndefined()
  })

  it('等脚本跑的时候面板被删了：不换', async () => {
    register()
    useScriptRunStore.setState({
      run: vi.fn(async (script: string) => {
        await runResult(D2)(script)
        useDocumentStore.getState().commit({ key: 'x' } as never, (d) => {
          d.objects = d.objects.filter((o) => o.type !== 'panel')
        })
      }),
    })
    await useFigureOriginStore.getState().switchKind(SCRIPT1, 'sem')
    expect(panels()).toHaveLength(0)
  })

  it('同一时刻只换一份；选当前这一种是空操作；没记到指纹的不给换', async () => {
    register()
    await useFigureOriginStore.getState().switchKind(SCRIPT1, 'sd')
    expect(propose).not.toHaveBeenCalled()
    useFigureOriginStore.getState().register({ ...baseOrigin(), script: 'x/y.py', fingerprint: null })
    await useFigureOriginStore.getState().switchKind('x/y.py', 'sem')
    expect(propose).not.toHaveBeenCalled()
  })

  it('等待态：下拉立刻显示新值、禁用；换完回到真值', async () => {
    register()
    let release!: () => void
    refresh.mockReturnValue(new Promise<void>((r) => (release = r)))
    await mountRow(panels()[0]!)
    const p = useFigureOriginStore.getState().switchKind(SCRIPT1, 'ci95')
    await flush()
    await flush()
    expect(row()!.textContent).toContain('95%')
    expect(row()!.getAttribute('aria-busy')).toBe('true')
    expect(row()!.querySelector('button')!.hasAttribute('disabled')).toBe(true)
    release()
    await p
    await flush()
  })

  it('开着的草稿不被冲掉：session / candidates / answers 原样；留存只丢最旧的，给换类型腾一个会话名额', async () => {
    register()
    useDataFigureStore.setState({
      selected: { path: 'b.csv', sheet: null }, session: 'DRAFT', phase: 'ready', answers: { y: 'q' },
      candidates: [{ ...cand({ id: 'd1' }), thumb: '/t' }], thumbs: { d1: 'ready' },
    })
    const snap = { ...useDataFigureStore.getState() }
    await useFigureOriginStore.getState().switchKind(SCRIPT1, 'sem')
    const s = useDataFigureStore.getState()
    expect(s.session).toBe(snap.session)
    expect(s.candidates).toBe(snap.candidates)
    expect(s.answers).toBe(snap.answers)
    expect(s.phase).toBe('ready')
    expect(s.selected).toBe(snap.selected)
    expect(discard).not.toHaveBeenCalledWith('DRAFT', expect.anything())
  })
})

const seedManifest = (p: PanelObject, gids: string[]) => {
  const elements = gids.map((gid) => ({ gid, role: gid.includes('errorbar') ? 'errorbar' : 'axes', label: gid, bbox: [0, 0, 1, 1], editable: [], draggable: false }))
  useRenderStore.getState().patch(renderKeyOf(p), {
    fileId: p.fileId, rev: 1, manifest: { stem: 's', size_mm: [80, 60], elements } as never, svg: '<svg/>', status: 'ready', stale: false,
    lastPatches: '[]', wantPatches: '[]',
  })
  useRenderStore.setState((s) => ({ latest: { ...s.latest, [p.fileId]: renderKeyOf(p) } }))
}

describe('换完之后选回误差棒（等新图的 manifest，不计时）', () => {
  const setup = async () => {
    register()
    const id = panels()[0]!.id
    useUiStore.setState({ elementPanelId: id, selectedGids: ['axes_0.errorbar_1'] })
    await useFigureOriginStore.getState().switchKind(SCRIPT1, 'sem')
    return id
  }

  it('manifest 到了：优先同一个 gid', async () => {
    const id = await setup()
    expect(useUiStore.getState().elementPanelId).toBe(id)
    expect(errorBarSelectPending()).toBe(true)
    expect(useUiStore.getState().selectedGids).toEqual([])
    seedManifest(panels()[0]!, ['axes_0', 'axes_0.errorbar_0', 'axes_0.errorbar_1'])
    expect(useUiStore.getState().selectedGids).toEqual(['axes_0.errorbar_1'])
    expect(errorBarSelectPending()).toBe(false)
  })

  it('同一个 gid 没有：选第一个误差棒；一个都没有：不动', async () => {
    await setup()
    seedManifest(panels()[0]!, ['axes_0', 'axes_0.errorbar_7'])
    expect(useUiStore.getState().selectedGids).toEqual(['axes_0.errorbar_7'])
  })

  it('一个误差棒都没有：选区不动', async () => {
    await setup()
    seedManifest(panels()[0]!, ['axes_0'])
    expect(useUiStore.getState().selectedGids).toEqual([])
    expect(errorBarSelectPending()).toBe(false)
  })

  it('用户在等的时候自己点了别的元素 / 撤销了：取消，不抢选区、不报错', async () => {
    await setup()
    useUiStore.setState({ selectedGids: ['axes_0'] })
    expect(errorBarSelectPending()).toBe(false)
    seedManifest(panels()[0]!, ['axes_0', 'axes_0.errorbar_1'])
    expect(useUiStore.getState().selectedGids).toEqual(['axes_0'])
  })

  it('等的时候撤销（面板换回旧图）：取消，不报错', async () => {
    await setup()
    expect(errorBarSelectPending()).toBe(true)
    expect(() => useDocumentStore.getState().undo()).not.toThrow()
    expect(errorBarSelectPending()).toBe(false)
  })
})

describe('画布名跟着换', () => {
  it('还是旧图当初自动起的名字：同一条历史里改成新图名；撤销一起回去', async () => {
    register()
    useDocumentStore.getState().commit({ key: 'x' } as never, (d) => {
      d.name = D1.stem
    })
    const past = useDocumentStore.getState().past.length
    await useFigureOriginStore.getState().switchKind(SCRIPT1, 'sem')
    expect(doc().name).toBe(D2.stem)
    expect(useDocumentStore.getState().past.length).toBe(past + 1)
    useDocumentStore.getState().undo()
    expect(doc().name).toBe(D1.stem)
  })

  it('用户改过名：不动', async () => {
    register()
    useDocumentStore.getState().commit({ key: 'x' } as never, (d) => {
      d.name = '我的画布'
    })
    await useFigureOriginStore.getState().switchKind(SCRIPT1, 'sem')
    expect(panels()[0]!.fileId).toBe('runtime:a2')
    expect(doc().name).toBe('我的画布')
  })
})
