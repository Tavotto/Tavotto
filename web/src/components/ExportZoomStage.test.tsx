/**
 * 导出预览的缩放舞台：contain 铺满不留边距；滚轮以指针为中心缩放；放大后可拖动、夹在内容范围内；
 * 双击 / 小按钮回到适合大小；放大按显示宽度 × 缩放 × DPR 挑 `/api/render` 的更高档（只增不减）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ExportZoomStage } from './ExportZoomStage'
import { initI18n } from '@/i18n'
import { TooltipProvider } from '@/components/ui/Tooltip'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const BOX = { w: 400, h: 300 }
let host: HTMLDivElement
let root: Root
let seen: number[]

beforeEach(() => {
  initI18n('zh-CN')
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  seen = []
  // jsdom 不排版：舞台 400×300，ResizeObserver 一挂上就报一次
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(BOX.w)
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(BOX.h)
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    x: 0, y: 0, left: 0, top: 0, right: BOX.w, bottom: BOX.h, width: BOX.w, height: BOX.h, toJSON: () => ({}),
  })
  vi.stubGlobal('ResizeObserver', class {
    cb: () => void
    constructor(cb: () => void) { this.cb = cb }
    observe() { this.cb() }
    disconnect() {}
  })
  vi.stubGlobal('devicePixelRatio', 1)
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

async function mount(ratio: number) {
  await act(async () => {
    root.render(
      <TooltipProvider>
        <ExportZoomStage ratio={ratio}>
          {(px) => {
            seen.push(px)
            return <span data-probe />
          }}
        </ExportZoomStage>
      </TooltipProvider>,
    )
  })
}
const stage = () => host.querySelector<HTMLElement>('[data-export-preview-zoom]')!
const content = () => host.querySelector<HTMLElement>('[data-export-preview-content]')!
const scaleOf = () => Number(stage().dataset.exportPreviewZoom)
const wheel = (deltaY: number, x: number, y: number, extra: WheelEventInit = {}) =>
  act(async () => {
    stage().dispatchEvent(new WheelEvent('wheel', { deltaY, clientX: x, clientY: y, bubbles: true, cancelable: true, ...extra }))
  })

describe('ExportZoomStage', () => {
  it('contain：宽图左右顶满、高图上下顶满，没有边距', async () => {
    await mount(2) // 400×300 的舞台装 2:1 → 400×200，左右顶满
    expect(content().style.width).toBe('400px')
    expect(content().style.height).toBe('200px')
    await act(async () => root.render(
      <TooltipProvider><ExportZoomStage ratio={0.5}>{() => null}</ExportZoomStage></TooltipProvider>,
    ))
    expect(content().style.height).toBe('300px') // 1:2 → 150×300，上下顶满
    expect(content().style.width).toBe('150px')
  })

  it('滚轮以指针为中心缩放：指针下的那一点不动', async () => {
    await mount(4 / 3) // 400×300 正好铺满
    // 指针在舞台中心偏右 100：放大后内容向左挪，使指针下那点不动
    await wheel(-300, 300, 150)
    const s = scaleOf()
    expect(s).toBeGreaterThan(1)
    const m = /translate\((-?[\d.]+)px, (-?[\d.]+)px\)/.exec(content().style.transform)!
    // 指针相对中心 q=100；t' = q - q*s（原先 t=0）
    expect(Number(m[1])).toBeCloseTo(100 - 100 * s, 1)
    expect(Number(m[2])).toBeCloseTo(0, 3)
  })

  it('缩放有上下限：缩不到比适合大小更小；放大有封顶', async () => {
    await mount(4 / 3)
    await wheel(500, 200, 150)
    expect(scaleOf()).toBe(1)
    for (let i = 0; i < 20; i++) await wheel(-1000, 200, 150)
    expect(scaleOf()).toBe(12)
  })

  it('触控板捏合（ctrlKey）同样缩放，且比滚轮灵敏', async () => {
    await mount(4 / 3)
    await wheel(-10, 200, 150, { ctrlKey: true })
    const pinch = scaleOf()
    expect(pinch).toBeGreaterThan(1.05)
  })

  it('滚轮事件被拦下（preventDefault），页面 / 对话框正文不会跟着滚', async () => {
    await mount(4 / 3)
    const ev = new WheelEvent('wheel', { deltaY: -100, clientX: 200, clientY: 150, bubbles: true, cancelable: true })
    await act(async () => { stage().dispatchEvent(ev) })
    expect(ev.defaultPrevented).toBe(true)
  })

  it('双击与小按钮都回到适合大小；适合大小时按钮不出现', async () => {
    await mount(4 / 3)
    expect(host.querySelector('[data-export-preview-reset]')).toBeNull()
    await wheel(-500, 200, 150)
    expect(scaleOf()).toBeGreaterThan(1)
    await act(async () => { host.querySelector<HTMLElement>('[data-export-preview-reset]')!.click() })
    expect(scaleOf()).toBe(1)
    expect(content().style.transform).toContain('translate(0px, 0px)')
    await wheel(-500, 200, 150)
    await act(async () => { stage().dispatchEvent(new MouseEvent('dblclick', { bubbles: true })) })
    expect(scaleOf()).toBe(1)
  })

  it('放大后可拖动平移，且夹在内容范围内（不会把内容拖出视野）', async () => {
    await mount(4 / 3)
    await wheel(-200, 200, 150) // 约 ×1.35，居中
    const s = scaleOf()
    stage().setPointerCapture = vi.fn()
    // jsdom 的 MouseEvent 没有 pointerId：补上
    const fire = (type: string, x: number, y: number) =>
      act(async () => {
        const e = new MouseEvent(type, { clientX: x, clientY: y, button: 0, bubbles: true }) as MouseEvent & { pointerId: number }
        Object.defineProperty(e, 'pointerId', { value: 1 })
        stage().dispatchEvent(e)
      })
    await fire('pointerdown', 200, 150)
    await fire('pointermove', 190, 140) // 左上各挪 10
    const m1 = /translate\((-?[\d.]+)px, (-?[\d.]+)px\)/.exec(content().style.transform)!
    expect(Number(m1[1])).toBeCloseTo(-10, 3)
    expect(Number(m1[2])).toBeCloseTo(-10, 3)
    await fire('pointermove', -2000, -2000)
    const m2 = /translate\((-?[\d.]+)px, (-?[\d.]+)px\)/.exec(content().style.transform)!
    expect(Number(m2[1])).toBeCloseTo(-(400 * s - 400) / 2, 1) // 夹在边上
    expect(Number(m2[2])).toBeCloseTo(-(300 * s - 300) / 2, 1)
  })

  it('取图档位随放大升高、缩回去不降（只增不减）', async () => {
    await mount(4 / 3)
    expect(seen.at(-1)).toBe(400) // 400px × DPR1 → 400 档
    await wheel(-2000, 200, 150)
    const high = seen.at(-1)!
    expect(high).toBeGreaterThan(400)
    await wheel(2000, 200, 150)
    expect(scaleOf()).toBe(1)
    expect(seen.at(-1)).toBe(high)
  })
})
