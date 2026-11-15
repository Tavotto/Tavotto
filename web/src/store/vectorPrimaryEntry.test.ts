// ADR 0184 §二：同一张图有矢量主文件时，没动过的 PNG 改从主文件进编辑，不再单独走位图采用
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import type { PanelInfo } from '@/lib/api'
import { emptyProject, type PanelObject } from '@/types/document'
import { useDocumentStore } from './documentStore'
import { useAssetStore } from './assetStore'
import { enterElementEdit } from './actions'
import { useSelectionStore } from './selectionStore'
import { setCurrentProjectId } from '@/lib/session'

const render = vi.fn()
vi.mock('@/lib/api', async (original) => ({
  ...(await original<typeof import('@/lib/api')>()),
  engineRender: (...args: unknown[]) => render(...args),
  fetchScriptBackedEnabled: async () => false,
}))

const pngPanel = (extra: Partial<PanelObject> = {}): PanelObject => ({
  id: 'p', type: 'panel', fileId: 'fig1.png', fileKind: 'raster', script: 'plot.py', figureFrame: 1,
  nativeW: 120, nativeH: 90, pxW: 1200, pxH: 900, x: 10, y: 20, w: 60, h: 45, overrides: [], ...extra,
})
const info = (id: string, extra: Partial<PanelInfo>): PanelInfo => ({
  id, name: 'fig1', folder: '.', kind: 'raster', native_w_mm: 120, native_h_mm: 90, mtime: 0, script: 'plot.py', ...extra,
})
const S = () => useDocumentStore.getState()
const P = () => S().doc.objects[0] as PanelObject

async function seed(p: PanelObject, panels: PanelInfo[]) {
  await S().switchDocument(emptyProject(), 'vector-primary-test')
  S().commit(literal('seed'), (d) => { d.objects.push(p) })
  useDocumentStore.setState({ past: [], future: [] })
  useAssetStore.setState({ panels, byId: {} })
}

const scripted = () => [
  info('pdf/fig1.pdf', { kind: 'pdf', native_w_mm: 121, native_h_mm: 90.5, figure_key: 'script:fig1', primary: true }),
  info('fig1.png', { px_w: 1200, px_h: 900, figure_key: 'script:fig1', primary: false }),
]

beforeEach(() => {
  render.mockReset().mockResolvedValue({ rev: 1, svg: '<svg/>', manifest: { size_mm: [121, 90.5], elements: [] } })
  useSelectionStore.getState().clear()
  setCurrentProjectId('A')
})

describe('PNG → 矢量主文件进编辑', () => {
  it('换成主文件：外框左上角与宽度不变，宽高比差 ≤1% 时高度也不变；可撤销', async () => {
    await seed(pngPanel(), scripted())
    enterElementEdit('p')
    expect(P().fileId).toBe('pdf/fig1.pdf')
    expect(P().fileKind).toBe('pdf')
    expect([P().nativeW, P().nativeH, P().pxW, P().pxH]).toEqual([121, 90.5, undefined, undefined])
    expect([P().x, P().y, P().w, P().h]).toEqual([10, 20, 60, 45])
    // 没有发位图采用请求（带 source_policy 的渲染）
    for (const call of render.mock.calls) expect(JSON.stringify(call)).not.toContain('selected-figsize-v1')
    S().undo()
    expect(P().fileId).toBe('fig1.png')
  })

  it('宽高比明显不同时按主文件的比例算高度', async () => {
    const panels = scripted()
    panels[0] = { ...panels[0], native_w_mm: 100, native_h_mm: 100 }
    await seed(pngPanel(), panels)
    enterElementEdit('p')
    expect([P().w, P().h]).toEqual([60, 60])
  })

  it.each([
    ['裁剪过', { crop: { x: 0.1, y: 0.1, w: 0.5, h: 0.5 } }],
    ['旋转过', { rotation: 90 as const }],
    ['翻转过', { flipH: true }],
  ])('%s的不换（会挪动画面），照旧走位图采用', async (_label, extra) => {
    await seed(pngPanel(extra), scripted())
    enterElementEdit('p')
    expect(P().fileId).toBe('fig1.png')
  })

  it('没脚本的图（目录键）不换', async () => {
    await seed(pngPanel({ script: 'plot.py' }), [
      info('fig1.pdf', { kind: 'pdf', figure_key: 'dir:fig1', primary: true, script: undefined }),
      info('fig1.png', { figure_key: 'dir:fig1', primary: false, script: undefined }),
    ])
    enterElementEdit('p')
    expect(P().fileId).toBe('fig1.png')
  })
})
