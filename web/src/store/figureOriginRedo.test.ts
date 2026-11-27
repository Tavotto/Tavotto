/**
 * 新建图那一步被撤销再重做：面板照历史里「还没有来历」的样子回来，来历要补回去（否则「误差线」那一行永久消失，
 * 还会把一块没有来历的面板存进项目）。只在同一个脚本、同一个来源指纹时补；换了项目不补。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AiDataProposeItem, CapturedFigureDescriptor } from '@/lib/api'
import { makeOrigin, readPanelOrigin } from '@/lib/dataFigureOrigin'
import { setCurrentProjectId } from '@/lib/session'
import '@/lib/dataFigureHost'
import { addRuntimePanel } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useFigureOriginStore } from '@/store/figureOriginStore'
import { emptyProject, type PanelObject } from '@/types/document'

vi.mock('@/store/liveSync', () => ({ refreshProjectNow: vi.fn(async () => {}) }))
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

const SHA = 'b'.repeat(64)
const SCRIPT = 'tavotto_figures/redo.py'
const desc = (fp: string): CapturedFigureDescriptor => ({
  asset_id: 'runtime:r1', script: SCRIPT, entry: SCRIPT, stem: 'r1', capture_source: 'savefig', execution_profile: 'safe',
  original_artifact: null, size_mm: [80, 60], source_fingerprint: fp, can_writeback_artifact: false, can_writeback_source: false,
})
const cand: AiDataProposeItem = {
  id: 'i1', title_code: 'points_mean', why_code: 'w', recommended: true, unplotted: { count: 0, code: null }, thumbnail_url: '/t/1', error: 'sd', error_source: 'computed',
}
const register = () =>
  useFigureOriginStore.getState().register(
    makeOrigin({ script: SCRIPT, item: cand, file: { path: 'a.csv', sheet: null }, grouped: false, headerRow: 2, answers: {}, spec: null, fontRange: null, dataSha256: SHA, pj: 'pa' })!,
  )
const panels = () => useDocumentStore.getState().doc.objects.filter((o): o is PanelObject => o.type === 'panel')
const doc = () => useDocumentStore.getState()

let seq = 0
beforeEach(async () => {
  setCurrentProjectId('pa')
  useFigureOriginStore.getState().clear()
  await doc().switchDocument(emptyProject(), `d_redo_${seq++}`)
})

describe('撤销新建图那一步再重做：来历还在', () => {
  it('重做回来的面板带着同一份来历；这一来一回不多占撤销步数', () => {
    register()
    addRuntimePanel(desc('F1'))
    const first = readPanelOrigin(panels()[0]!)
    expect(first).toMatchObject({ script: SCRIPT, kind: 'sd', fingerprint: 'F1', dataSha256: SHA })
    const past = doc().past.length

    doc().undo()
    expect(panels()).toHaveLength(0)
    doc().redo()
    expect(panels()).toHaveLength(1)
    expect(readPanelOrigin(panels()[0]!)).toEqual(first)
    expect(doc().past.length).toBe(past)

    // 再来一轮也一样
    doc().undo()
    doc().redo()
    expect(readPanelOrigin(panels()[0]!)).toEqual(first)
  })

  it('同一个脚本但来源指纹不同的面板（脚本后来被改过再画的）：不补', () => {
    register()
    addRuntimePanel(desc('F1'))
    doc().undo()
    addRuntimePanel(desc('F2'))
    expect(panels()).toHaveLength(1)
    expect(panels()[0]!.dataOrigin).toBeUndefined()
  })

  it('换了项目（clear）：记下的那份清掉，不补', () => {
    register()
    addRuntimePanel(desc('F1'))
    doc().undo()
    useFigureOriginStore.getState().clear()
    doc().redo()
    expect(panels()).toHaveLength(1)
    expect(panels()[0]!.dataOrigin).toBeUndefined()
  })
})
