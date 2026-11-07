import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { canvasToDoc, emptyProject, type PanelObject } from '@/types/document'

beforeEach(() => { vi.stubGlobal('fetch', vi.fn(async () => Response.json({}, { status: 404 }))) })

afterEach(async () => {
  const { setCurrentProjectId } = await import('@/lib/session')
  setCurrentProjectId(null)
  vi.unstubAllGlobals()
})

describe('同步保存时刻捕获端口', () => {
  it('没有实际renderer装配时明确拒绝，不能用缺省null图源伪装snapshot', async () => {
    vi.resetModules()
    const { captureDocumentMoment, MomentCaptureUnavailable } = await import('./momentCapture')
    const project = emptyProject()
    const state = { doc: canvasToDoc(project.canvases[0]), activeCanvasId: project.activeCanvasId,
      documentId: 'unconfigured', documentFormat: 'legacy' as const,
      projectMeta: { ...project.project, createdAt: project.createdAt } }
    expect(() => captureDocumentMoment(state, 'p', 0)).toThrow(MomentCaptureUnavailable)
    expect(state.doc.objects).toEqual([])
  })

  it.each(['documentStore', 'App', 'embedded', 'mcp', 'playground'] as const)('%s实际入口在首个同步capture前装配真实renderer，不靠时间线挂载', async entry => {
    vi.resetModules()
    const port = await import('./momentCapture')
    if (entry === 'documentStore') await import('@/store/documentStore')
    else if (entry === 'App') await import('@/App')
    else if (entry === 'embedded') await import('@/embedded/session')
    else if (entry === 'mcp') await import('@/mcp/session')
    else await import('@/playground/playgroundSession')
    const { useDocumentStore } = await import('@/store/documentStore')
    const moment = port.captureDocumentMoment(useDocumentStore.getState(), null, 0)
    expect(moment.identity.doc).toBe(useDocumentStore.getState().doc)
    expect(moment.thumb).toBeInstanceOf(Map)
  }, 15_000)

  it('冻结原文档/画布/项目/format及实际SVG，后续修改renderer和文档不重新取源', async () => {
    vi.resetModules()
    const { useDocumentStore } = await import('@/store/documentStore')
    const { useRenderStore, renderKeyOf } = await import('@/store/renderStore')
    const { setCurrentProjectId } = await import('@/lib/session')
    const { captureDocumentMoment } = await import('./momentCapture')
    setCurrentProjectId(null)
    const project = emptyProject(); project.schema = 4
    const panel: PanelObject = { id: 'panel', type: 'panel', fileId: 'figure.pdf', fileKind: 'pdf', nativeW: 100, nativeH: 80,
      x: 0, y: 0, w: 100, h: 80, overrides: [] }
    project.canvases[0].objects = [panel]
    await useDocumentStore.getState().switchDocument(project, 'frozen', undefined, { format: 'v4' })
    const key = renderKeyOf(panel)
    const render = { svg: '<svg id="original"/>', manifest: null, fileId: panel.fileId, lastPatches: [], stale: false, status: 'ready' as const }
    useRenderStore.setState({ byKey: { [key]: render as never }, latest: { [panel.fileId]: key } })
    const before = useDocumentStore.getState()
    const moment = captureDocumentMoment(before, 'original-project', 4)
    useRenderStore.setState({ byKey: { [key]: { ...render, svg: '<svg id="later"/>' } as never } })
    useDocumentStore.getState().commit({ key: 'test', ns: 'workspace' }, draft => { draft.name = 'later' })
    setCurrentProjectId('later-project')
    expect(moment.identity.doc).toBe(before.doc)
    expect(moment.identity).toMatchObject({ documentId: 'frozen', format: 'v4', pj: 'original-project', canvasId: before.activeCanvasId })
    expect(moment.ctx).toBe(JSON.stringify([4, 'v4', 'frozen']))
    expect(moment.thumb?.get('panel')?.svg).toBe('<svg id="original"/>')
  })
})
