/**
 * 时间线预览清晰度（beta 0.19.0b1 反馈「预览特别糊」）。
 *
 * 根因：`LayoutSnapshot` 的面板取图档位与文字字号写死（200 档、6px），而预览对话框里
 * 页面可以有上千 px 宽——位图被 CSS 放大 5 倍以上。判据：取图档位的像素宽 ≥ 面板的
 * 显示宽 × DPR（直到档位表顶 3200），文字字号随显示宽缩放。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { LayoutSnapshot, snapshotBucket } from '@/components/VersionDialog'
import { RENDER_BUCKETS } from '@/lib/units'
import type { FigureDocument, PanelObject, TextObject } from '@/types/document'

globalThis.IS_REACT_ACT_ENVIRONMENT = true

const panel: PanelObject = {
  id: 'p', type: 'panel', fileId: 'plot.pdf', fileKind: 'pdf', x: 0, y: 0, w: 150, h: 100,
  nativeW: 150, nativeH: 100, overrides: [],
}
const label: TextObject = {
  id: 't', type: 'text', text: 'Fig', sizePt: 10, bold: false, color: '#000', align: 'left',
  x: 0, y: 0, w: 30, h: 8,
}
const doc: FigureDocument = {
  schema: 2, name: 'x', page: { w: 150, h: 100 }, objects: [panel, label], guides: [],
}

let el: HTMLDivElement
let root: Root
beforeEach(() => {
  el = document.createElement('div')
  document.body.appendChild(el)
  root = createRoot(el)
})
afterEach(() => {
  act(() => root.unmount())
  el.remove()
  Object.defineProperty(window, 'devicePixelRatio', { value: 1, configurable: true })
})

const dpr = (v: number) => Object.defineProperty(window, 'devicePixelRatio', { value: v, configurable: true })
const requestedW = () => Number(new URL(el.querySelector('img')!.getAttribute('src')!, 'http://x').searchParams.get('w'))

describe('snapshotBucket：档位像素宽 ≥ 显示宽 × DPR', () => {
  it.each([
    [150, 150, 400, 1],
    [150, 150, 900, 1],
    [150, 150, 900, 2],
    [75, 150, 1200, 2],
    [150, 150, 60, 1],
  ])('面板 %d / 页宽 %d、显示 %dpx、DPR %d', (pw, pageW, shown, d) => {
    const need = (pw / pageW) * shown * d
    const got = snapshotBucket(pw, pageW, shown, d)
    expect(RENDER_BUCKETS).toContain(got)
    expect(got).toBeGreaterThanOrEqual(Math.min(need, RENDER_BUCKETS[RENDER_BUCKETS.length - 1]))
  })
})

describe('LayoutSnapshot 渲染', () => {
  it.each([
    [900, 1],
    [900, 2],
    [1400, 2],
  ])('displayWidth=%d、DPR=%d：请求的位图 ≥ 显示宽 × DPR', async (shown, d) => {
    dpr(d)
    await act(async () => root.render(<LayoutSnapshot doc={doc} displayWidth={shown} />))
    expect(requestedW()).toBeGreaterThanOrEqual(Math.min(shown * d, 3200))
  })

  it('不给 displayWidth = 缩略档 200（列表行为不变）', async () => {
    await act(async () => root.render(<LayoutSnapshot doc={doc} />))
    expect(requestedW()).toBe(200)
  })

  it('文字字号随显示宽缩放：10pt 在 900px/150mm 的页面上约 21px，而不是固定 6px', async () => {
    await act(async () => root.render(<LayoutSnapshot doc={doc} displayWidth={900} />))
    const t = [...el.querySelectorAll<HTMLElement>('div')].find((d) => d.textContent === 'Fig' && d.style.fontSize !== '')!
    expect(parseFloat(t.style.fontSize)).toBeCloseTo(10 * (25.4 / 72) * (900 / 150), 1)
  })
})
