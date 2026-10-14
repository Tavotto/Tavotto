/**
 * #570：量的是生产 store 发给每个真实 React 订阅者的通知次数，而非 React
 * 最终 render 次数（React 自己的批处理可能掩盖逐箭头同步通知）。rAF 只控时钟，
 * subscribe / getSnapshot / DOM 写入仍走生产实现；这里不声称真实设备帧率。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MATPLOTLIB_SVG } from '@/lib/__fixtures__/matplotlibSvg'
import {
  beginPreview,
  cancelPreview,
  commitPreview,
  discardPanelPreview,
  previewLine,
  previewLinesOf,
  previewTransform,
  reattachPreview,
  resetPreview,
  usePreviewLinePanels,
  usePreviewLines,
} from './svgPreviewStore'

const observed = vi.hoisted(() => ({ notifications: 0, transforms: [] as (string | null)[] }))

vi.mock('react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react')>()
  return {
    ...actual,
    useSyncExternalStore: (
      subscribe: (fn: () => void) => () => void,
      getSnapshot: () => unknown,
      getServerSnapshot?: () => unknown,
    ) => actual.useSyncExternalStore((listener) => subscribe(() => {
      observed.notifications++
      observed.transforms.push(document.querySelector('[id="axes_0.lines_0"]')?.getAttribute('transform') ?? null)
      listener()
    }), getSnapshot, getServerSnapshot),
  }
})

const PANEL = 'p1'
const KEY = 'Flow.pdf []'
const FINAL_KEY = 'Flow.pdf [moved]'
let root: Root
let host: HTMLDivElement
let frames: Map<number, FrameRequestCallback>
let frameSeq: number

function LinesObserver({ panelId = PANEL }: { panelId?: string }) {
  const lines = usePreviewLines(panelId)
  const transform = document.querySelector(`[data-element-svg="${panelId}"] [id="axes_0.lines_0"]`)?.getAttribute('transform') ?? ''
  return <output data-preview-transform={transform}>{JSON.stringify([...lines])}</output>
}

function PanelsObserver() {
  return <output>{JSON.stringify(usePreviewLinePanels())}</output>
}

const begin = () => beginPreview({ panelId: PANEL, renderKey: KEY })
const ends = (move: number) => ({ a: [move / 100, 0.3] as [number, number], b: [0.9, 0.3] as [number, number] })
const rendered = () => JSON.parse(host.textContent!) as unknown[]

function finishPreview(finish: 'cancel' | 'discard' | 'authority' | 'reset') {
  if (finish === 'cancel') cancelPreview()
  else if (finish === 'discard') discardPanelPreview(PANEL)
  else if (finish === 'reset') resetPreview()
  else {
    commitPreview([], FINAL_KEY)
    reattachPreview(PANEL, FINAL_KEY)
  }
}

async function nextFrame() {
  const ready = [...frames.values()]
  frames.clear()
  await act(async () => { for (const callback of ready) callback(16) })
}

beforeEach(async () => {
  resetPreview()
  observed.notifications = 0
  observed.transforms = []
  frames = new Map()
  frameSeq = 0
  vi.stubGlobal('requestAnimationFrame', vi.fn((callback: FrameRequestCallback) => {
    frames.set(++frameSeq, callback)
    return frameSeq
  }))
  vi.stubGlobal('cancelAnimationFrame', vi.fn((id: number) => { frames.delete(id) }))
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  document.body.innerHTML = `<div data-element-svg="${PANEL}">${MATPLOTLIB_SVG}</div>`
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<LinesObserver />))
})

afterEach(async () => {
  await act(async () => root.unmount())
  resetPreview()
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

describe('预览线通知与 DOM 写入共用 rAF', () => {
  it.each([1, 32, 128])('%i 根单端箭头，同帧 20 次移动只通知一次且保留最后端点', async (count) => {
    begin()
    for (let move = 1; move <= 20; move++) {
      previewTransform('axes_0.lines_0', move / 100, 0)
      for (let line = 0; line < count; line++) previewLine(`arrow_${line}`, ends(move))
    }
    expect(observed.notifications).toBe(0)
    expect(requestAnimationFrame).toHaveBeenCalledTimes(1)
    expect(rendered()).toEqual([])
    await nextFrame()
    expect(observed.notifications).toBe(1)
    expect(observed.transforms).toEqual([`translate(${0.2 * 288},0)`])
    expect(rendered()).toHaveLength(count)
    for (let line = 0; line < count; line++) expect(previewLinesOf(PANEL).get(`arrow_${line}`)).toEqual(ends(20))
    // 下一帧仍能正常发布；不是「永久只通知一次」。
    previewLine('arrow_0', ends(21))
    await nextFrame()
    expect(observed.notifications).toBe(2)
    expect(previewLinesOf(PANEL).get('arrow_0')).toEqual(ends(21))
  })

  it('只有线、没有 DOM 操作时也调度；同帧增删只发布最终集合', async () => {
    begin()
    previewLine('kept', ends(1))
    previewLine('removed', ends(1))
    previewLine('removed', null)
    previewLine('kept', ends(2))
    await nextFrame()
    expect(observed.notifications).toBe(1)
    expect(rendered()).toEqual([['kept', ends(2)]])
    previewLine('kept', null)
    await nextFrame()
    expect(observed.notifications).toBe(2)
    expect(rendered()).toEqual([])
    previewLine('missing', null)
    expect(frames.size).toBe(0)
  })

  it('面板集合订阅同样每帧更新，松手后的虚线保留到权威换图', async () => {
    await act(async () => root.render(<PanelsObserver />))
    begin()
    previewLine('arrow', ends(2))
    commitPreview([], FINAL_KEY)
    expect(rendered()).toEqual([])
    await nextFrame()
    expect(observed.notifications).toBe(1)
    expect(rendered()).toEqual([PANEL])
    expect(previewLinesOf(PANEL).get('arrow')).toEqual(ends(2))
    await act(async () => reattachPreview(PANEL, FINAL_KEY))
    expect(observed.notifications).toBe(2)
    expect(rendered()).toEqual([])
  })

  it.each(['cancel', 'discard', 'authority', 'reset'] as const)('%s 在 rAF 前清除，排队帧不会复活或重复发布虚线', async (finish) => {
    begin()
    previewLine('arrow', ends(3))
    expect(frames.size).toBe(1)
    await act(async () => finishPreview(finish))
    expect(observed.notifications).toBe(1)
    expect(rendered()).toEqual([])
    await nextFrame()
    expect(observed.notifications).toBe(1)
    expect(previewLinesOf(PANEL).size).toBe(0)
  })

  it.each(['cancel', 'discard', 'authority', 'reset'] as const)('%s 在最后一根线排队删除后立即撤下已显示的覆盖层', async (finish) => {
    begin()
    previewLine('arrow', ends(3))
    await nextFrame()
    expect(rendered()).toEqual([['arrow', ends(3)]])
    previewLine('arrow', null)
    // 地图已经空了，但订阅者上一帧显示的线仍需在收尾时同步撤下。
    expect(previewLinesOf(PANEL).size).toBe(0)
    await act(async () => finishPreview(finish))
    expect(observed.notifications).toBe(2)
    expect(rendered()).toEqual([])
    await nextFrame()
    expect(observed.notifications).toBe(2)
  })

  it.each(['authority', 'discard'] as const)('A 面板 %s 收尾不能消费 B 面板等待 DOM 位移的线通知', async (finish) => {
    begin()
    previewLine('arrow_A', ends(3))
    await nextFrame()
    commitPreview([], FINAL_KEY)

    const other = 'p2'
    document.body.insertAdjacentHTML('beforeend', `<div data-element-svg="${other}">${MATPLOTLIB_SVG}</div>`)
    await act(async () => root.render(<LinesObserver panelId={other} />))
    beginPreview({ panelId: other, renderKey: KEY })
    observed.notifications = 0
    previewTransform('axes_0.lines_0', 0.4, 0)
    previewLine('arrow_B', ends(40))
    expect(frames.size).toBe(1)
    await act(async () => {
      if (finish === 'authority') reattachPreview(PANEL, FINAL_KEY)
      else discardPanelPreview(PANEL)
    })
    expect(previewLinesOf(PANEL).size).toBe(0)
    expect(observed.notifications).toBe(1) // A 覆盖层即时撤下
    expect(host.querySelector('output')?.getAttribute('data-preview-transform')).toBe('')
    await nextFrame()
    // B 的帧仍需通知：否则 DOM 已移动，覆盖层还停在 A 收尾时读到的状态。
    expect(observed.notifications).toBe(2)
    expect(rendered()).toEqual([['arrow_B', ends(40)]])
    expect(host.querySelector('output')?.getAttribute('data-preview-transform')).toBe(`translate(${0.4 * 288},0)`)
  })

  it('收掉本身无线的 A 账本不提前发布或吞掉 B 的待发布通知', async () => {
    begin()
    commitPreview([], FINAL_KEY)
    const other = 'p2'
    await act(async () => root.render(<LinesObserver panelId={other} />))
    beginPreview({ panelId: other, renderKey: KEY })
    previewLine('arrow_B', ends(4))
    await act(async () => discardPanelPreview(PANEL))
    expect(observed.notifications).toBe(0)
    expect(rendered()).toEqual([])
    await nextFrame()
    expect(observed.notifications).toBe(1)
    expect(rendered()).toEqual([['arrow_B', ends(4)]])
  })
})
