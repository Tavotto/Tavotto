import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
vi.mock('@/lib/timelineThumb', () => ({ captureThumbSources: vi.fn(() => new Map()), composeTimelineThumb: vi.fn(async () => null) }))
import { captureMoment, takeCheckpoint } from './timelineCheckpoint'
import { currentTimelineCtx } from './timelineContext'
import { useDocumentStore } from '@/store/documentStore'
import { setCurrentProjectId } from './session'
import { emptyProject, type PanelObject } from '@/types/document'
import chartVectors from '../../../tests/golden/chart_model_vectors.json'

const requests: { path: string; body: Record<string, unknown>; pj: string | null }[] = []
beforeEach(async () => {
  localStorage.clear(); requests.length = 0; setCurrentProjectId('p4')
  vi.stubGlobal('fetch', vi.fn(async (url: unknown, init?: RequestInit) => {
    const u = new URL(String(url), 'http://localhost')
    if (init?.method === 'POST') { requests.push({ path: u.pathname, pj: u.searchParams.get('pj'), body: JSON.parse(String(init.body)) }); return Response.json({ version: { id: 'v1' } }) }
    return new Response('{}', { status: 404 })
  }))
  const pd = emptyProject(); pd.schema = 4
  const v = chartVectors.vectors.find(v => v.expect === 'ok')!
  const panel: PanelObject = { id: 'p', type: 'panel', fileId: 'gone.svg', fileKind: 'svg', nativeW: 100, nativeH: 80, x: 0, y: 0, w: 100, h: 80,
    chart: structuredClone({ model: v.model, binding: v.binding }) as unknown as PanelObject['chart'], overrides: [{ gid: 'axes_0.lines_0', prop: 'linewidth', value: 6 }] }
  pd.canvases[0].objects = [panel]; pd.canvases.push({ ...structuredClone(pd.canvases[0]), id: 'other-canvas', name: 'Other' })
  pd.activeCanvasId = pd.canvases[0].id
  await useDocumentStore.getState().switchDocument(pd, 'v4_doc')
})
afterEach(() => { vi.unstubAllGlobals(); setCurrentProjectId(null) })

describe('v4版本仍是单画布捕获', () => {
  it('实际POST只包活动画布及完整model/ref/原序edits，不把整个项目当版本', async () => {
    const before = useDocumentStore.getState(), snapshot = captureMoment()
    await takeCheckpoint({ auto: false, allowEmpty: true }, snapshot)
    const req = requests.at(-1)!
    expect(req.path).toBe('/api/documents/v4/versions/v4_doc')
    const pd = req.body.doc as ReturnType<typeof before.buildProject>
    expect(pd.schema).toBe(4); expect(pd.canvases).toHaveLength(1)
    expect(pd.canvases[0].id).toBe(before.activeCanvasId)
    expect(pd.canvases[0].objects).toEqual(before.doc.objects)
    expect(pd.project).toEqual({ id: before.projectMeta.id, name: before.projectMeta.name })
    expect(useDocumentStore.getState().past).toBe(before.past)
  })
  it('await前快照捕获format/pj/身份，之后换格式也不改实际发送的版本地址', async () => {
    const snapshot = captureMoment(), ctx = currentTimelineCtx()
    setCurrentProjectId('p_new')
    await useDocumentStore.getState().switchDocument(emptyProject(), 'v4_doc')
    expect(currentTimelineCtx()).not.toBe(ctx)
    await takeCheckpoint({ auto: false, allowEmpty: true }, snapshot)
    expect(requests.at(-1)?.path).toBe('/api/documents/v4/versions/v4_doc')
    expect(requests.at(-1)?.pj).toBe('p4')
  })
})
