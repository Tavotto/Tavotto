/** 共用的项目设置写入：请求在飞时切了项目，响应不许套到新项目上；在飞期间 pending 为真。 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const h = vi.hoisted(() => ({ patch: vi.fn() }))
vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<typeof import('@/lib/api')>()),
  patchProjectSettings: (...a: unknown[]) => h.patch(...a),
}))

import { applyProjectSettingsPatch } from '@/lib/projectSettingsPatch'
import { useProjectStore } from '@/store/projectStore'

const project = (id: string, exp: string) =>
  ({ id, figures_dir: `/p/${id}`, settings: { export_dir: exp }, export_dir: exp, backup_dir: `/b/${id}` }) as never

beforeEach(() => {
  h.patch.mockReset()
  useProjectStore.setState({ project: project('A', '/a/export') })
})

describe('applyProjectSettingsPatch', () => {
  it('正常：把后端回执写进当前项目', async () => {
    h.patch.mockResolvedValue({ settings: { export_dir: '/a/new' }, export_dir: '/a/new', backup_dir: '/b/A' })
    await applyProjectSettingsPatch({ export_dir: '/a/new' })
    expect(useProjectStore.getState().project!.export_dir).toBe('/a/new')
  })

  it('请求在飞时切到项目 B：A 的回执被丢弃，B 的导出 / 备份目录不变', async () => {
    let release!: () => void
    h.patch.mockImplementation(
      () =>
        new Promise((res) => {
          release = () => res({ settings: { export_dir: '/a/new' }, export_dir: '/a/new', backup_dir: '/b/A' })
        }),
    )
    const inFlight = applyProjectSettingsPatch({ export_dir: '/a/new' })
    useProjectStore.setState({ project: project('B', '/b/export') })
    release()
    await inFlight
    const p = useProjectStore.getState().project!
    expect(p.id).toBe('B')
    expect(p.export_dir).toBe('/b/export')
    expect(p.backup_dir).toBe('/b/B')
    expect(p.settings).toEqual({ export_dir: '/b/export' })
  })

  it('失败原样抛出，且不改项目', async () => {
    h.patch.mockRejectedValue(new Error('nope'))
    await expect(applyProjectSettingsPatch({ export_dir: '/x' })).rejects.toThrow('nope')
    expect(useProjectStore.getState().project!.export_dir).toBe('/a/export')
  })
})
