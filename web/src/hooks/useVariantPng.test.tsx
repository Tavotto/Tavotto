/**
 * 版本缩略图缓存的**身份与回收**（#137 评审 P1）。
 *
 * 缓存键少带一个维度的后果不是「慢一点」，而是**把别的图当成这一版的样子
 * 显示出来**，而且一次请求都不发——正是 issue #131 要修掉的那类「无提示地
 * 冒充版本视觉状态」。`fileId` 是项目内相对路径，两个项目里同名同 overrides
 * 的图完全可能是两张不同的图。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { clearVariantPngCache, useVariantPng, variantNeedsRender } from './useVariantPng'
import { setCurrentProjectId } from '@/lib/session'
import { setEngineTransport } from '@/lib/engineTransport'
import type { ChartSourceRef, FigureOrigin, PanelChartState } from '@/types/document'
import chartVectors from '../../../tests/golden/chart_model_vectors.json'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const previewPng = vi.fn()
const snapshotPng = vi.fn()

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  enginePreviewPng: (id: string, patches: unknown[], bucket: number, signal?: AbortSignal) =>
    previewPng(id, patches, bucket, signal),
  enginePreviewPngSnapshot: (...args: unknown[]) => snapshotPng(...args),
}))

const OV = [{ gid: 't1', prop: 'pos_frac', value: [0.1, 0.2] }]

let created = 0
let revoked: string[] = []
let container: HTMLDivElement
let root: Root
/** 最近一次渲染读到的 hook 结果 */
let last: ReturnType<typeof useVariantPng>

function Probe({
  fileId,
  overrides,
  bucket = 200,
  rev = 0,
  chart,
  chartSource,
  origin,
}: {
  fileId: string
  overrides: unknown[]
  bucket?: number
  rev?: number
  chart?: PanelChartState
  chartSource?: ChartSourceRef
  origin?: FigureOrigin
}) {
  last = useVariantPng(fileId, overrides, bucket, true, rev, undefined,
    chart, chartSource, undefined, undefined, origin)
  return null
}

beforeEach(() => {
  snapshotPng.mockReset()
  snapshotPng.mockResolvedValue({ url: 'data:image/png;base64,eA==', manifest: { stem: 'chart', size_mm: [80,60], elements: [] }, chartRevision: 'paired' })
  previewPng.mockReset()
  previewPng.mockResolvedValue(new Blob(['png']))
  clearVariantPngCache()
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
  setEngineTransport(null)
  act(() => root.unmount())
  container.remove()
})

/** 挂一次 Probe 并等取数落定 */
async function fetchOnce(
  fileId: string,
  overrides: unknown[],
  bucket = 200,
  rev = 0,
): Promise<string | null> {
  await act(async () => {
    root.render(<Probe fileId={fileId} overrides={overrides} bucket={bucket} rev={rev} />)
  })
  // 取数是一个 microtask 链（fetch → blob → setState），冲干净再断言；
  // 不是「等 N 毫秒应该就好了」——这里等的是队列排空，不是墙钟
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
  return last.url
}

describe('chart版本缩略图复用同一PNG helper', () => {
  const chartState = (): PanelChartState => {
    const v = chartVectors.vectors.find(v => v.expect === 'ok')!
    return structuredClone({ model: v.model, binding: v.binding }) as unknown as PanelChartState
  }
  const show = async (chart: PanelChartState) => {
    await act(async () => { root.render(<Probe fileId="chart.svg" overrides={[]} chart={chart} />) })
  }

  it.each([{ name: 'empty', overrides: [] }, { name: 'edits', overrides: OV }])('附件缺失科学模型不能作为普通文件请求或缓存 $name', async ({ overrides }) => {
    const source: ChartSourceRef = { schema: 1, sourceId: 'chart.svg', bytesSha256: 'a'.repeat(64),
      sizeBytes: 100, sceneDigest: chartState().model.scene_digest }
    await act(async () => { root.render(<Probe fileId="chart.svg" overrides={overrides} chartSource={source} />) })
    expect(last).toMatchObject({ url: null, loading: false, approximate: true })
    expect(previewPng).not.toHaveBeenCalled()
    expect(snapshotPng).not.toHaveBeenCalled()
  })

  it('同chart不同附件必须重新取图且完整ref进入PNG，不命中旧来源缓存', async () => {
    const chart = chartState()
    const source: ChartSourceRef = { schema: 1, sourceId: 'chart.svg', bytesSha256: 'a'.repeat(64),
      sizeBytes: 100, sceneDigest: chart.model.scene_digest }
    await act(async () => { root.render(<Probe fileId="chart.svg" overrides={[]} chart={chart} chartSource={source} />) })
    expect(snapshotPng.mock.calls[0][4]).toEqual({ chart, chart_source: source })
    expect(snapshotPng.mock.calls[0][4].chart_source).not.toBe(source)
    const other = { ...source, bytesSha256: 'b'.repeat(64) }
    await act(async () => { root.render(<Probe fileId="chart.svg" overrides={[]} chart={chart} chartSource={other} />) })
    expect(snapshotPng).toHaveBeenCalledTimes(2)
    expect(snapshotPng.mock.calls[1][4].chart_source).toEqual(other)
  })

  it('旧chart宿主无来源能力不能接PNG，有能力时收到第六参完整ref', async () => {
    const chart = chartState()
    const source: ChartSourceRef = { schema: 1, sourceId: 'chart.svg', bytesSha256: 'a'.repeat(64),
      sizeBytes: 100, sceneDigest: chart.model.scene_digest }
    const previewPngSnapshot = vi.fn(async () => ({ url: 'data:image/png;base64,eA==',
      manifest: { stem: 'chart', size_mm: [80, 60] as [number, number], elements: [] }, chartRevision: 'host' }))
    const host = { supportsChartRender: true as const, render: vi.fn(), previewPngSnapshot,
      previewPngUrl: vi.fn(), panelSrc: () => null }
    setEngineTransport(host)
    await act(async () => { root.render(<Probe fileId="chart.svg" overrides={[]} chart={chart} chartSource={source} />) })
    expect(previewPngSnapshot).not.toHaveBeenCalled()
    expect(last.approximate).toBe(true)
    setEngineTransport({ ...host, supportsChartSourceRender: true })
    const other = { ...source, bytesSha256: 'b'.repeat(64) }
    await act(async () => { root.render(<Probe fileId="chart.svg" overrides={[]} chart={chart} chartSource={other} />) })
    expect(previewPngSnapshot).toHaveBeenCalledWith('chart.svg', [], 200, expect.any(AbortSignal), chart, other)
  })

  it('无patch的chart仍出图，cache包含全部模型/绑定且重复同身份只取一次', async () => {
    const a = chartState()
    expect(variantNeedsRender([], undefined, a)).toBe(true)
    await show(a)
    expect(snapshotPng).toHaveBeenCalledOnce()
    expect(snapshotPng.mock.calls[0][4]).toEqual({ chart: a })
    await show(structuredClone(a))
    expect(snapshotPng).toHaveBeenCalledOnce()
    const b = structuredClone(a)
    b.model.axes[0].xlim[1] += 10
    await show(b)
    expect(snapshotPng).toHaveBeenCalledTimes(2)
    expect(snapshotPng.mock.calls[1][4]).toEqual({ chart: b })
    expect(previewPng).not.toHaveBeenCalled()
  })

  it('换chart的首帧不冒充上一份缩略图，迟到旧项目响应不进新项目/cache', async () => {
    let finishOld!: (value: unknown) => void
    snapshotPng.mockReturnValueOnce(new Promise(resolve => { finishOld = resolve }))
    const a = chartState()
    await show(a)
    setCurrentProjectId('projB')
    await show(a)
    const shownB = last.url
    await act(async () => { finishOld({ url: 'blob:old-A', manifest: {}, chartRevision: 'A' }) })
    expect(last.url).toBe(shownB)
    expect(revoked).toContain('blob:old-A')
    setCurrentProjectId('projA')
    await show(a)
    expect(snapshotPng).toHaveBeenCalledTimes(3)
  })

  it('未实现chart的宿主不退回patch-only PNG伪装版本图', async () => {
    const oldPng = vi.fn(async () => 'blob:old-native')
    setEngineTransport({ render: vi.fn(), previewPngUrl: oldPng, panelSrc: () => null })
    await show(chartState())
    expect(oldPng).not.toHaveBeenCalled()
    expect(last).toMatchObject({ url: null, approximate: true })
  })

  it('A到B再回A即使组件尚未换props，旧PNG也不写状态或缓存', async () => {
    let finishOld!: (value: unknown) => void
    snapshotPng.mockReturnValueOnce(new Promise(resolve => { finishOld = resolve }))
    const a = chartState()
    await show(a)
    setCurrentProjectId('projB')
    setCurrentProjectId('projA')
    await act(async () => { finishOld({ url: 'blob:retired-generation', manifest: {}, chartRevision: 'old' }) })
    expect(last.url).toBeNull()
    expect(revoked).toContain('blob:retired-generation')
  })
})

describe('缓存键：项目 + 素材版本 + 变体，少一个都会串图', () => {
  it('同一项目同一变体命中缓存，只发一次请求', async () => {
    const a = await fetchOnce('fig1.pdf', OV)
    const b = await fetchOnce('fig1.pdf', OV)
    expect(a).toBe(b)
    expect(a).not.toBeNull()
    expect(previewPng).toHaveBeenCalledTimes(1)
  })

  it('换项目之后同名同 overrides 不得命中旧项目的图', async () => {
    const a = await fetchOnce('fig1.pdf', OV)
    setCurrentProjectId('projB')
    const b = await fetchOnce('fig1.pdf', OV)
    expect(b).not.toBe(a)
    expect(previewPng).toHaveBeenCalledTimes(2)
  })

  it('磁盘素材被改过（rev 变了）也不得命中旧缩略图', async () => {
    const a = await fetchOnce('fig1.pdf', OV, 200, 1000)
    const b = await fetchOnce('fig1.pdf', OV, 200, 2000)
    expect(b).not.toBe(a)
    expect(previewPng).toHaveBeenCalledTimes(2)
  })

  it('没有 overrides 的面板压根不发请求（与磁盘图一模一样）', async () => {
    expect(await fetchOnce('fig1.pdf', [])).toBeNull()
    expect(previewPng).not.toHaveBeenCalled()
  })
})

describe('回收：blob 不许泄漏', () => {
  it('超出上限的老条目当场 revoke', async () => {
    for (let i = 0; i < 30; i++) {
      await fetchOnce('fig1.pdf', [{ gid: 't', prop: 'p', value: i }])
    }
    // 上限 24：最早那几个必须已经还回去了
    expect(revoked.length).toBeGreaterThanOrEqual(6)
    expect(revoked).toContain('blob:url-1')
  })

  it('clearVariantPngCache 整表释放', async () => {
    await fetchOnce('fig1.pdf', OV)
    await fetchOnce('fig2.pdf', OV)
    revoked = []
    clearVariantPngCache()
    expect(revoked).toHaveLength(2)
  })
})

describe('失败：退回磁盘图，但必须标成近似', () => {
  it('请求失败 → url 为空、approximate 置位', async () => {
    previewPng.mockRejectedValue(new Error('engine down'))
    await fetchOnce('fig1.pdf', OV)
    expect(last.url).toBeNull()
    expect(last.approximate).toBe(true)
  })
})

describe('variantNeedsRender：哪些版本永远不出引擎图（快照据此判断「还在出」）', () => {
  const frame = { gid: 'figure', prop: 'frame', value: [0, 0, 1, 1] }
  const edit = { gid: 'ax0', prop: 'title', value: 'x' }
  const guard = { policy: 'script_backed' } as never
  it('没有 override 不出', () => expect(variantNeedsRender([], guard)).toBe(false))
  it('来源驱动面板只有结构性 FRAME 不出（磁盘原像素即是）', () => expect(variantNeedsRender([frame], guard)).toBe(false))
  it('来源驱动面板有真实编辑要出', () => expect(variantNeedsRender([frame, edit], guard)).toBe(true))
  it('普通面板只要有 override 就出', () => expect(variantNeedsRender([frame], undefined)).toBe(true))
})

describe('历史输入的 PNG 归属', () => {
  const origin = (id: string): FigureOrigin => ({ version: 1, snapshot: {
    version: 1, sourceId: 'History.pdf', patchHash: 'P', receiptId: id, snapshotId: id,
  }, patches: [{ gid: 'line', prop: 'linewidth', value: 2 }] })
  const show = async (input: FigureOrigin) => {
    await act(async () => { root.render(<Probe fileId="History.pdf" overrides={[]} origin={input} />) })
  }

  it('同Q不同历史不共享像素，迟到A不覆盖B或进入缓存', async () => {
    let finish!: (value: unknown) => void
    snapshotPng.mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
    const inputA = origin('A')
    await show(inputA)
    inputA.snapshot.receiptId = 'changed-after-send'
    snapshotPng.mockResolvedValueOnce({ url: 'blob:B', manifest: {}, figureOrigin: origin('B') })
    await show(origin('B'))
    expect(last.url).toBe('blob:B')
    await act(async () => { finish({ url: 'blob:late-A', manifest: {}, figureOrigin: origin('A') }) })
    expect(last.url).toBe('blob:B')
    expect(revoked).toContain('blob:late-A')
    snapshotPng.mockResolvedValueOnce({ url: 'blob:new-A', manifest: {}, figureOrigin: origin('A') })
    await show(origin('A'))
    expect(last.url).toBe('blob:new-A')
    expect(snapshotPng).toHaveBeenCalledTimes(3)
    expect(previewPng).not.toHaveBeenCalled()
    expect(snapshotPng.mock.calls[0][4]).toEqual({ figure_origin: origin('A') })
  })

  it('显式混入chart不落普通路径', async () => {
    const chart = structuredClone(chartVectors.vectors.find(v => v.expect === 'ok')!)
    await act(async () => { root.render(<Probe fileId="History.pdf" overrides={[]} origin={origin('A')} chart={{ model: chart.model, binding: chart.binding } as unknown as PanelChartState} />) })
    expect(last.url).toBeNull()
    expect(last.approximate).toBe(true)
    expect(snapshotPng).not.toHaveBeenCalled()
    expect(previewPng).not.toHaveBeenCalled()
  })
})
