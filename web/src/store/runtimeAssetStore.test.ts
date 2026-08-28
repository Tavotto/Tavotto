import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { PanelObject } from '@/types/document'
import { fetchRuntimeAssets, fetchRuntimeStatus } from '@/lib/api'
import { useRuntimeAssetStore } from './runtimeAssetStore'

vi.mock('@/lib/api', () => ({
  fetchRuntimeStatus: vi.fn(),
  fetchRuntimeAssets: vi.fn(),
}))

const mockFetch = vi.mocked(fetchRuntimeStatus)
const mockAssets = vi.mocked(fetchRuntimeAssets)

const runtimePanel = (fileId = 'runtime:fig.py#fig'): PanelObject =>
  ({
    id: 'p1',
    type: 'panel',
    fileId,
    fileKind: 'runtime',
    nativeW: 100,
    nativeH: 80,
    x: 0,
    y: 0,
    w: 100,
    h: 80,
    source: {
      script: 'fig.py',
      entry: '__main__',
      stem: 'fig',
      captureSource: 'pyplot',
      fingerprint: 'sha256:x',
      sizeMm: [100, 80],
    },
    overrides: [],
  }) as PanelObject

const flush = () => new Promise((r) => setTimeout(r, 0))

beforeEach(() => {
  useRuntimeAssetStore.getState().clear()
  mockFetch.mockReset()
})

describe('runtimeAssetStore', () => {
  it('ensure 只查询一次并带上文档描述块（恢复线索）', async () => {
    mockFetch.mockResolvedValue({
      id: 'runtime:fig.py#fig', status: 'fresh', script: 'fig.py',
      stem: 'fig', entry: '__main__', registered: true, cached: true,
    })
    const st = useRuntimeAssetStore.getState()
    st.ensure(runtimePanel())
    st.ensure(runtimePanel())
    await flush()
    useRuntimeAssetStore.getState().ensure(runtimePanel())
    expect(mockFetch).toHaveBeenCalledTimes(1)
    expect(mockFetch).toHaveBeenCalledWith('runtime:fig.py#fig', {
      script: 'fig.py',
      stem: 'fig',
    })
    expect(useRuntimeAssetStore.getState().byId['runtime:fig.py#fig']).toEqual({
      status: 'fresh', cached: true, registered: true, checked: true, profile: 'safe',
    })
  })

  it('后端说 native 就记 native——离线角标靠它（ADR 0021 §9）', async () => {
    mockFetch.mockResolvedValue({
      id: 'runtime:fig.py#fig', status: 'fresh', script: 'fig.py',
      stem: 'fig', entry: '__main__', registered: true, cached: true,
      execution_profile: 'native',
    })
    useRuntimeAssetStore.getState().ensure(runtimePanel())
    await flush()
    expect(useRuntimeAssetStore.getState().byId['runtime:fig.py#fig'].profile).toBe('native')
  })

  it('**未知不等于 native**：老后端不给这个字段时按 safe 记', async () => {
    // 反过来的话，每一个普通 runtime 面板都会挂上「会话已结束」的角标
    mockFetch.mockResolvedValue({
      id: 'runtime:fig.py#fig', status: 'fresh', script: 'fig.py',
      stem: 'fig', entry: '__main__', registered: true, cached: true,
    })
    useRuntimeAssetStore.getState().ensure(runtimePanel())
    await flush()
    expect(useRuntimeAssetStore.getState().byId['runtime:fig.py#fig'].profile).toBe('safe')
  })

  it('非 runtime 面板绝不查询', () => {
    const file = { ...runtimePanel('Fig1.pdf'), fileKind: 'pdf' } as PanelObject
    useRuntimeAssetStore.getState().ensure(file)
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it('查询失败按 needs_rerun 处理，绝不猜成新鲜', async () => {
    mockFetch.mockRejectedValue(new Error('404'))
    useRuntimeAssetStore.getState().ensure(runtimePanel())
    await flush()
    expect(useRuntimeAssetStore.getState().byId['runtime:fig.py#fig']).toEqual({
      status: 'needs_rerun', cached: false, registered: false, checked: true,
      // 查询失败时同样按 safe：不猜成 native，也就不会误挂「会话已结束」
      profile: 'safe',
    })
  })

  it('渲染成功 → fresh；一次重跑失败 → rerun_failed（该状态唯一 producer）', async () => {
    mockFetch.mockResolvedValue({
      id: 'runtime:fig.py#fig', status: 'possibly_stale', script: 'fig.py',
      stem: 'fig', entry: '__main__', registered: true, cached: true,
    })
    const st = useRuntimeAssetStore.getState()
    st.ensure(runtimePanel())
    await flush()
    st.markFresh('runtime:fig.py#fig')
    expect(useRuntimeAssetStore.getState().byId['runtime:fig.py#fig'].status).toBe('fresh')
    st.markRerunFailed('runtime:fig.py#fig')
    expect(useRuntimeAssetStore.getState().byId['runtime:fig.py#fig'].status).toBe(
      'rerun_failed',
    )
  })

  it('invalidate 作废判定，下次 ensure 重新查询', async () => {
    mockFetch.mockResolvedValue({
      id: 'runtime:fig.py#fig', status: 'fresh', script: 'fig.py',
      stem: 'fig', entry: '__main__', registered: true, cached: true,
    })
    const st = useRuntimeAssetStore.getState()
    st.ensure(runtimePanel())
    await flush()
    st.invalidate(['runtime:fig.py#fig'])
    useRuntimeAssetStore.getState().ensure(runtimePanel())
    await flush()
    expect(mockFetch).toHaveBeenCalledTimes(2)
  })

  it('切项目：pending 的清单响应作废，绝不落进新项目（Codex 评审 P1）', async () => {
    // A 项目的请求还在途
    let resolveA!: (v: { assets: never[] }) => void
    mockAssets.mockImplementationOnce(
      () => new Promise((r) => (resolveA = r as never)),
    )
    const st = useRuntimeAssetStore.getState()
    void st.loadAssets()

    // 切到 B：clear 换代
    st.clear()
    resolveA({
      assets: [{ id: 'runtime:a.py#a' }],
    } as never)
    await flush()
    // A 的清单没有落地；loading 状态也不归旧响应管
    expect(useRuntimeAssetStore.getState().assets).toBeNull()

    // B 自己的请求照常新发、照常落地
    mockAssets.mockResolvedValueOnce({ assets: [] } as never)
    await useRuntimeAssetStore.getState().loadAssets()
    expect(useRuntimeAssetStore.getState().assets).toEqual([])
    expect(mockAssets).toHaveBeenCalledTimes(2)
  })

  it('切项目：pending 的 status 判定作废（ensure 同一条代际纪律）', async () => {
    let resolveA!: (v: unknown) => void
    mockFetch.mockImplementationOnce(
      () => new Promise((r) => (resolveA = r as never)),
    )
    const st = useRuntimeAssetStore.getState()
    st.ensure(runtimePanel())
    st.clear()
    resolveA({
      id: 'runtime:fig.py#fig', status: 'fresh', script: 'fig.py',
      stem: 'fig', entry: '__main__', registered: true, cached: true,
    })
    await flush()
    expect(useRuntimeAssetStore.getState().byId).toEqual({})
  })
})
