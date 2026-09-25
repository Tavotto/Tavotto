/**
 * 换项目时素材清单必须换代（#577，#561 评审遗留）。
 *
 * `assetStore` 的「刷新失败保留上一份」是为**同一个项目**准备的（#561）。改造前 `resetForNewProject()`
 * 不清它：切到 B 之后、B 的 `/api/panels` 回来之前（失败的话就一直），素材库在 B 下面显示的是
 * A 的卡片、A 的「以下文件无法使用」清单，来源下拉里也是 A 的目录。
 *
 * 三条判据各自可被单独拆掉：
 *   * 切到 B、B 挂起：store 里没有 A 的任何清单数据（`clear()` 被 `resetForNewProject` 调到）；
 *   * 切到 B、B 失败：同上，而且失败不会把 A 的那份「保留」回来；
 *   * 同一项目刷新失败：照旧保留（#561 的行为不回退）。
 * 另有一条管换代：A 在切走之前发出的请求切完才回来，不许落地。
 */
import { beforeEach, describe, expect, it } from 'vitest'

import { setCurrentProjectId } from '@/lib/session'
import type { PanelsResponse } from '@/lib/api'
import { assetFolders, resetAssetLoadBookkeeping, useAssetStore } from './assetStore'
import { useProjectStore } from './projectStore'

const A_PANELS: PanelsResponse = {
  figures_dir: '/a',
  panels: [
    {
      id: 'figs/a1.pdf',
      name: 'a1',
      folder: 'figs',
      kind: 'pdf',
      native_w_mm: 80,
      native_h_mm: 60,
      mtime: 1,
    },
  ],
  unsupported: [{ id: 'raw-a/scan.tif', name: 'scan.tif', folder: 'raw-a', code: 'tiff_sample_format' }],
}

/** `/api/panels` 的回答由用例控制：`hold` 时扣住不答，`fail` 时回 500 */
let panelsMode: 'answer' | 'hold' | 'fail' = 'answer'
let panelsAnswer: PanelsResponse = A_PANELS
const held: ((r: Response) => void)[] = []

globalThis.fetch = (async (url: unknown) => {
  const u = String(url)
  if (u.includes('/api/panels')) {
    if (panelsMode === 'hold') return new Promise<Response>((resolve) => held.push(resolve))
    if (panelsMode === 'fail')
      return new Response(JSON.stringify({ error: 'boom' }), { status: 500 })
    return new Response(JSON.stringify(panelsAnswer), { status: 200 })
  }
  const body = u.includes('/api/projects/recent')
    ? { recent: [] }
    : u.includes('/api/projects/open')
      ? []
      : u.includes('/api/engine/environment')
        ? { ok: true, python: '/p', source: 'system', project: { open: true } }
        : {}
  return new Response(JSON.stringify(body), { status: 200 })
}) as typeof fetch

const switchProject = (id: string) =>
  useProjectStore
    .getState()
    .adoptOpenedProject({ id, path: `/${id}`, name: id, writable: true, open: true } as never)

const tick = async () => {
  for (let i = 0; i < 20; i++) await new Promise((r) => setTimeout(r, 0))
}

/** store 里凡是能把 A 的内容带到界面上的东西 */
const leaked = () => {
  const s = useAssetStore.getState()
  return JSON.stringify({
    panels: s.panels,
    byId: s.byId,
    unsupported: s.unsupported,
    folders: assetFolders(s.panels, s.unsupported),
    dir: s.figuresDir,
  })
}

beforeEach(async () => {
  panelsMode = 'answer'
  panelsAnswer = A_PANELS
  held.length = 0
  setCurrentProjectId('pa')
  resetAssetLoadBookkeeping()
  useAssetStore.getState().clear()
  await useAssetStore.getState().load()
  expect(useAssetStore.getState().unsupported).toHaveLength(1)
})

describe('切到 B 时 A 的清单不跟过来', () => {
  it('B 的 /api/panels 挂起：没有 A 的卡片、无法使用清单、来源目录', async () => {
    panelsMode = 'hold'
    void switchProject('pb')
    await tick()
    const s = useAssetStore.getState()
    expect(s.loaded).toBe(false)
    expect(leaked()).not.toContain('a1')
    expect(leaked()).not.toContain('scan.tif')
    expect(leaked()).not.toContain('raw-a')
    expect(leaked()).not.toContain('figs')
    expect(leaked()).not.toContain('/a')
    held.forEach((r) => r(new Response(JSON.stringify({ figures_dir: '/b', panels: [] }))))
  })

  it('B 的 /api/panels 失败：同样一条都不带过来（「失败保留」只对同一项目）', async () => {
    panelsMode = 'fail'
    await switchProject('pb')
    await tick()
    const s = useAssetStore.getState()
    expect(s.error).toBeTruthy()
    expect(leaked()).not.toContain('scan.tif')
    expect(leaked()).not.toContain('raw-a')
    expect(leaked()).not.toContain('a1')
  })

  it('A 在切走之前发出的请求切完才回来：不落地', async () => {
    panelsMode = 'hold'
    void useAssetStore.getState().load({ force: true }) // A 的一次刷新，扣住
    const staleA = held.pop()!
    // B 的清单失败：没有任何更新的响应落地，请求序号（`mine < applied`）挡不住那份旧的
    panelsMode = 'fail'
    await switchProject('pb')
    // 又回到 A 的 pj（只换认领、不再发请求）：A 那份旧响应这时才到，pj 对得上，
    // 但它早于切项目时的清空——只有代际挡得住它
    setCurrentProjectId('pa')
    staleA(new Response(JSON.stringify(A_PANELS)))
    await tick()
    expect(leaked()).not.toContain('scan.tif')
    expect(leaked()).not.toContain('a1')
  })
})

describe('同一项目刷新失败（#561 的行为不回退）', () => {
  it('保留上一份清单与来源目录', async () => {
    panelsMode = 'fail'
    await useAssetStore.getState().load({ force: true })
    const s = useAssetStore.getState()
    expect(s.error).toBeTruthy()
    expect(s.unsupported.map((u) => u.id)).toEqual(['raw-a/scan.tif'])
    expect(assetFolders(s.panels, s.unsupported)).toEqual(['figs', 'raw-a'])
  })
})
