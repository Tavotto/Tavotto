import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enginePreviewPngSnapshot, engineRender, type EngineRenderResponse } from '@/lib/api'
import { setEngineTransport } from '@/lib/engineTransport'
import { figureOriginFor, validFigureOrigin } from '@/lib/figureSnapshot'
import { setCurrentProjectId } from '@/lib/session'
import type { FigureOrigin, PanelObject } from '@/types/document'
import { requestRender } from './renderScheduler'
import { captureThumbSources, exactPanelRender, panelRender, renderKeyOf, renderRequestIdentity, renderSourceKey, structuralPanelManifest, useRenderStore } from './renderStore'
import { emptyDocument } from '@/types/document'

const origin = (receipt = 'A'): FigureOrigin => ({ version: 1,
  snapshot: { version: 1, sourceId: 'History.pdf', patchHash: 'P', receiptId: receipt, snapshotId: `snapshot-${receipt}` },
  patches: [{ gid: 'line', prop: 'linewidth', value: 2 }, { gid: 'line', prop: 'linewidth', value: 3 }],
})
const panel = (receipt = 'A', width = 7): PanelObject => ({ id: receipt, type: 'panel', fileId: 'History.pdf', fileKind: 'pdf',
  figureFrame: 1, figureOrigin: origin(receipt), overrides: [{ gid: 'line', prop: 'linewidth', value: width }],
  x: 0, y: 0, w: 80, h: 60, nativeW: 80, nativeH: 60 })
const response = (input: FigureOrigin): EngineRenderResponse => ({ rev: 1, figure_origin: structuredClone(input),
  svg: `<svg id="${input.snapshot.receiptId}"/>`, manifest: { stem: 'History', size_mm: [80, 60], elements: [] } })
let fetchMock: ReturnType<typeof vi.fn>
beforeEach(() => {
  setCurrentProjectId('origin-project')
  useRenderStore.getState().clear()
  fetchMock = vi.fn(async (_url: unknown, init: RequestInit) => {
    const body = JSON.parse(String(init.body))
    return Response.json(response(body.figure_origin))
  })
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => { useRenderStore.getState().clear(); setCurrentProjectId(null); setEngineTransport(null); vi.unstubAllGlobals(); vi.useRealTimers() })
const render = (p: PanelObject) => useRenderStore.getState().render(p.fileId, p.overrides, undefined, undefined, undefined, undefined, 'foreground', undefined, undefined, figureOriginFor(p))

describe('historical Figure render source ownership', () => {
  it('separates same file/current patches across complete historical refs and keeps ordinary keys unchanged', async () => {
    const a = panel('A'), b = panel('B'), ordinary = { ...a, figureOrigin: undefined }
    expect(renderKeyOf(a)).not.toBe(renderKeyOf(b))
    expect(renderKeyOf(ordinary)).toBe(`History.pdf ${JSON.stringify(a.overrides)}`)
    await render(a)
    expect(exactPanelRender(useRenderStore.getState(), a)?.svg).toContain('id="A"')
    expect(panelRender(useRenderStore.getState(), b)).toBeUndefined()
    expect(structuralPanelManifest(useRenderStore.getState(), b)).toBeNull()
    await render(b)
    expect(panelRender(useRenderStore.getState(), panel('A', 12))?.svg).toContain('id="A"')
    expect(panelRender(useRenderStore.getState(), panel('B', 12))?.svg).toContain('id="B"')
    expect(useRenderStore.getState().recent[renderSourceKey(a)]).toEqual([renderKeyOf(a)])
    expect(exactPanelRender(useRenderStore.getState(), panel('A', 12))).toBeNull()
  })

  it('freezes the complete origin and ordered baseline at the real scheduler enqueue boundary', async () => {
    vi.useFakeTimers()
    const p = panel()
    requestRender(p, 'defer')
    p.figureOrigin!.snapshot.receiptId = 'B'
    p.figureOrigin!.patches.reverse()
    p.overrides[0].value = 99
    await vi.advanceTimersByTimeAsync(300)
    expect(JSON.parse(String(fetchMock.mock.calls[0][1].body))).toMatchObject({ figure_origin: origin('A'), patches: [{ gid: 'line', prop: 'linewidth', value: 7 }] })
    expect(exactPanelRender(useRenderStore.getState(), panel('A'))).not.toBeNull()
  })

  it('checks every origin value without depending on JSON member order and rejects wrong or missing echoes', async () => {
    const a = origin()
    fetchMock.mockResolvedValueOnce(Response.json({ ...response(a), figure_origin: { patches: a.patches, snapshot: { ...a.snapshot }, version: 1 } }))
    await expect(engineRender('History.pdf', [], { figure_origin: a })).resolves.toMatchObject({ figure_origin: a })
    for (const echoed of [undefined, null, origin('B'), { ...a, patches: [...a.patches].reverse() }]) {
      fetchMock.mockResolvedValueOnce(Response.json({ ...response(a), figure_origin: echoed }))
      await expect(engineRender('History.pdf', [], { figure_origin: a })).rejects.toMatchObject({ code: 'svg_source_unavailable' })
    }
    expect(validFigureOrigin({ ...a, version: true }, 'History.pdf')).toBe(false)
    expect(validFigureOrigin({ ...a, patches: [{ gid: '', prop: 'x', value: 1 }] }, 'History.pdf')).toBe(false)
    await expect(engineRender('Other.pdf', [], { figure_origin: a })).rejects.toMatchObject({ code: 'svg_source_unavailable' })
    expect(fetchMock).toHaveBeenCalledTimes(5)
  })

  it('rejects a wrong-receipt response and an unsupported host without falling back to current execution', async () => {
    const p = panel()
    fetchMock.mockResolvedValue(Response.json(response(origin('B'))))
    await render(p)
    expect(useRenderStore.getState().byKey[renderKeyOf(p)]).toMatchObject({ status: 'error', stale: true, code: 'svg_source_unavailable' })
    expect(exactPanelRender(useRenderStore.getState(), p)).toBeNull()
    const host = { render: vi.fn(), previewPngUrl: vi.fn(), panelSrc: vi.fn() }
    setEngineTransport(host)
    await render(p)
    expect(host.render).not.toHaveBeenCalled()
    expect(useRenderStore.getState().byKey[renderKeyOf(p)].code).toBe('svg_source_unavailable')
  })

  it('keeps the explicit leaving snapshot project and source after current project changes', async () => {
    let finish!: (response: Response) => void
    fetchMock.mockImplementation(() => new Promise<Response>(resolve => { finish = resolve }))
    const p = panel()
    const doc = { ...emptyDocument(), objects: [p] }
    const sources = captureThumbSources(doc, 'project-A')!
    setCurrentProjectId('project-B')
    p.figureOrigin!.snapshot.receiptId = 'B'
    finish(Response.json({ png: 'YQ==', manifest: { size_mm: [80, 60], elements: [] }, figure_origin: origin('A') }))
    expect(await sources.get(p.id)!.png).toBe('data:image/png;base64,YQ==')
    expect(String(fetchMock.mock.calls[0][0])).toContain('pj=project-A')
    expect(JSON.parse(String(fetchMock.mock.calls[0][1].body)).figure_origin).toEqual(origin('A'))
  })

  it('pairs origin PNG with geometry and rejects a different historical receipt', async () => {
    fetchMock.mockResolvedValueOnce(Response.json({ png: 'YQ==', manifest: { size_mm: [80, 60], elements: [] }, figure_origin: origin('B') }))
    await expect(enginePreviewPngSnapshot('History.pdf', [], 360, undefined, { figure_origin: origin() }, 'A')).rejects.toMatchObject({ code: 'svg_source_unavailable' })
    const p = panel()
    expect(renderRequestIdentity(p.overrides, undefined, undefined, origin())).not.toBe(renderRequestIdentity(p.overrides, undefined, undefined, { ...origin(), patches: [] }))
  })

  it('refuses mixed historical and current-original selection before any request', async () => {
    await expect(engineRender('History.pdf', [], { figure_origin: origin(), source_policy: 'selected-figsize-v1' }))
      .rejects.toMatchObject({ code: 'svg_source_unavailable' })
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
