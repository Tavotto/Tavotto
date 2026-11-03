import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

const desk = vi.hoisted(() => ({
  get: vi.fn(async (): Promise<number | null> => null),
  listen: vi.fn(async (_cb: (v: number | null) => void) => () => {}),
}))
vi.mock('./desktop', () => ({ getScreenPpi: desk.get, onScreenPpiChange: desk.listen }))

import { __resetScreenPpiForTests, dotForPpi, useScreenPpi } from './screenPpi'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

afterEach(() => {
  __resetScreenPpiForTests()
  desk.get.mockReset()
  desk.listen.mockReset()
})

describe('dotForPpi：按屏幕物理 ppi 选点径（目标约 0.2 mm，ADR 0168 第二节）', () => {
  it.each([
    [254, 2, '14 寸 MacBook Pro Retina'],
    [224, 2, '13 寸 MacBook Air'],
    [141, 1, '15.6 寸 1080p 笔记本（常开 125%）'],
    [122, 1, '24 寸 2K（常开 125%）'],
    [163, 1, '27 寸 4K（常开 150% / 175%：按倍率是 2，按 ppi 是 1）'],
    [92, 1, '24 寸 1080p（100%）'],
    [440, 3, '手机级高密度'],
  ])('%s ppi → %s 个物理像素（%s）', (ppi, dot) => {
    expect(dotForPpi(ppi)).toBe(dot)
  })

  it('读不到 / 不合理的数 → undefined（退回按缩放倍数取整）；上限 4', () => {
    expect(dotForPpi(null)).toBeUndefined()
    expect(dotForPpi(Number.NaN)).toBeUndefined()
    expect(dotForPpi(0)).toBeUndefined()
    expect(dotForPpi(1000)).toBe(4)
  })
})

describe('useScreenPpi：模块级一份，先听再读', () => {
  const mount = () => {
    const seen: (number | null)[] = []
    const Probe = () => {
      seen.push(useScreenPpi())
      return null
    }
    const host = document.createElement('div')
    const root = createRoot(host)
    act(() => root.render(<Probe />))
    return { seen, unmount: () => act(() => root.unmount()) }
  }

  it('回读到 ppi 就用它；之后换屏事件覆盖', async () => {
    let push: ((v: number | null) => void) | undefined
    desk.listen.mockImplementation(async (cb) => {
      push = cb
      return () => {}
    })
    desk.get.mockResolvedValue(141)
    const { seen, unmount } = mount()
    await act(async () => {})
    expect(seen.at(-1)).toBe(141)
    act(() => push!(254))
    expect(seen.at(-1)).toBe(254)
    unmount()
  })

  it('事件先于回读到达：回读的旧值不覆盖事件值', async () => {
    let resolveGet: (v: number | null) => void = () => {}
    desk.get.mockImplementation(() => new Promise((r) => (resolveGet = r)))
    desk.listen.mockImplementation(async (cb) => {
      cb(163)
      return () => {}
    })
    const { seen, unmount } = mount()
    await act(async () => {})
    expect(seen.at(-1)).toBe(163)
    await act(async () => resolveGet(141))
    expect(seen.at(-1)).toBe(163)
    unmount()
  })

  it('多个订阅者只向壳回读一次', async () => {
    desk.get.mockResolvedValue(null)
    desk.listen.mockResolvedValue(() => {})
    const a = mount()
    const b = mount()
    await act(async () => {})
    expect(desk.get).toHaveBeenCalledTimes(1)
    expect(desk.listen).toHaveBeenCalledTimes(1)
    a.unmount()
    b.unmount()
  })
})
