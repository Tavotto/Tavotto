/**
 * 项目刷新（`refreshAssetsAndSync`，手动刷新 / 磁盘事件共用的那一条）也让「数据」的文件清单失效并重读：
 * 开关开着、项目里原本没有数据文件，外部加了一份 CSV 后点刷新，「数据」入口要能出现。清单没读过（开关关着）不发请求。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { aiDataFilesAll } from '@/lib/api'
import { setCurrentProjectId } from '@/lib/session'
import { useAssetStore } from './assetStore'
import { useDataFigureStore } from './dataFigureStore'
import { refreshAssetsAndSync } from './liveSync'
import { useProjectReadinessStore } from './projectReadinessStore'

vi.mock('@/lib/api', async (importOriginal) => ({ ...(await importOriginal<typeof import('@/lib/api')>()), aiDataFilesAll: vi.fn() }))
const files = vi.mocked(aiDataFilesAll)
const flush = () => new Promise((r) => setTimeout(r, 0))

beforeEach(() => {
  files.mockReset()
  setCurrentProjectId('pa')
  useDataFigureStore.getState().clear()
  vi.spyOn(useAssetStore.getState(), 'load').mockResolvedValue(null as never)
  vi.spyOn(useProjectReadinessStore.getState(), 'load').mockResolvedValue(undefined as never)
})

describe('项目刷新 → 数据文件清单', () => {
  it('清单读过（空）→ 外部加了文件 → 刷新后有文件', async () => {
    files.mockResolvedValueOnce({ files: [], truncated: false })
    await useDataFigureStore.getState().loadFiles()
    files.mockResolvedValueOnce({ files: [{ path: 'new.csv', size: 1, table: { status: 'ok', code: null, delimiter: 'comma', header_row: 1, columns: 2, rows: 3 } }], truncated: false })
    await refreshAssetsAndSync({ force: true })
    await flush()
    expect(useDataFigureStore.getState().files).toMatchObject({ status: 'ready', entries: [{ path: 'new.csv' }] })
  })

  it('清单没读过（开关关着）：刷新不去请求数据文件', async () => {
    await refreshAssetsAndSync({ force: true })
    await flush()
    expect(files).not.toHaveBeenCalled()
  })
})
