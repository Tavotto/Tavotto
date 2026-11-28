/**
 * renderStore 注入给时间线的归档感知图源捕获（ADR 0101 + 0182）：
 * 归档 Figure 面板在发起那一刻冻结引用与项目，缩略图源是 `png`（按归档引用现算的
 * 位图），绝不是 live 素材 URL——同名文件换了也不能画出替换后的样子。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const enginePreviewPng = vi.fn()
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  enginePreviewPng: (...args: unknown[]) => enginePreviewPng(...args),
  enginePreviewPngSnapshot: vi.fn(),
  panelSrc: vi.fn(() => null),
}))

import { captureThumbSources } from './renderStore'
import { TIMELINE_THUMB_PX } from '@/lib/momentCapture'
import { emptyProject, type FigureDocument, type PanelObject } from '@/types/document'
import { useDocumentStore } from './documentStore'

const SNAPSHOT = { version: 1 as const, sourceId: 'Fig1.pdf', patchHash: 'p'.repeat(8), receiptId: 'r'.repeat(8), snapshotId: 's'.repeat(8) }



beforeEach(async () => {
  enginePreviewPng.mockReset()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_thumb_sources')
})

const docWith = (panel: Partial<PanelObject>): FigureDocument => {
  const base = { schema: 3, name: 'doc', guides: [], activeCanvasId: 'c1', canvases: [] } as Record<string, unknown>
  base.objects = [{ id: 'p1', type: 'panel', fileId: 'Fig1.pdf', fileKind: 'pdf',
    x: 0, y: 0, w: 40, h: 30, nativeW: 40, nativeH: 30, overrides: [], hidden: false, ...panel }]
  base.page = { w: 100, h: 80 }
  return base as unknown as FigureDocument
}

describe('captureThumbSources（归档感知）', () => {
  it('归档 Figure 面板：冻结引用按归档出图，给 png 不给 live url', async () => {
    enginePreviewPng.mockResolvedValue(new Blob(['<svg/>'], { type: 'image/svg+xml' }))
    const sources = captureThumbSources(docWith({ figureSnapshot: SNAPSHOT }), 'proj-thumb')
    const src = sources!.get('p1')!
    expect(src).not.toBeNull()
    expect(src!.url).toBeUndefined()
    expect(enginePreviewPng).toHaveBeenCalledWith('Fig1.pdf', [], TIMELINE_THUMB_PX, undefined,
      { figure_snapshot: SNAPSHOT }, 'proj-thumb')
    await expect(src!.png).resolves.toMatch(/^data:/)
  })

  it('普通面板仍走显示 SVG / 素材 URL；隐藏面板不取', () => {
    const doc = docWith({})
    doc.objects.push({ id: 'p2', type: 'panel', fileId: 'Fig2.pdf', fileKind: 'pdf',
      x: 0, y: 0, w: 40, h: 30, nativeW: 40, nativeH: 30, overrides: [], hidden: true } as PanelObject)
    const sources = captureThumbSources(doc, 'proj-thumb')
    expect(sources!.has('p1')).toBe(true)
    expect(sources!.has('p2')).toBe(false)
  })
})
