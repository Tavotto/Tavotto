/**
 * 图的来历落在面板上（`PanelObject.dataOrigin`）：关掉项目再打开仍在、数据变了 / 不见了 / 还没核对完就不出那一行、
 * 严格读（不信文档里的任何形状）、绝不落绝对路径、换类型前核数据摘要、不外流（导出请求、前端诊断快照；后端三条通道在
 * `tests/test_data_origin_no_leak.py`）。换图本身的行为在 `figureOriginSwitch.test.tsx`。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { ErrorKindRow } from '@/components/inspector/ErrorKindRow'
import { aiDataFigureCommit, aiDataFigureDigest, aiDataFigurePropose, type AiDataProposeItem, type CapturedFigureDescriptor, type RuntimeAssetInfo } from '@/lib/api'
import { canvasSegmentOf } from '@/lib/exportRequest'
import { isProjectRelativePosix, makeOrigin, readPanelOrigin, toPanelOrigin } from '@/lib/dataFigureOrigin'
import { migrateFigureFrames } from '@/lib/figureFrameMigration'
import { setCurrentProjectId } from '@/lib/session'
import '@/lib/dataFigureHost'
import { panelSnapshot } from '@/diagnostics/snapshot'
import { addRuntimePanel } from '@/store/actions'
import { useDataFigureStore } from '@/store/dataFigureStore'
import { useDocumentStore } from '@/store/documentStore'
import { useFigureOriginStore } from '@/store/figureOriginStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { useTafFlagStore } from '@/store/tafFlagStore'
import { emptyProject, migrateToProject, type PanelDataOrigin, type PanelObject } from '@/types/document'

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
  aiDataFigureDigest: vi.fn(),
}))
vi.mock('@/store/liveSync', () => ({ refreshProjectNow: vi.fn(async () => {}) }))
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

const propose = vi.mocked(aiDataFigurePropose)
const commit = vi.mocked(aiDataFigureCommit)
const digest = vi.mocked(aiDataFigureDigest)

const SHA = 'a'.repeat(64)
const SENTINEL = 'DATAORIGIN_SENTINEL_9f3a'
const D1: CapturedFigureDescriptor = {
  asset_id: 'runtime:a1', script: 'tavotto_figures/a.py', entry: 'tavotto_figures/a.py', stem: 'a1', capture_source: 'savefig', execution_profile: 'safe',
  original_artifact: null, size_mm: [80, 60], source_fingerprint: 'F1', can_writeback_artifact: false, can_writeback_source: false,
}
const asset = (d: CapturedFigureDescriptor): RuntimeAssetInfo => ({
  id: d.asset_id, script: d.script, stem: d.stem, entry: d.entry, status: 'fresh', cached: true, size_mm: d.size_mm, capture_source: 'savefig', descriptor: d,
})
const item = (): AiDataProposeItem => ({
  id: 'i1', title_code: 'points_mean', why_code: 'w', recommended: true, unplotted: { count: 0, code: null }, thumbnail_url: '/t/1', error: 'sd', error_source: 'computed',
})
const origin = (over: Partial<PanelDataOrigin> = {}): PanelDataOrigin => ({
  version: 1, script: D1.script, file: { path: 'data/a.csv', sheet: null }, headerRow: 2, answers: { y: SENTINEL }, spec: { name: SENTINEL }, fontRange: null,
  identity: 'x', kind: 'sd', band: false, fingerprint: 'F1', dataSha256: SHA, ...over,
})
const panels = () => useDocumentStore.getState().doc.objects.filter((o): o is PanelObject => o.type === 'panel')
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
  await flush()
  await flush()
}
const row = () => document.querySelector('[data-error-kind]')

/** 面板带着来历（直接写进文档，模拟「重开项目读回来」之后的状态） */
let bootSeq = 0
async function boot(o: PanelDataOrigin | null = origin()) {
  await useDocumentStore.getState().switchDocument(emptyProject(), `d_persist_${bootSeq++}`)
  const p = addRuntimePanel(D1)
  useDocumentStore.getState().commit({ key: 'x' } as never, (d) => {
    const x = d.objects.find((q) => q.id === p.id) as PanelObject
    if (o) x.dataOrigin = o
  })
  useRuntimeAssetStore.setState({ assets: [asset(D1)] })
}

beforeEach(() => {
  for (const f of [propose, commit, digest]) f.mockReset()
  digest.mockResolvedValue({ sha256: SHA })
  setCurrentProjectId('pa')
  useDataFigureStore.getState().clear()
  useFigureOriginStore.getState().clear()
  useTafFlagStore.setState({ enabled: true })
})
afterEach(() => {
  act(() => root?.unmount())
  hostEl?.remove()
  document.body.innerHTML = ''
})

describe('关掉项目再打开：来历随文档落盘、读回来仍在', () => {
  it('存盘 → 读回（JSON 往返 + migrateToProject）→ 那一行仍出现', async () => {
    await boot()
    const json = JSON.stringify(useDocumentStore.getState().buildProject())
    expect(json).toContain('"dataOrigin"')
    const reopened = migrateToProject(JSON.parse(json))!
    await useDocumentStore.getState().switchDocument(reopened, 'd_reopened')
    useRuntimeAssetStore.setState({ assets: [asset(D1)] })
    expect(readPanelOrigin(panels()[0]!)).toMatchObject({ script: D1.script, kind: 'sd', dataSha256: SHA, file: { path: 'data/a.csv' } })
    await mountRow(panels()[0]!)
    expect(row()).not.toBeNull()
  })

  it('旧构建没有这个字段：面板照常，那一行不出；字段在迁移（补图幅记号）里被带着走', async () => {
    await boot(null)
    await mountRow(panels()[0]!)
    expect(row()).toBeNull()
    const withOrigin: PanelObject = { ...panels()[0]!, dataOrigin: origin(), figureFrame: undefined }
    const [out] = migrateFigureFrames([withOrigin]) as PanelObject[]
    expect(out!.dataOrigin).toEqual(origin())
  })
})

describe('数据文件变了 / 不见了 / 还没核对完：不出那一行（静默）', () => {
  it('核对结果与画图时一致才出；不同（同样大小改了内容也一样）/ 文件不在（null）/ 还在核对：不出', async () => {
    await boot()
    await mountRow(panels()[0]!)
    expect(row()).not.toBeNull()
    act(() => root.unmount())
    hostEl.remove()
    useFigureOriginStore.getState().clear()
    digest.mockResolvedValue({ sha256: 'b'.repeat(64) })
    await mountRow(panels()[0]!)
    expect(row()).toBeNull()
    act(() => root.unmount())
    hostEl.remove()
    useFigureOriginStore.getState().clear()
    digest.mockResolvedValue({ sha256: null })
    await mountRow(panels()[0]!)
    expect(row()).toBeNull()
    act(() => root.unmount())
    hostEl.remove()
    useFigureOriginStore.getState().clear()
    digest.mockReturnValue(new Promise(() => {}))
    await mountRow(panels()[0]!)
    expect(row()).toBeNull() // 核对完成前也不出
    expect(digest).toHaveBeenCalledWith('data/a.csv', 'pa')
  })

  it('核对请求失败：当核对不了，不出、不报错', async () => {
    await boot()
    digest.mockRejectedValue(new Error('net'))
    await mountRow(panels()[0]!)
    expect(row()).toBeNull()
  })

  it('换类型时数据已经变了：不提交、不换（propose 回的摘要与来历里的不同）', async () => {
    await boot()
    propose.mockResolvedValue({ kind: 'candidates', session: 'SW', dropped: [], render: 'lazy', data_sha256: 'c'.repeat(64), items: [item()] } as never)
    await useFigureOriginStore.getState().switchKind(panels()[0]!.id, 'sem')
    expect(propose).toHaveBeenCalledTimes(1)
    expect(commit).not.toHaveBeenCalled()
    expect(panels()[0]!.fileId).toBe('runtime:a1')
    expect(useFigureOriginStore.getState().switching).toBeNull()
  })

  it('旧后端 propose 没回摘要：同样不换', async () => {
    await boot()
    propose.mockResolvedValue({ kind: 'candidates', session: 'SW', dropped: [], render: 'lazy', items: [item()] } as never)
    await useFigureOriginStore.getState().switchKind(panels()[0]!.id, 'sem')
    expect(commit).not.toHaveBeenCalled()
  })
})

describe('读来历是严格的，路径只许项目相对', () => {
  const read = (over: Record<string, unknown>) => readPanelOrigin({ dataOrigin: { ...origin(), ...over } as never })
  it('合格的读得出；版本不认识 / 形状不对 / 摘要不是 64 位十六进制 / 种类不在闭集 = 当没有', () => {
    expect(read({})).not.toBeNull()
    expect(readPanelOrigin({})).toBeNull()
    for (const bad of [{ version: 2 }, { version: undefined }, { kind: 'se' }, { dataSha256: 'zz' }, { dataSha256: 'A'.repeat(64) }, { fingerprint: '' }, { answers: [] }, { answers: null }, { band: 'no' }, { headerRow: 1.5 }, { file: null }, { file: { path: 'a.csv', sheet: 3 } }, { identity: 1 }]) {
      expect(read(bad), JSON.stringify(bad)).toBeNull()
    }
  })

  it('路径不是项目相对的 POSIX（绝对 / 盘符 / UNC / 反斜杠 / .. / 空段 / 控制字符）一律读不出、写不进', () => {
    for (const bad of ['/abs/a.csv', 'C:/a.csv', 'c:\\a.csv', '\\\\srv\\a.csv', 'a\\b.csv', '../a.csv', 'a/../b.csv', './a.csv', 'a//b.csv', 'a/', '', 'a\u0000b.csv']) {
      expect(isProjectRelativePosix(bad), bad).toBe(false)
      expect(read({ file: { path: bad, sheet: null } }), bad).toBeNull()
      expect(read({ script: bad }), bad).toBeNull()
    }
    expect(isProjectRelativePosix('data/中文 表.csv')).toBe(true)
  })

  it('登记时绝对路径的数据不登记（makeOrigin = null）；写盘前再挡一道（toPanelOrigin = null）；没有摘要也不登记', () => {
    const args = { script: D1.script, item: item(), file: { path: '/Users/x/data.csv', sheet: null }, grouped: false, headerRow: null, answers: {}, spec: null, fontRange: null, dataSha256: SHA, pj: 'pa' }
    expect(makeOrigin(args)).toBeNull()
    expect(makeOrigin({ ...args, file: { path: 'data.csv', sheet: null }, dataSha256: null })).toBeNull()
    const ok = makeOrigin({ ...args, file: { path: 'data.csv', sheet: null } })!
    expect(ok).not.toBeNull()
    expect(toPanelOrigin({ ...ok, file: { path: 'C:\\data.csv', sheet: null } }, 'F1')).toBeNull()
    expect(toPanelOrigin(ok, 'F1')).not.toBeNull()
  })
})

describe('不外流（前端两条；后端三条在 tests/test_data_origin_no_leak.py）', () => {
  it('导出请求的画布段：面板只带显式字段，来历一个字都不在', async () => {
    await boot()
    const seg = canvasSegmentOf(useDocumentStore.getState().doc)
    const text = JSON.stringify(seg)
    expect(text).toContain('runtime:a1') // 面板确实在载荷里
    expect(text).not.toContain(SENTINEL)
    expect(text).not.toContain('dataOrigin')
    expect(text).not.toContain('data/a.csv')
  })

  it('前端诊断快照里的面板项：来历一个字都不在', async () => {
    await boot()
    const text = JSON.stringify(panelSnapshot(panels()[0]!))
    expect(text).not.toContain(SENTINEL)
    expect(text).not.toContain('data/a.csv')
    expect(text).not.toContain('dataOrigin')
  })
})
