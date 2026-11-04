/** 「刷新项目」顺带重扫导入即扫描的快照（QA-WIN-44）：脚本在外面改名又恢复后，旧的待准备项不再留着。 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  refreshProject: vi.fn().mockResolvedValue({}),
  fetchPanels: vi.fn().mockResolvedValue({ panels: [], figures_dir: '/figures' }),
  fetchReadiness: vi.fn().mockResolvedValue({
    project_id: 'pj', fingerprint: 'x', generated_at: 0, panels: [], conflicts: [], issues: [],
    summary: { total: 0, editable: 0, auto_linkable: 0, needs_probe: 0, conflict: 0, source_missing: 0, layout_only: 0 },
    project: { writable: true, registry_valid: true, scan_ok: true, can_rescan: true },
  }),
  startProjectScan: vi.fn(),
}))

import { startProjectScan, type ProjectScan } from '@/lib/api'
import { setCurrentProjectId } from '@/lib/session'
import { resetAssetLoadBookkeeping, useAssetStore } from './assetStore'
import { refreshProjectNow } from './liveSync'
import { resetProjectScanBookkeeping, useProjectScanStore } from './projectScanStore'

const snap = (over: Partial<ProjectScan>): ProjectScan => ({
  scan_version: 1, project_id: 'pj', scan_id: 's1', epoch: 1, observation_seq: 1, reason: 'claim',
  state: 'complete', phase: 'completed', outcome: { kind: 'static_source' },
  budget: { entries: 1, scripts: 1, assets: 0, elapsed_s: 0 }, issues: [], checks: [], actions: [],
  ...over,
})
const flush = async () => { for (let i = 0; i < 16; i++) await Promise.resolve() }

beforeEach(() => {
  vi.mocked(startProjectScan).mockReset()
  setCurrentProjectId('pj')
  useAssetStore.getState().clear()
  resetAssetLoadBookkeeping()
  resetProjectScanBookkeeping()
  useProjectScanStore.getState().clear()
})

describe('refreshProjectNow 与导入即扫描', () => {
  it('有扫描快照时顺带强制重扫，旧的待准备脚本随新快照消失', async () => {
    useProjectScanStore.setState({ scan: snap({ default_target: 'fig1_kinetics_1101.py' }) })
    vi.mocked(startProjectScan).mockResolvedValue(snap({ scan_id: 's2', reason: 'refresh' }))
    await refreshProjectNow()
    await flush()
    expect(startProjectScan).toHaveBeenCalledWith({ force: true, reason: 'refresh' })
    expect(useProjectScanStore.getState().scan?.default_target).toBeUndefined()
  })

  it('没有扫描快照（教程项目）不替它开一轮', async () => {
    await refreshProjectNow()
    await flush()
    expect(startProjectScan).not.toHaveBeenCalled()
  })
})
