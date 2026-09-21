import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { RENDER_RETRY_DELAYS_MS, isRenderUrl, retrySrc, useRetryingSrc } from './imgRetry'

function Probe({ src }: { src: string }) {
  const r = useRetryingSrc(src)
  return <img data-testid="img" alt="" src={r.src} onError={r.onError} />
}

let container: HTMLDivElement
let root: Root

beforeEach(() => {
  vi.useFakeTimers()
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})
afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.useRealTimers()
})

const img = () => container.querySelector('img') as HTMLImageElement
const show = (src: string) => act(() => root.render(<Probe src={src} />))
const fail = () => act(() => img().dispatchEvent(new Event('error')))
const wait = (ms: number) => act(() => vi.advanceTimersByTime(ms))

describe('useRetryingSrc', () => {
  it('/api/render 失败后按退避表换地址重试，次数有界', () => {
    const base = '/api/render?id=a.pdf&w=400&m=1'
    show(base)
    expect(img().getAttribute('src')).toBe(base)
    for (let i = 0; i < RENDER_RETRY_DELAYS_MS.length; i++) {
      fail()
      // 定时器到期前地址不变（不是立刻重试——那样只会立刻再撞一次队列）
      expect(img().getAttribute('src')).toBe(retrySrc(base, i))
      wait(RENDER_RETRY_DELAYS_MS[i])
      expect(img().getAttribute('src')).toBe(`${base}&r=${i + 1}`)
    }
    // 次数用完：再失败也不换地址
    fail()
    wait(60_000)
    expect(img().getAttribute('src')).toBe(`${base}&r=${RENDER_RETRY_DELAYS_MS.length}`)
  })

  it('不是 /api/render 的地址（blob / data / api/file）失败不重试', () => {
    for (const src of ['blob:http://x/1', 'data:image/png;base64,AAAA', '/api/file?id=a.png&m=1']) {
      show(src)
      fail()
      wait(60_000)
      expect(img().getAttribute('src')).toBe(src)
      expect(isRenderUrl(src)).toBe(false)
    }
  })

  it('src 换了：计数归零、上一张挂着的定时器不再改地址', () => {
    show('/api/render?id=a.pdf&w=400')
    fail()
    show('/api/render?id=b.pdf&w=400')
    wait(60_000)
    expect(img().getAttribute('src')).toBe('/api/render?id=b.pdf&w=400')
  })
})
