/**
 * runtime 面板「还没有引擎图」时的预览（QA-WIN-21）：按**此刻的 overrides** 取图，而且只接受
 * **发起它的那一代**的回包——项目换了、或同一份文档被重新载入（`loadSeq` +1），上一代的图
 * 不许挂到这一代的面板上。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useRuntimePreview, type RuntimePreview } from './useRuntimePreview'
import { setCurrentProjectId } from '@/lib/session'
import { useDocumentStore } from '@/store/documentStore'
import type { FigureSnapshot } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const fetchState = vi.fn()
const fetchArchive = vi.fn()

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchRuntimePreviewForState: (id: string, patches: unknown[], signal?: AbortSignal, pj?: string | null) =>
    fetchState(id, patches, signal, pj),
  fetchFigureSnapshotPreview: (id: string, patches: unknown[], snapshot: FigureSnapshot, signal?: AbortSignal, pj?: string | null) =>
    fetchArchive(id, patches, snapshot, signal, pj),
}))

const ID = 'runtime:a.png'

let created = 0
let revoked: string[] = []
let container: HTMLDivElement
let root: Root
/** 最近一次渲染读到的 hook 结果 */
let last: RuntimePreview

function Probe({ snapshot }: { snapshot?: FigureSnapshot }) {
  last = useRuntimePreview(ID, [], true, 0, snapshot)
  return null
}

/** 取数是一个 microtask 链（fetch → blob → setState），冲干净再断言 */
const flush = () =>
  act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })

beforeEach(() => {
  fetchState.mockReset()
  fetchArchive.mockReset()
  fetchState.mockResolvedValue({ blob: new Blob(['png']), exact: true })
  created = 0
  revoked = []
  setCurrentProjectId('projA')
  globalThis.URL.createObjectURL = vi.fn(() => `blob:url-${++created}`)
  globalThis.URL.revokeObjectURL = vi.fn((u: string) => {
    revoked.push(u)
  })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
})

describe('按状态取图', () => {
  it('取到之前是空的（不借用别的状态的图），取到之后挂上这一版', async () => {
    let finish!: (v: { blob: Blob; exact: boolean }) => void
    fetchState.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve
        }),
    )
    await act(async () => {
      root.render(<Probe />)
    })
    expect(last.url).toBeNull()
    await act(async () => {
      finish({ blob: new Blob(['png']), exact: true })
    })
    expect(last.url).toBe('blob:url-1')
    expect(last.exact).toBe(true)
  })

  /**
   * 整份恢复 / 重新载入**同一个** `documentId`：项目与文件都没变，只有载入代次 +1。
   * 上一代的回包晚到，不许覆盖这一代的图，也不许先被做成 blob URL（那等于把旧图接过来再还回去）。
   */
  it('同一份文档重新载入后，上一代的晚到回包不落地', async () => {
    let finishOld!: (v: { blob: Blob; exact: boolean }) => void
    fetchState.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishOld = resolve
        }),
    )
    await act(async () => {
      root.render(<Probe />)
    })
    await act(async () => {
      useDocumentStore.setState({ loadSeq: useDocumentStore.getState().loadSeq + 1 })
    })
    await flush()
    const current = last.url
    expect(current).not.toBeNull()
    expect(fetchState).toHaveBeenCalledTimes(2)

    await act(async () => {
      finishOld({ blob: new Blob(['old']), exact: true })
    })
    expect(last.url).toBe(current)
    expect(created, '上一代的回包连 blob URL 都不该建').toBe(1)
    expect(revoked).not.toContain(current)
  })

  it('项目切换后，同一归档引用只接收新项目的预览', async () => {
    const snapshot: FigureSnapshot = {
      version: 1, sourceId: ID, patchHash: 'patch', receiptId: 'receipt', snapshotId: 'archive',
    }
    let finishOld!: (value: { svg: string }) => void
    fetchArchive
      .mockImplementationOnce(() => new Promise((resolve) => { finishOld = resolve }))
      .mockResolvedValueOnce({ svg: '<svg id="current"/>' })
    await act(async () => {
      root.render(<Probe snapshot={snapshot} />)
    })
    await act(async () => {
      setCurrentProjectId('projB')
    })
    await flush()
    const current = last.url
    expect(current).toBe('blob:url-1')
    expect(last.exact).toBe(true)
    expect(fetchArchive.mock.calls.map((c) => c[4])).toEqual(['projA', 'projB'])
    expect(fetchState).not.toHaveBeenCalled()

    await act(async () => {
      finishOld({ svg: '<svg id="stale"/>' })
    })
    expect(last.url).toBe(current)
    expect(created).toBe(1)
    expect(revoked).not.toContain(current)
  })

  /** 后端按「项目 + 相对路径」解析 runtime 图：请求必须带上发起时的项目 */
  it('请求带上发起时的项目 id', async () => {
    await act(async () => {
      root.render(<Probe />)
    })
    await flush()
    expect(fetchState.mock.calls.map((c) => c[3])).toEqual(['projA'])
  })
})
