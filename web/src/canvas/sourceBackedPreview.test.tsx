import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { PanelView } from './PanelView'
import { clearVariantPngCache } from '@/hooks/useVariantPng'
import { useRenderStore, renderKeyOf } from '@/store/renderStore'
import { useMountedPngStore } from '@/store/mountedPngStore'
import { useUiStore } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'
import { VECTOR_PREVIEW } from '@/lib/previewBudget'
import { SCRIPT_BACKED_SOURCE_POLICY, type SourceBackedFigure } from '@/lib/artifactValidation'
import type { PanelObject } from '@/types/document'
import type { Manifest } from '@/lib/api'

globalThis.IS_REACT_ACT_ENVIRONMENT = true
const preview = vi.fn()
vi.mock('@/lib/api', async (original) => ({ ...(await original<typeof import('@/lib/api')>()), enginePreviewPngSnapshot: (...a: unknown[]) => preview(...a) }))

const SHA = 'c'.repeat(64)
const guard: SourceBackedFigure = {
  version: 1, policy: SCRIPT_BACKED_SOURCE_POLICY, sourceId: 'plot.png', bytesSha256: 'a'.repeat(64), sizeBytes: 10,
  status: 'reconstructed', reconstructionSha256: SHA, confirmedAt: '2026-10-20T10:00:00.000Z',
}
const FRAME = { gid: 'figure', prop: 'frame', value: 'figsize' }
const TITLE = { gid: 'axes_0.title', prop: 'fontsize', value: 14 }
const zero: PanelObject = { id: 'p', type: 'panel', fileId: 'plot.png', fileKind: 'raster', script: 'figure.py', x: 0, y: 0, w: 100, h: 80, nativeW: 100, nativeH: 80, overrides: [FRAME], sourceBackedFigure: guard }
const edited: PanelObject = { ...zero, overrides: [FRAME, TITLE] }
const manifest = { stem: 'plot', size_mm: [100, 80], elements: [{ gid: 'figure', role: 'figure', bbox: [0, 0, 1, 1], editable: [] }] } as unknown as Manifest
let el: HTMLDivElement, root: Root

beforeEach(() => {
  el = document.createElement('div'); document.body.appendChild(el); root = createRoot(el)
  useRenderStore.getState().clear(); useMountedPngStore.setState({ byPanel: {} }); useUiStore.getState().setElementPanel(null)
  useViewportStore.setState({ zoom: 1 }); preview.mockReset(); clearVariantPngCache(); URL.revokeObjectURL = vi.fn()
})
afterEach(() => { act(() => root.unmount()); el.remove() })

function seed(p: PanelObject, svg = '<svg id="engine"/>') {
  useRenderStore.getState().patch(renderKeyOf(p), {
    fileId: p.fileId, artifactValidation: guard, rev: 1, svg, svgBytes: svg.length, manifest, status: 'ready',
    lastPatches: JSON.stringify(p.overrides), wantPatches: JSON.stringify(p.overrides), preview: VECTOR_PREVIEW,
  })
}
const visibleImg = () => el.querySelector('img:not([aria-hidden="true"])') as HTMLImageElement | null

describe('zero edits show the pixels on disk', () => {
  it('outside edit mode: the original file, no engine request', async () => {
    seed(zero)
    await act(async () => root.render(<PanelView obj={zero} />))
    expect(visibleImg()!.getAttribute('src')).toMatch(/\/api\/file\?.*plot\.png/)
    expect(preview).not.toHaveBeenCalled()
    expect(el.querySelector('[data-element-svg]')).toBeNull()
  })
  it.each(['exact', 'reconstructed'] as const)('%s: outside edit → original, entering edit → the redraw (visible SVG), leaving edit still unedited → original again', async (status) => {
    const g = status === 'exact' ? { ...guard, status: 'exact' as const, confirmedAt: undefined } : guard
    const p: PanelObject = { ...zero, sourceBackedFigure: g as SourceBackedFigure }
    seed(p)
    await act(async () => root.render(<PanelView obj={p} />))
    expect(visibleImg()!.getAttribute('src')).toMatch(/\/api\/file\?.*plot\.png/)
    expect(el.querySelector('[data-element-svg]')).toBeNull()
    // 进入图内编辑：可编辑基础可见，首次拖动的预览才看得见
    await act(async () => useUiStore.getState().setElementPanel('p'))
    const svg = el.querySelector('[data-element-svg]') as HTMLElement
    expect(svg).not.toBeNull(); expect(svg.style.visibility).not.toBe('hidden')
    expect(el.querySelector('img[data-original-pixels]')).toBeNull()
    expect(el.querySelector('img')).toBeNull()
    expect(preview).not.toHaveBeenCalled()
    // 退出编辑、仍是零编辑：回到磁盘原像素
    await act(async () => useUiStore.getState().setElementPanel(null))
    expect(el.querySelector('[data-element-svg]')).toBeNull()
    expect(visibleImg()!.getAttribute('src')).toMatch(/\/api\/file\?.*plot\.png/)
    expect(preview).not.toHaveBeenCalled()
  })
  it('a huge (raster-tier) figure: the engine bitmap exists only while editing; outside editing it is the original', async () => {
    useRenderStore.getState().patch(renderKeyOf(zero), {
      fileId: zero.fileId, artifactValidation: guard, rev: 1, svg: null, svgBytes: 0, manifest, status: 'ready',
      lastPatches: JSON.stringify(zero.overrides), wantPatches: JSON.stringify(zero.overrides),
      preview: { mode: 'raster' } as never,
    })
    preview.mockResolvedValue({ url: 'data:image/png;base64,ENGINE', manifest })
    await act(async () => root.render(<PanelView obj={zero} />))
    expect(visibleImg()!.getAttribute('src')).toMatch(/\/api\/file\?.*plot\.png/)
    expect(preview).not.toHaveBeenCalled()
    await act(async () => useUiStore.getState().setElementPanel('p'))
    await vi.waitFor(() => expect(visibleImg()!.getAttribute('src')).toContain('ENGINE'))
    await act(async () => useUiStore.getState().setElementPanel(null))
    expect(visibleImg()!.getAttribute('src')).toMatch(/\/api\/file\?.*plot\.png/)
  })
})

describe('real edits show the whole-figure redraw', () => {
  it('outside edit mode: the engine PNG request carries the policy, the source identity and the confirmed digest', async () => {
    seed(edited); preview.mockResolvedValue({ url: 'data:image/png;base64,ENGINE', manifest })
    await act(async () => root.render(<PanelView obj={edited} />))
    expect(preview).toHaveBeenCalled()
    expect(preview.mock.calls[0][1]).toEqual([FRAME, TITLE])
    expect(preview.mock.calls[0][4]).toEqual({
      source_policy: SCRIPT_BACKED_SOURCE_POLICY,
      expected_source: { bytes_sha256: 'a'.repeat(64), size_bytes: 10 },
      confirmed_reconstruction_sha256: SHA,
    })
    await vi.waitFor(() => expect(visibleImg()!.getAttribute('src')).toContain('ENGINE'))
    expect(el.querySelector('img[data-original-pixels]')).toBeNull()
  })
  it('in edit mode: the SVG is visible and no stale original sits on top of it', async () => {
    seed(edited); useUiStore.getState().setElementPanel('p')
    await act(async () => root.render(<PanelView obj={edited} />))
    const svg = el.querySelector('[data-element-svg]') as HTMLElement
    expect(svg.style.visibility).not.toBe('hidden')
    expect(el.querySelector('img[data-original-pixels]')).toBeNull()
  })
  it('undo back to zero edits: still editing → the redraw; leaving edit → the original pixels, no engine PNG left over', async () => {
    seed(zero); seed(edited); useUiStore.getState().setElementPanel('p')
    await act(async () => root.render(<PanelView obj={edited} />))
    await act(async () => root.render(<PanelView obj={zero} />))
    expect((el.querySelector('[data-element-svg]') as HTMLElement).style.visibility).not.toBe('hidden')
    useUiStore.getState().setElementPanel(null)
    await act(async () => root.render(<PanelView obj={zero} />))
    expect(visibleImg()!.getAttribute('src')).toMatch(/\/api\/file\?.*plot\.png/)
    expect(String(visibleImg()!.getAttribute('src'))).not.toContain('ENGINE')
  })
})
