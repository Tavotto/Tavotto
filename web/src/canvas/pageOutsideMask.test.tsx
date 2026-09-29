/**
 * 页面外遮罩（2026-09-28）：轮廓正好落在页面的屏幕矩形上；四条色带铺满页面以外、
 * 与页面零重叠、彼此不重叠。jsdom 量不到绘制，这里量的是写进 style 的几何。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { useDocumentStore } from '@/store/documentStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { PageOutsideMask } from './PageOutsideMask'

const VW = 800
const VH = 600
let host: HTMLDivElement
let root: Root

beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(() => {
  act(() => root.unmount())
  host.remove()
})

type R = { l: number; t: number; w: number; h: number }
const px = (v: string) => Number.parseFloat(v)

/** 色带的 style 换成视口内的矩形（right/bottom 按视口尺寸换算） */
function rectOf(el: HTMLElement): R {
  const s = el.style
  const l = px(s.left)
  const t = px(s.top)
  const w = s.width ? px(s.width) : VW - px(s.right) - l
  const h = s.height ? px(s.height) : VH - px(s.bottom) - t
  return { l, t, w, h }
}

const area = (r: R) => Math.max(r.w, 0) * Math.max(r.h, 0)
const clip = (r: R): R => {
  const l = Math.max(r.l, 0)
  const t = Math.max(r.t, 0)
  return { l, t, w: Math.min(r.l + r.w, VW) - l, h: Math.min(r.t + r.h, VH) - t }
}
const overlap = (a: R, b: R) =>
  Math.max(0, Math.min(a.l + a.w, b.l + b.w) - Math.max(a.l, b.l)) *
  Math.max(0, Math.min(a.t + a.h, b.t + b.h) - Math.max(a.t, b.t))

function mount(view: { zoom: number; panX: number; panY: number }, page = { w: 85, h: 60 }) {
  useDocumentStore.setState((s) => ({ doc: { ...s.doc, page: { ...s.doc.page, ...page } } }))
  useViewportStore.setState(view)
  act(() => root.render(<PageOutsideMask />))
  const mask = host.querySelector<HTMLElement>('[data-page-outside-mask]')!
  const outline = mask.querySelector<HTMLElement>('[data-page-outline]')!
  const bands = [...mask.children].filter((c) => c !== outline) as HTMLElement[]
  return { outline: rectOf(outline), bands: bands.map(rectOf) }
}

describe('PageOutsideMask', () => {
  const cases = [
    { name: '页面在视口中间', view: { zoom: 2, panX: 120, panY: 80 } },
    { name: '页面伸出视口左上', view: { zoom: 3, panX: -200, panY: -150 } },
    { name: '页面整张在视口外右下', view: { zoom: 1, panX: 900, panY: 700 } },
  ]
  for (const c of cases) {
    it(`${c.name}：轮廓 = 页面，色带铺满其余部分且不重叠`, () => {
      const { outline, bands } = mount(c.view)
      const page = {
        l: c.view.panX,
        t: c.view.panY,
        w: mmToWorld(85) * c.view.zoom,
        h: mmToWorld(60) * c.view.zoom,
      }
      expect(outline.l).toBeCloseTo(page.l)
      expect(outline.t).toBeCloseTo(page.t)
      expect(outline.w).toBeCloseTo(page.w)
      expect(outline.h).toBeCloseTo(page.h)

      const visible = bands.map(clip)
      for (const b of visible) expect(overlap(b, clip(page))).toBeCloseTo(0)
      for (let i = 0; i < visible.length; i++)
        for (let j = i + 1; j < visible.length; j++)
          expect(overlap(visible[i], visible[j])).toBeCloseTo(0)
      // 视口面积 = 页面可见面积 + 四条色带可见面积：铺满，没有漏缝
      const sum = visible.reduce((acc, b) => acc + area(b), 0)
      expect(sum + area(clip(page))).toBeCloseTo(VW * VH, 0)
    })
  }
})
