/**
 * 定位舞台灯（`IssueSpotlight`）：跟着定位高亮框亮起，放完一段自己撤；没有高亮框就不亮；
 * 景深层与暗幕的受光遮罩都以高亮框中心为光心，光柱从最近的画布角射来。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useUiStore } from '@/store/uiStore'
import { IssueSpotlight, SPOTLIGHT_MS } from './IssueSpotlight'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let root: Root
let host: HTMLDivElement
let now = 0
let frames: FrameRequestCallback[] = []

const rect = (x: number, y: number, w: number, h: number) =>
  ({ left: x, top: y, width: w, height: h, right: x + w, bottom: y + h, x, y, toJSON: () => ({}) }) as DOMRect

async function frame(advance = 16) {
  now += advance
  await act(async () => {
    frames.splice(0).forEach((cb) => cb(now))
  })
}

beforeEach(async () => {
  now = 0
  frames = []
  vi.spyOn(performance, 'now').mockImplementation(() => now)
  vi.spyOn(globalThis, 'requestAnimationFrame').mockImplementation(((cb: FrameRequestCallback) => {
    frames.push(cb)
    return frames.length
  }) as never)
  vi.spyOn(globalThis, 'cancelAnimationFrame').mockImplementation(() => {})
  useUiStore.setState({ issueHighlight: null })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<IssueSpotlight />))
  vi.spyOn(host.firstElementChild as HTMLElement, 'getBoundingClientRect').mockReturnValue(rect(0, 0, 800, 600))
})

afterEach(async () => {
  await act(async () => root.unmount())
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

const spot = () => host.querySelector('[data-issue-spotlight]')

describe('定位舞台灯', () => {
  it('跟着高亮框亮起、挖出光圈，放完自己撤', async () => {
    const target = document.createElement('div')
    target.setAttribute('data-issue-highlight', 'p1')
    vi.spyOn(target, 'getBoundingClientRect').mockReturnValue(rect(100, 120, 40, 20))
    document.body.appendChild(target)
    await act(async () => useUiStore.getState().setIssueHighlight({ objectId: 'p1', gid: 'axes_0.legend.title' }))
    await frame()
    expect(spot()).not.toBeNull()
    // 景深层以高亮框中心为光心，半径 = 半对角线 + 36
    const blur = host.querySelector<HTMLElement>('[data-spotlight-blur]')!
    const side = parseFloat(blur.style.width)
    expect(parseFloat(blur.style.left) + side / 2).toBe(120)
    expect(parseFloat(blur.style.top) + side / 2).toBe(130)
    expect(blur.style.getPropertyValue('--spot-r')).toBe('58px')
    // 暗幕只有一层；光圈与光柱是同一张遮罩里的受光区，光心对准高亮框中心
    expect(host.querySelectorAll('[data-spotlight-beam]')).toHaveLength(1)
    const circle = host.querySelector('[data-spotlight-beam] mask circle')!
    expect([circle.getAttribute('cx'), circle.getAttribute('cy')]).toEqual(['120', '130'])
    // 光柱从离光心最近的画布角（左上，往外推 40px）射来
    const shaft = host.querySelector<SVGElement>('[data-spotlight-beam] mask [data-spotlight-shaft]')!
    const [sx, sy] = shaft.style.transformOrigin.split(' ').map(parseFloat)
    expect(sx).toBeLessThan(0)
    expect(sy).toBeLessThan(0)
    await frame(SPOTLIGHT_MS)
    expect(spot()).toBeNull()
  })

  it('画布上没有高亮框（解不出目标）就不亮', async () => {
    await act(async () => useUiStore.getState().setIssueHighlight({ objectId: 'p1', gid: null }))
    await frame()
    expect(spot()).toBeNull()
  })
})
