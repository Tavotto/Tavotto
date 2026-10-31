import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { SourceBackedReviewDialog, percentText } from './SourceBackedReviewDialog'
import { useSourceBackedReview, type ReviewRequest } from '@/store/sourceBackedReview'

globalThis.IS_REACT_ACT_ENVIRONMENT = true
const request = (mode: 'adopt' | 'changed' = 'adopt'): ReviewRequest => ({
  mode, panelId: 'p', fileId: 'plot.png', originalSrc: '/api/file?id=plot.png', reconstructionSrc: 'data:image/png;base64,R',
  verdict: { status: 'needs_confirmation', reconstructionSha256: 'c'.repeat(64),
    metrics: { differingPixels: 30, differingRatio: 0.03, totalPixels: 1000, bbox: [10, 20, 30, 40] } },
})
let el: HTMLDivElement, root: Root
beforeEach(() => { el = document.createElement('div'); document.body.appendChild(el); root = createRoot(el); useSourceBackedReview.getState().clear() })
afterEach(() => { act(() => root.unmount()); el.remove() })
const q = (sel: string) => document.body.querySelector(sel) as HTMLElement | null
const img = (kind: string) => q(`[data-source-review-pane="${kind}"] img`) as HTMLImageElement
const fire = (kind: string, type: 'load' | 'error') => act(async () => { img(kind).dispatchEvent(new Event(type)) })
const loadBoth = async () => { await fire('original', 'load'); await fire('redrawn', 'load') }
const confirmBtn = () => q('[data-source-review-confirm]') as HTMLButtonElement

describe('SourceBackedReviewDialog', () => {
  it('one sentence with the percentage, original left and redraw right, one primary button', async () => {
    await act(async () => root.render(<SourceBackedReviewDialog />))
    expect(q('[data-source-review]')).toBeNull()
    let answer: Promise<boolean>
    await act(async () => { answer = useSourceBackedReview.getState().ask(request()) })
    expect(document.body.textContent).toMatch(/3% 像素不同|3% of pixels/)
    expect(document.body.textContent).toMatch(/原 PNG 不会被改动|never changed/)
    const panes = [...document.body.querySelectorAll('[data-source-review-pane]')].map((n) => n.getAttribute('data-source-review-pane'))
    expect(panes).toEqual(['original', 'redrawn'])
    expect(q('[data-source-review-pane="original"] img')!.getAttribute('src')).toBe('/api/file?id=plot.png')
    expect(q('[data-source-review-pane="redrawn"] img')!.getAttribute('src')).toBe('data:image/png;base64,R')
    expect(document.body.querySelectorAll('button[data-source-review-confirm]')).toHaveLength(1)
    // 差异包围框：原图加载后按自然尺寸（100x100）换算成百分比
    const img = q('[data-source-review-pane="original"] img') as HTMLImageElement
    Object.defineProperty(img, 'naturalWidth', { value: 100 }); Object.defineProperty(img, 'naturalHeight', { value: 100 })
    await act(async () => { img.dispatchEvent(new Event('load')) })
    expect(q('[data-source-review-diff="original"]')!.style.left).toBe('10%')
    expect(q('[data-source-review-diff="redrawn"]')!.style.width).toBe('20%')
    await loadBoth()
    await act(async () => q('[data-source-review-confirm]')!.click())
    expect(await answer!).toBe(true)
    expect(useSourceBackedReview.getState().request).toBeNull()
  })
  it('keeping the original and dismissing both answer no', async () => {
    await act(async () => root.render(<SourceBackedReviewDialog />))
    let a: Promise<boolean>
    await act(async () => { a = useSourceBackedReview.getState().ask(request('changed')) })
    expect(document.body.textContent).toMatch(/脚本、数据或环境变了|script, data or environment changed/)
    await act(async () => q('[data-source-review-keep]')!.click())
    expect(await a!).toBe(false)
    await act(async () => { a = useSourceBackedReview.getState().ask(request()) })
    await act(async () => useSourceBackedReview.getState().clear())
    expect(await a!).toBe(false)
  })
  it('the confirm button needs BOTH images loaded; a failed image fails closed with one sentence and the keep button still works', async () => {
    await act(async () => root.render(<SourceBackedReviewDialog />))
    let a: Promise<boolean>
    await act(async () => { a = useSourceBackedReview.getState().ask(request()) })
    expect(confirmBtn().disabled).toBe(true) // 都在加载
    await fire('original', 'load')
    expect(confirmBtn().disabled).toBe(true) // 只加载了一张
    await fire('redrawn', 'error')
    expect(confirmBtn().disabled).toBe(true)
    expect(document.body.textContent).toMatch(/没加载出来|didn't load/)
    await act(async () => confirmBtn().click())
    expect(useSourceBackedReview.getState().request).not.toBeNull() // 点了也不写
    await act(async () => q('[data-source-review-keep]')!.click())
    expect(await a!).toBe(false)
    await act(async () => { a = useSourceBackedReview.getState().ask(request()) })
    await fire('redrawn', 'load')
    expect(confirmBtn().disabled).toBe(true)
    await fire('original', 'error')
    expect(confirmBtn().disabled).toBe(true)
    await act(async () => useSourceBackedReview.getState().clear())
    await a!
    await act(async () => { a = useSourceBackedReview.getState().ask(request()) })
    await loadBoth()
    expect(confirmBtn().disabled).toBe(false)
    await act(async () => useSourceBackedReview.getState().clear())
    await a!
  })
  it('percentages never round a real difference down to zero', () => {
    expect(percentText(0.03, 30)).toBe('3')
    expect(percentText(0.123456, 1)).toBe('12')
    expect(percentText(0.00001, 3)).toBe('<0.01')
    expect(percentText(0.0123, 3)).toBe('1.23')
  })
})
