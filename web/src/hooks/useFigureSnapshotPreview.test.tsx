import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchFigureSnapshotPreview } from '@/lib/api'
import type { FigureSnapshot, PanelObject } from '@/types/document'
import type { PreviewOwner } from '@/lib/previewOwner'
import { useFigureSnapshotPreview } from './useFigureSnapshotPreview'

vi.mock('@/lib/api', () => ({ fetchFigureSnapshotPreview: vi.fn() }))
vi.mock('@/hooks/useDocumentPreviewOwner', () => ({
  useDocumentPreviewOwner: (_subject: unknown, owner: PreviewOwner) => owner,
}))

globalThis.IS_REACT_ACT_ENVIRONMENT = true
const fetchPreview = vi.mocked(fetchFigureSnapshotPreview)
let root: Root
let container: HTMLDivElement
let last: ReturnType<typeof useFigureSnapshotPreview>
let createUrl: ReturnType<typeof vi.fn>
let revokeUrl: ReturnType<typeof vi.fn>

function panel(letter = 'a'): PanelObject {
  return {
    id: 'saved-panel', type: 'panel', fileId: 'History.pdf', fileKind: 'pdf',
    x: 0, y: 0, w: 20, h: 10, nativeW: 20, nativeH: 10, overrides: [],
    figureSnapshot: { version: 1, sourceId: 'History.pdf', snapshotId: letter.repeat(64),
      receiptId: `sha256:${letter.repeat(64)}`, patchHash: `sha256:${'c'.repeat(64)}` },
  }
}

function owner(pj: string | null = 'project-A', still = () => true): PreviewOwner {
  const captured: PreviewOwner = { pj, key: `${pj}:original-doc`, still, refresh: () => captured }
  return captured
}

const response = (p: PanelObject) => ({
  preview_source: 'figure_snapshot' as const, figure_snapshot: p.figureSnapshot as FigureSnapshot,
  svg: '<svg xmlns="http://www.w3.org/2000/svg"><text>Saved figure</text></svg>', size_mm: [20, 10] as [number, number],
})

function Probe({ object, captured, enabled = true }: { object: PanelObject; captured: PreviewOwner; enabled?: boolean }) {
  last = useFigureSnapshotPreview(object, enabled, captured)
  return last.url ? <img data-frozen-picture src={last.url} alt="" /> : null
}

beforeEach(() => {
  fetchPreview.mockReset()
  let serial = 0
  createUrl = vi.fn(() => `blob:archive-${++serial}`)
  revokeUrl = vi.fn()
  vi.stubGlobal('URL', Object.assign(class extends URL {}, { createObjectURL: createUrl, revokeObjectURL: revokeUrl }))
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
})

afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
  vi.unstubAllGlobals()
})

describe('归档Figure图像的正文归属与生命周期', () => {
  it('显式null项目和完整ref进入请求，SVG只作为图片Blob，不挂编辑DOM', async () => {
    const p = panel()
    fetchPreview.mockResolvedValue(response(p))
    await act(async () => root.render(<Probe object={p} captured={owner(null)} />))
    expect(fetchPreview).toHaveBeenCalledWith(p.fileId, p.overrides, p.figureSnapshot, expect.any(AbortSignal), null)
    expect(createUrl.mock.calls[0][0]).toBeInstanceOf(Blob)
    expect((createUrl.mock.calls[0][0] as Blob).type).toBe('image/svg+xml')
    expect(container.querySelector('img[data-frozen-picture]')?.getAttribute('src')).toBe('blob:archive-1')
    expect(container.querySelector('svg')).toBeNull()
  })

  it('同文件/patch换完整ref立即收起旧图，迟到A不替换B', async () => {
    const a = panel('a'), b = panel('b'), captured = owner()
    let resolveA!: (value: ReturnType<typeof response>) => void
    let resolveB!: (value: ReturnType<typeof response>) => void
    fetchPreview.mockImplementationOnce(() => new Promise(resolve => { resolveA = resolve }))
      .mockImplementationOnce(() => new Promise(resolve => { resolveB = resolve }))
    await act(async () => root.render(<Probe object={a} captured={captured} />))
    await act(async () => root.render(<Probe object={b} captured={captured} />))
    expect(last.url).toBeNull()
    expect(fetchPreview.mock.calls[1][2]).toEqual(b.figureSnapshot)
    await act(async () => resolveB(response(b)))
    expect(last.url).toBe('blob:archive-1')
    await act(async () => resolveA(response(a)))
    expect(last.url).toBe('blob:archive-1')
    expect(createUrl).toHaveBeenCalledTimes(1)
  })

  it('换历史与卸载分别回收本次创建的图片URL', async () => {
    const a = panel('a'), b = panel('b'), captured = owner()
    fetchPreview.mockImplementation(async (_id, _patches, ref) => response({ ...a, figureSnapshot: ref }))
    await act(async () => root.render(<Probe object={a} captured={captured} />))
    await act(async () => root.render(<Probe object={b} captured={captured} />))
    expect(revokeUrl).toHaveBeenCalledWith('blob:archive-1')
    await act(async () => root.render(null))
    expect(revokeUrl.mock.calls).toEqual([['blob:archive-1'], ['blob:archive-2']])
  })

  it('正文失去归属后不采纳迟到图像', async () => {
    const p = panel()
    let active = true
    const captured = owner('project-A', () => active)
    let resolve!: (value: ReturnType<typeof response>) => void
    fetchPreview.mockImplementationOnce(() => new Promise(done => { resolve = done }))
    await act(async () => root.render(<Probe object={p} captured={captured} />))
    active = false
    await act(async () => root.render(<Probe object={p} captured={captured} />))
    await act(async () => resolve(response(p)))
    expect(last).toEqual({ url: null, loading: false, failed: false })
    expect(createUrl).not.toHaveBeenCalled()
  })

  it('显式坏ref保持失败，不发送请求或回退同名素材', async () => {
    const p = { ...panel(), figureSnapshot: null } as unknown as PanelObject
    await act(async () => root.render(<Probe object={p} captured={owner()} />))
    expect(last).toEqual({ url: null, loading: false, failed: true })
    expect(fetchPreview).not.toHaveBeenCalled()
    expect(container.childElementCount).toBe(0)
  })

  it('未启用时不读取档案', async () => {
    await act(async () => root.render(<Probe object={panel()} captured={owner()} enabled={false} />))
    expect(fetchPreview).not.toHaveBeenCalled()
    expect(last).toEqual({ url: null, loading: false, failed: false })
  })
})
