/**
 * 视口缓动。
 *
 * 这里守的两条纪律，都是「加了动画反而更差」的典型死法：
 *
 * 1. **任何直接操纵都要掐断在飞的补间**。不掐的话补间与用户抢着写 zoom/pan，
 *    滚轮滚一下画面会在两个目标之间抖。
 * 2. **连按以补间的终点为基准**，不是以当前这一帧的中间值。否则连按三下 ⌘+
 *    只放大到「一下半」，用户以为按键丢了。
 *
 * 另外：滚轮 / 捏合（zoomAt）永远瞬时——跟手的东西加缓动就是变粘。
 */
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'

import { MAX_ZOOM, useViewportStore } from './viewportStore'
import { BASE_PX_PER_MM } from '@/lib/units'

const VIEW = { left: 0, top: 0, width: 800, height: 600 }

/**
 * 等补间真的停下来。**不能用固定延时**：jsdom 的 rAF 是 setTimeout 模拟的，
 * 一个文件里几十个定时器排队时它比真浏览器慢得多，写死 320ms 会量到一个
 * 「跑了 90%」的中间值——这种用例平时绿、CI 慢一点就红。
 */
async function settle(): Promise<void> {
  let last = ''
  for (let i = 0; i < 200; i++) {
    await new Promise((r) => setTimeout(r, 20))
    const { zoom, panX, panY } = useViewportStore.getState()
    const now = `${zoom}|${panX}|${panY}`
    if (now === last) return
    last = now
  }
  throw new Error('补间在 4s 内没有停下来')
}

function setReducedMotion(reduce: boolean) {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: reduce && q.includes('prefers-reduced-motion'),
    media: q,
    addEventListener() {},
    removeEventListener() {},
  }))
}

beforeEach(() => {
  setReducedMotion(false)
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0 })
  useViewportStore.getState().setViewRect(VIEW)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

const zoom = () => useViewportStore.getState().zoom

describe('setZoomCentered', () => {
  it('补间到目标，且以视口中心为锚点', async () => {
    useViewportStore.getState().setZoomCentered(2)
    await settle()
    expect(zoom()).toBeCloseTo(2, 6)
    // 锚在中心：中心那个点在世界坐标里没动
    const s = useViewportStore.getState()
    expect(s.panX).toBeCloseTo(400 - 400 * 2, 6)
    expect(s.panY).toBeCloseTo(300 - 300 * 2, 6)
  })

  it('reduced-motion：同步落终态，不等帧', () => {
    setReducedMotion(true)
    useViewportStore.getState().setZoomCentered(3)
    expect(zoom()).toBe(3)
  })
})

describe('zoomBy（顶栏 ± 与 ⌘±）', () => {
  it('连按以补间终点为基准累加，不会「吃掉」按键', async () => {
    const vp = useViewportStore.getState()
    vp.zoomBy(1.25)
    // 第一段还在飞的时候就按第二、第三下
    vp.zoomBy(1.25)
    vp.zoomBy(1.25)
    await settle()
    // 调用方写 setZoomCentered(zoom * 1.25) 的老写法在这里会停在 1.25
    expect(zoom()).toBeCloseTo(1.25 ** 3, 6)
  })

  it('缩小同样累加', async () => {
    const vp = useViewportStore.getState()
    vp.zoomBy(1 / 1.25)
    vp.zoomBy(1 / 1.25)
    await settle()
    expect(zoom()).toBeCloseTo(1 / 1.25 ** 2, 6)
  })

  it('连按到顶仍钳在 MAX_ZOOM', async () => {
    const vp = useViewportStore.getState()
    for (let i = 0; i < 30; i++) vp.zoomBy(1.25)
    await settle()
    expect(zoom()).toBe(MAX_ZOOM)
  })

  it('钳到 MAX_ZOOM', async () => {
    useViewportStore.getState().setZoomCentered(999)
    await settle()
    expect(zoom()).toBe(MAX_ZOOM)
  })

})

describe('直接操纵掐断补间', () => {
  it('滚轮缩放（zoomAt）掐断，且自己永远瞬时', async () => {
    useViewportStore.getState().setZoomCentered(4)
    useViewportStore.getState().zoomAt(2, 0, 0) // 滚轮插进来
    const afterWheel = zoom()
    expect(afterWheel).toBeCloseTo(2, 6) // 瞬时生效，没有缓动
    await settle()
    expect(zoom(), '补间被掐断后不该再把 zoom 拖向 4').toBeCloseTo(afterWheel, 6)
  })

  it('平移掐断', async () => {
    useViewportStore.getState().setZoomCentered(4)
    useViewportStore.getState().panBy(10, 10)
    const held = { ...useViewportStore.getState() }
    await settle()
    expect(zoom()).toBeCloseTo(held.zoom, 6)
    expect(useViewportStore.getState().panX).toBeCloseTo(held.panX, 6)
  })

  it('瞬时 fit 掐断（切画布/载入文档走它，必须一步到位）', async () => {
    useViewportStore.getState().setZoomCentered(8)
    useViewportStore.getState().fit(100, 100)
    const settled = zoom()
    await settle()
    expect(zoom()).toBeCloseTo(settled, 6)
  })
})

describe('revealRect', () => {
  it('把区域挪到视口中央（装得下就不改缩放）', async () => {
    useViewportStore.getState().revealRect({ x: 100, y: 50, w: 10, h: 10 })
    await settle()
    const s = useViewportStore.getState()
    // 区域中心应当落在视口中心
    expect(s.panX + 105 * BASE_PX_PER_MM * s.zoom).toBeCloseTo(400, 3)
    expect(s.panY + 55 * BASE_PX_PER_MM * s.zoom).toBeCloseTo(300, 3)
  })
})
