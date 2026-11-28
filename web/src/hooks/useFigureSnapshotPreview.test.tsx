/**
 * 归档预览的请求归属闸（L-123）：预览请求按文档归属项目发。文档归属项目不是当前
 * 认领的项目（在 B 里打开已关闭的 A 的最近排版）时不发这条请求——它的 409
 * no_project 会触发 API 层全局 project-drop，把健康的当前项目也甩出画布。
 * 面板退「近似预览」角标（failed），绝不拿异主请求赌运气。
 */
import { act } from 'react'
import { createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useFigureSnapshotPreview } from './useFigureSnapshotPreview'
import { setCurrentProjectId } from '@/lib/session'
import type { PreviewOwner } from '@/lib/previewOwner'
import type { PanelObject } from '@/types/document'

const fetchPreview = vi.fn()
vi.mock('@/lib/api', () => ({ fetchFigureSnapshotPreview: (...args: unknown[]) => fetchPreview(...args) }))

globalThis.IS_REACT_ACT_ENVIRONMENT = true

const SNAPSHOT = { version: 1, sourceId: 'Fig1.pdf', patchHash: 'p'.repeat(8), receiptId: 'r'.repeat(8), snapshotId: 's'.repeat(8) }
const PREVIEW = { preview_source: 'figure_snapshot', figure_snapshot: SNAPSHOT, svg: '<svg/>', size_mm: [40, 30] }

const panel = {
  id: 'p1', type: 'panel', fileId: 'Fig1.pdf', fileKind: 'pdf',
  x: 0, y: 0, w: 40, h: 30, overrides: [], figureSnapshot: SNAPSHOT,
} as unknown as PanelObject

const ownerOf = (pj: string | null): PreviewOwner => ({
  pj,
  key: `owner-${pj}`,
  still: () => true,
  refresh: () => ownerOf(pj),
})

let host: HTMLDivElement, root: Root
let latest: { url: string | null; loading: boolean; failed: boolean }

/** 每次渲染都换新的 Probe 组件实例，让 effect 按新 owner 重跑 */
function mountWith(owner: PreviewOwner): void {
  let renders = 0
  const Probe = () => {
    renders += 1
    latest = useFigureSnapshotPreview(panel, true, owner)
    return createElement('div', { 'data-probe': renders })
  }
  act(() => {
    root.render(createElement(Probe))
  })
}

beforeEach(() => {
  fetchPreview.mockReset()
  setCurrentProjectId(null)
  URL.createObjectURL = vi.fn(() => `blob:mock/${Math.random()}`)
  URL.revokeObjectURL = vi.fn()
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(() => {
  act(() => root.unmount())
  host.remove()
})

describe('归档预览的请求归属闸', () => {
  it('文档归属项目 ≠ 当前项目：不发请求，直接 failed（近似预览）', async () => {
    setCurrentProjectId('proj-B')
    await act(async () => mountWith(ownerOf('proj-A')))
    expect(fetchPreview).not.toHaveBeenCalled()
    expect(latest).toEqual({ url: null, loading: false, failed: true })
  })

  it('同一项目照常请求并落到预览位', async () => {
    setCurrentProjectId('proj-A')
    fetchPreview.mockResolvedValue(structuredClone(PREVIEW))
    await act(async () => mountWith(ownerOf('proj-A')))
    expect(fetchPreview).toHaveBeenCalledTimes(1)
    expect(fetchPreview.mock.calls[0][4]).toBe('proj-A')
    await act(async () => {})
    expect(latest).toMatchObject({ url: expect.any(String), loading: false, failed: false })
  })
})
