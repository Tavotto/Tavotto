import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
vi.mock('@/lib/api', async (original) => ({ ...(await original<typeof import('@/lib/api')>()), fetchTimeline: vi.fn(), fetchVersionDoc: vi.fn() }))
vi.mock('@/hooks/useVariantPng', async (original) => ({ ...(await original<typeof import('@/hooks/useVariantPng')>()), useVariantPng: vi.fn(() => ({ url: 'data:image/png;base64,cGFpcmVk', approximate: false, loading: false })) }))
import { fetchTimeline, fetchVersionDoc, type LayoutVersionMeta } from '@/lib/api'
import { useVariantPng } from '@/hooks/useVariantPng'
import { emptyProject, type FigureDocument, type PanelObject } from '@/types/document'
import { useDocumentStore } from '@/store/documentStore'
import { useTimelineStore } from '@/store/timelineStore'
import { useUiStore } from '@/store/uiStore'
import { TimelinePanel } from './VersionDialog'
import { VersionThumbImage } from './versionThumbCrop'
import { TooltipProvider } from './ui/Tooltip'
import chartVectors from '../../../tests/golden/chart_model_vectors.json'

globalThis.IS_REACT_ACT_ENVIRONMENT = true
let root: Root | undefined
const meta: LayoutVersionMeta = { id: 'v1', name: 'Chart', ts: Date.now(), auto: false, description: '', objects: 1, canvasId: 'c1', page: { w: 100, h: 80 } }
let captured: FigureDocument
beforeEach(async () => {
  document.body.innerHTML = ''; vi.clearAllMocks(); localStorage.clear()
  const vector = chartVectors.vectors.find(v => v.expect === 'ok')!
  const p: PanelObject = { id: 'p1', type: 'panel', fileId: 'deleted.svg', fileKind: 'svg', x: 0, y: 0, w: 100, h: 80, nativeW: 100, nativeH: 80,
    overrides: [{ gid: 'axes_0.lines_0', prop: 'linewidth', value: 6 }], chart: structuredClone({ model: vector.model, binding: vector.binding }) as unknown as PanelObject['chart'] }
  p.chartSource = { schema: 1, sourceId: p.fileId, bytesSha256: 'a'.repeat(64), sizeBytes: 200, sceneDigest: p.chart!.model.scene_digest }
  const pd = emptyProject(); pd.schema = 4; pd.canvases[0].id = 'c1'; pd.activeCanvasId = 'c1'; pd.canvases[0].objects = [p]
  await useDocumentStore.getState().switchDocument(pd, 'format_ui')
  captured = structuredClone(useDocumentStore.getState().doc)
  vi.mocked(fetchTimeline).mockResolvedValue({ versions: [meta] })
  vi.mocked(fetchVersionDoc).mockResolvedValue({ ...meta, doc: captured })
  useTimelineStore.setState({ preview: null, restoring: null, restoreToken: null }); useUiStore.setState({ rightTab: 'timeline', rightOpen: true })
})
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined })
const mount = async (node: React.ReactNode) => { const host = document.createElement('div'); document.body.append(host); root = createRoot(host); await act(async () => { root!.render(node) }) }

describe('schema4版本UI', () => {
  it('没有存图时列表不拉正文，用户打开卡片才以v4读正文并用完整ref/edits请求配对PNG', async () => {
    await mount(<TooltipProvider><TimelinePanel /></TooltipProvider>)
    expect(fetchTimeline).toHaveBeenCalledWith('format_ui', expect.any(Object), 'v4')
    expect(fetchVersionDoc).not.toHaveBeenCalled()
    const before = useDocumentStore.getState()
    await act(async () => { document.querySelector<HTMLButtonElement>('[data-timeline-row]')!.click() })
    expect(fetchVersionDoc).toHaveBeenCalledWith('format_ui', 'v1', 'v4')
    const p = captured.objects[0] as PanelObject
    expect(vi.mocked(useVariantPng).mock.calls.some(args => args[0] === p.fileId && args[1] === p.overrides && args[3] === true && args[6] === p.chart && args[7] === p.chartSource)).toBe(true)
    expect(document.querySelector('[data-timeline-card] img')?.getAttribute('src')).toBe('data:image/png;base64,cGFpcmVk')
    expect(useDocumentStore.getState().doc).toBe(before.doc); expect(useDocumentStore.getState().past).toBe(before.past)
  })
  it('存好的thumb使用v4地址，crop几何保持同一参数', async () => {
    await mount(<VersionThumbImage docId="same" format="v4" meta={{ ...meta, thumb: 'png', thumbBox: { x: 10, y: 5, w: 50, h: 40 } }} page={{ w: 100, h: 80 }} />)
    const img = document.querySelector('img')!
    expect(img.src).toContain('/api/documents/v4/versions/same/v1/thumb?f=png')
    expect(img.style.left).toBe('10%'); expect(img.style.width).toBe('50%')
  })
})
