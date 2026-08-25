import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { PanelObject } from '@/types/document'
import { fetchRuntimeStatus } from '@/lib/api'
import { useRuntimeAssetStore } from './runtimeAssetStore'

vi.mock('@/lib/api', () => ({
  fetchRuntimeStatus: vi.fn(),
}))

const mockFetch = vi.mocked(fetchRuntimeStatus)

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
      status: 'fresh', cached: true, registered: true, checked: true,
    })
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
})
