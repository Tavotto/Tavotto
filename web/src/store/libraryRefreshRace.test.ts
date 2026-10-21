import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchPanels: vi.fn(),
  fetchRegistry: vi.fn(),
  fetchReadiness: vi.fn().mockResolvedValue({
    project_id: 'project-a', fingerprint: 'empty-readiness', generated_at: 0,
    panels: [], conflicts: [], issues: [],
    summary: { total: 0, editable: 0, auto_linkable: 0, needs_probe: 0, conflict: 0, source_missing: 0, layout_only: 0 },
    project: { writable: true, registry_valid: true, scan_ok: true, can_rescan: true },
  }),
  refreshProject: vi.fn(),
}))

import { fetchPanels, fetchRegistry, refreshProject, type PanelsResponse, type RegistryView } from '@/lib/api'
import { setCurrentProjectId } from '@/lib/session'
import { groupOf } from '@/components/left/ScriptLibrary'
import type { PrepEntry } from './projectPreparationStore'
import { resetAssetLoadBookkeeping, useAssetStore } from './assetStore'
import { refreshProjectNow } from './liveSync'
import { useScriptLibraryStore } from './scriptLibraryStore'

function gate<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: Error) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

const tick = async () => {
  for (let i = 0; i < 16; i++) await Promise.resolve()
}
const panels = (): PanelsResponse => ({ panels: [], figures_dir: '/figures' })
const registry = (): RegistryView => ({
  source: '', scripts: {}, candidates: [], conflicts: {},
  all_scripts: [{ script: 'plot.py', registered: true, linked: false,
    static_stems: [], entry_candidates: [], reason: 'no_static_output', can_probe: true }],
})

beforeEach(() => {
  vi.mocked(fetchPanels).mockReset()
  vi.mocked(fetchRegistry).mockReset()
  vi.mocked(refreshProject).mockReset()
  setCurrentProjectId('project-a')
  useAssetStore.getState().clear()
  resetAssetLoadBookkeeping()
  useScriptLibraryStore.getState().clear()
})
afterEach(() => vi.restoreAllMocks())

describe('素材刷新按发起时刻与刷新生命周期认领（#902 / #904）', () => {
  it('运行前发出的注册表请求晚到，不把刚跑通的脚本挪回未运行', async () => {
    const old = gate<RegistryView>()
    vi.mocked(fetchRegistry).mockReturnValueOnce(old.promise)
    const clock = vi.spyOn(Date, 'now').mockReturnValue(100)
    const pending = useScriptLibraryStore.getState().load()
    const prep = { reportAt: 200, report: {
      outcome: { kind: 'succeeded' }, captured: [{ asset_id: 'fresh-figure' }],
    } } as unknown as PrepEntry
    expect(groupOf(registry().all_scripts[0], undefined, prep, 0)).toBe('linked')

    clock.mockReturnValue(300)
    old.resolve(registry())
    await pending
    const loaded = useScriptLibraryStore.getState()
    expect(groupOf(loaded.view!.all_scripts[0], undefined, prep, loaded.loadedAt)).toBe('linked')
    expect(loaded.loadedAt).toBe(100)
  })

  it('运行完成触发的刷新不被在途旧快照吞掉，多次通知只补问一次', async () => {
    const old = gate<RegistryView>()
    const fresh = gate<RegistryView>()
    vi.mocked(fetchRegistry).mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise)
    const first = useScriptLibraryStore.getState().load()
    const afterRun = useScriptLibraryStore.getState().load()
    const sameBatch = useScriptLibraryStore.getState().load()
    expect(vi.mocked(fetchRegistry)).toHaveBeenCalledTimes(1)
    old.resolve(registry())
    await first
    await tick()
    expect(vi.mocked(fetchRegistry)).toHaveBeenCalledTimes(2)
    expect(useScriptLibraryStore.getState().loading).toBe(true)
    const next = registry()
    next.all_scripts[0].linked = true
    fresh.resolve(next)
    await Promise.all([afterRun, sameBatch])
    expect(vi.mocked(fetchRegistry)).toHaveBeenCalledTimes(2)
    expect(useScriptLibraryStore.getState().view?.all_scripts[0].linked).toBe(true)
    expect(useScriptLibraryStore.getState().loading).toBe(false)
  })

  it('打开素材库的统一刷新仍在等后端时，旧 panels finally 不能收掉忙碌态', async () => {
    const old = gate<PanelsResponse>()
    const fresh = gate<PanelsResponse>()
    const scan = gate<Awaited<ReturnType<typeof refreshProject>>>()
    vi.mocked(fetchPanels).mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise)
    vi.mocked(refreshProject).mockReturnValueOnce(scan.promise)
    const background = useAssetStore.getState().load()
    const manual = refreshProjectNow()
    expect(useAssetStore.getState().loading).toBe(true)
    old.resolve(panels())
    await background
    expect(useAssetStore.getState().loading).toBe(true)
    expect(vi.mocked(fetchPanels)).toHaveBeenCalledTimes(1)
    scan.resolve({} as Awaited<ReturnType<typeof refreshProject>>)
    await tick()
    expect(vi.mocked(fetchPanels)).toHaveBeenCalledTimes(2)
    expect(useAssetStore.getState().loading).toBe(true)
    fresh.resolve(panels())
    await manual
    expect(useAssetStore.getState().loading).toBe(false)
  })

  it('旧注册表请求失败，运行完成排的补问仍会执行，失败期间保留已有清单', async () => {
    const old = gate<RegistryView>()
    const fresh = gate<RegistryView>()
    const previous = registry()
    useScriptLibraryStore.setState({ view: previous, loaded: true })
    vi.mocked(fetchRegistry).mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise)
    const first = useScriptLibraryStore.getState().load()
    const afterRun = useScriptLibraryStore.getState().load()
    old.reject(new Error('old request failed'))
    await first
    await tick()
    expect(useScriptLibraryStore.getState().view).toBe(previous)
    expect(useScriptLibraryStore.getState().loading).toBe(true)
    expect(vi.mocked(fetchRegistry)).toHaveBeenCalledTimes(2)
    fresh.resolve(registry())
    await afterRun
    expect(useScriptLibraryStore.getState().error).toBeNull()
    expect(useScriptLibraryStore.getState().loading).toBe(false)
  })

  it('切项目作废旧注册表的补问，旧 finally 不释放新项目的加载', async () => {
    const old = gate<RegistryView>()
    const next = gate<RegistryView>()
    vi.mocked(fetchRegistry).mockReturnValueOnce(old.promise).mockReturnValueOnce(next.promise).mockResolvedValue(registry())
    const first = useScriptLibraryStore.getState().load()
    const trailing = useScriptLibraryStore.getState().load()
    useScriptLibraryStore.getState().clear()
    const current = useScriptLibraryStore.getState().load()
    old.resolve(registry())
    await first
    await tick()
    expect(useScriptLibraryStore.getState().view).toBeNull()
    expect(useScriptLibraryStore.getState().loading).toBe(true)
    expect(vi.mocked(fetchRegistry)).toHaveBeenCalledTimes(2)
    next.resolve(registry())
    await Promise.all([current, trailing])
    expect(vi.mocked(fetchRegistry)).toHaveBeenCalledTimes(2)
    expect(useScriptLibraryStore.getState().loading).toBe(false)
  })

  it('两次统一刷新各自收尾，先完成的一次不能收掉另一次的忙碌态', async () => {
    const a = gate<Awaited<ReturnType<typeof refreshProject>>>()
    const b = gate<Awaited<ReturnType<typeof refreshProject>>>()
    vi.mocked(refreshProject).mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise)
    vi.mocked(fetchPanels).mockResolvedValue(panels())
    const first = refreshProjectNow()
    const second = refreshProjectNow()
    a.resolve({} as Awaited<ReturnType<typeof refreshProject>>)
    await first
    expect(useAssetStore.getState().loading).toBe(true)
    b.resolve({} as Awaited<ReturnType<typeof refreshProject>>)
    await second
    expect(useAssetStore.getState().loading).toBe(false)
  })

  it('扫描和强制清单都失败时也释放忙碌态，清单保留错误而非永远转圈', async () => {
    const scan = gate<Awaited<ReturnType<typeof refreshProject>>>()
    vi.mocked(refreshProject).mockReturnValueOnce(scan.promise)
    vi.mocked(fetchPanels).mockRejectedValueOnce(new Error('panels failed'))
    const result = refreshProjectNow().catch((error: unknown) => error)
    scan.reject(new Error('scan failed'))
    expect(await result).toEqual(new Error('scan failed'))
    expect(vi.mocked(fetchPanels)).toHaveBeenCalledTimes(1)
    expect(useAssetStore.getState().error).toBe('panels failed')
    expect(useAssetStore.getState().loading).toBe(false)
  })

  it.each([false, true])('切项目%s：旧统一刷新不向新代取清单，也不释放新代忙碌态', async (backToA) => {
    const scan = gate<Awaited<ReturnType<typeof refreshProject>>>()
    vi.mocked(refreshProject).mockReturnValueOnce(scan.promise)
    vi.mocked(fetchPanels).mockResolvedValue(panels())
    const old = refreshProjectNow()
    setCurrentProjectId('project-b')
    useAssetStore.getState().clear()
    if (backToA) setCurrentProjectId('project-a')
    const release = useAssetStore.getState().beginRefresh()
    scan.resolve({} as Awaited<ReturnType<typeof refreshProject>>)
    await old
    expect(fetchPanels).not.toHaveBeenCalled()
    expect(useAssetStore.getState().loading).toBe(true)
    release()
    expect(useAssetStore.getState().loading).toBe(false)
  })

  it('刷新释放函数幂等，且不能收掉还在飞的较新清单请求', async () => {
    const pending = gate<PanelsResponse>()
    vi.mocked(fetchPanels).mockReturnValueOnce(pending.promise)
    const release = useAssetStore.getState().beginRefresh()
    const load = useAssetStore.getState().load()
    release()
    release()
    expect(useAssetStore.getState().loading).toBe(true)
    pending.resolve(panels())
    await load
    expect(useAssetStore.getState().loading).toBe(false)
  })
})
