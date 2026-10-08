/**
 * 画布页（审计 T31；2026-10-07 §9.3：折叠行互不排斥并跨会话记住、子开关变暗不卸载、真禁用、
 * 预设下拉含「自定义」、导出摘要行）：宽、高与横竖交换同一行；开关行不走
 * 44px 标签列（「对齐参考线」在 320px 属性栏里不再折行——折行本身 jsdom 量
 * 不到，这里钉的是结构：开关的标签没有固定宽度、文字占满剩余宽度）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDocumentStore } from '@/store/documentStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject } from '@/types/document'
import { literal } from '@/i18n'
import { ROW_GRID_COLS } from '../ui/Field'
import { CanvasPage } from './CanvasPage'
import { hydrateExportDefaults } from '@/lib/exportDefaults'

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
// Radix 的 Select 打开时会 scrollIntoView / 查 pointer capture；jsdom 没有
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
Element.prototype.hasPointerCapture ??= () => false

let container: HTMLDivElement
let root: Root

beforeEach(async () => {
  localStorage.clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_canvaspage')
  useUiStore.setState({ snapEnabled: true, showGrid: true })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() =>
    root.render(
      <TooltipProvider>
        <CanvasPage />
      </TooltipProvider>,
    ),
  )
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
})

const disclosure = (title: string) =>
  [...container.querySelectorAll<HTMLButtonElement>('button[aria-expanded]')].find((b) =>
    b.textContent?.includes(title),
  )!
/** 折叠行的开关按钮（稳定锚点 data-fold） */
const fold = (id: string) => container.querySelector<HTMLButtonElement>(`[data-fold="${id}"] > button`)!

describe('CanvasPage', () => {
  it('宽、高与横竖交换在同一行', () => {
    const row = container.querySelector('[data-page-size-row]')!
    expect(row.querySelectorAll('input').length).toBe(2)
    const swap = row.querySelector<HTMLButtonElement>('button[aria-label="横竖交换"]')
    expect(swap).not.toBeNull()
    const before = useDocumentStore.getState().doc.page
    expect(before.w).not.toBe(before.h)
    act(() => swap!.click())
    const after = useDocumentStore.getState().doc.page
    expect([after.w, after.h]).toEqual([before.h, before.w])
  })

  it('正方形画布没有方向：两档都禁用，不摆一个点了也不变的控件（Codex #829 P2）', () => {
    const opts = () => [...container.querySelectorAll<HTMLButtonElement>('[data-page-orientation] [data-value]')]
    expect(opts()).toHaveLength(2)
    expect(opts().every((b) => !b.disabled)).toBe(true)
    act(() => useDocumentStore.setState((s) => ({ doc: { ...s.doc, page: { ...s.doc.page, w: 150, h: 150 } } })))
    expect(opts().every((b) => b.disabled)).toBe(true)
    expect(opts().some((b) => b.getAttribute('aria-checked') === 'true')).toBe(false)
  })

  it('折叠行互不排斥（§9.3）：打开自动对齐时背景仍开着；再点同一组只收起它自己', () => {
    act(() => fold('canvas-bg').click())
    act(() => fold('canvas-snap').click())
    expect(fold('canvas-bg').getAttribute('aria-expanded')).toBe('true')
    expect(fold('canvas-snap').getAttribute('aria-expanded')).toBe('true')
    act(() => fold('canvas-snap').click())
    expect(fold('canvas-snap').getAttribute('aria-expanded')).toBe('false')
    expect(fold('canvas-bg').getAttribute('aria-expanded')).toBe('true')
  })

  it('展开状态跨卸载与跨会话记住（canvas:* 键写进 localStorage）', () => {
    act(() => fold('canvas-guides').click())
    act(() => root.render(<TooltipProvider><div /></TooltipProvider>))
    act(() =>
      root.render(
        <TooltipProvider>
          <CanvasPage />
        </TooltipProvider>,
      ),
    )
    expect(fold('canvas-guides').getAttribute('aria-expanded')).toBe('true')
    const saved = JSON.parse(localStorage.getItem('tavotto.inspector') ?? '{}')
    expect(saved.canvasFolds?.['canvas:guides']).toBe(true)
  })

  it('关掉自动对齐：子开关不卸载，留在原位禁用变暗', () => {
    act(() => fold('canvas-snap').click())
    const rows = () => [...container.querySelectorAll<HTMLElement>('[data-fold="canvas-snap"] [data-toggle-row]')]
    expect(rows()).toHaveLength(4)
    act(() => useUiStore.setState({ snapEnabled: false }))
    expect(rows()).toHaveLength(4)
    const dim = rows().filter((r) => r.hasAttribute('data-dim'))
    expect(dim).toHaveLength(3)
    for (const r of dim) expect(r.querySelector('button')!.disabled).toBe(true)
  })

  it('透明背景时背景色是真禁用（色块与 hex 都 disabled），不是 pointer-events-none', () => {
    act(() => useDocumentStore.getState().commit(literal('透明'), (d) => {
      d.page.transparent = true
    }))
    act(() => fold('canvas-bg').click())
    const field = container.querySelector('[data-fold="canvas-bg"] [data-color-field]') as HTMLElement
    expect(field.className).not.toContain('pointer-events-none')
    expect((field.querySelector('input[type="color"]') as HTMLInputElement).disabled).toBe(true)
  })

  it('尺寸对不上任何预设时下拉显示「自定义」；导出摘要行打开同一个导出对话框', () => {
    act(() => useDocumentStore.getState().commit(literal('改尺寸'), (d) => {
      d.page.w = 123
      d.page.h = 77
    }))
    expect(container.querySelector('[data-page-preset="custom"]')).not.toBeNull()
    act(() => container.querySelector<HTMLButtonElement>('[data-canvas-export-summary]')!.click())
    expect(useUiStore.getState().exportOpen).toBe(true)
    act(() => useUiStore.getState().setExportOpen(false))
  })

  it('「自定义」只在它就是当前尺寸时出现：尺寸对上预设时下拉里没有这一档（Codex #829）', async () => {
    const options = async () => {
      const trigger = container.querySelector<HTMLElement>('[role="combobox"]')!
      await act(async () => trigger.click())
      const found = [...document.body.querySelectorAll<HTMLElement>('[role="option"]')]
      await act(async () => {
        document.activeElement?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
      })
      return found
    }
    const preset = { id: 'single', w: 85, h: 60 } // 单栏预设
    act(() => useDocumentStore.getState().commit(literal('预设尺寸'), (d) => {
      d.page.w = preset.w
      d.page.h = preset.h
    }))
    const presetOpts = await options()
    expect(presetOpts.length).toBeGreaterThan(0) // 前提：真把下拉打开了
    expect(presetOpts.some((o) => o.querySelector('[data-page-preset="custom"]'))).toBe(false)
    expect(container.querySelector(`[role="combobox"] [data-page-preset="${preset.id}"]`)).not.toBeNull()

    act(() => useDocumentStore.getState().commit(literal('改尺寸'), (d) => {
      d.page.w = 123
      d.page.h = 77
    }))
    expect(container.querySelector('[role="combobox"] [data-page-preset="custom"]')).not.toBeNull()
    const customOpts = await options()
    const custom = customOpts.find((o) => o.querySelector('[data-page-preset="custom"]'))
    expect(custom).toBeDefined()
    expect(custom!.getAttribute('data-state')).toBe('checked')
  })

  it('导出摘要跟着取回的后端默认值重读：画布页先挂着、取回后不停在空缓存的 600 ppi（Codex #829）', async () => {
    const summary = () => container.querySelector('[data-canvas-export-summary]')!.textContent ?? ''
    expect(summary()).toContain('600 ppi') // 前提：挂载时本机缓存是空的
    const prev = globalThis.fetch
    globalThis.fetch = (async () =>
      new Response(
        JSON.stringify({ defaults: { dpi: '1200', formats: ['tiff'], withProof: false, strictInspection: false } }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      )) as typeof fetch
    try {
      await act(async () => {
        await hydrateExportDefaults()
      })
    } finally {
      globalThis.fetch = prev
    }
    expect(summary()).toContain('TIFF · 1200 ppi')
  })

  it.each([
    [['pdf'], 'PDF', false],
    [['pdf', 'eps'], 'PDF · EPS', false],
    [['png'], 'PNG · 900 ppi', true],
    [['pdf', 'png'], 'PDF · PNG · 900 ppi', true],
  ])('导出摘要只在有位图格式时报 ppi：%j（Codex #829 P2，判据 hasRaster）', async (formats, text, raster) => {
    const summary = () => container.querySelector('[data-canvas-export-summary]')!.textContent ?? ''
    const prev = globalThis.fetch
    globalThis.fetch = (async () =>
      new Response(
        JSON.stringify({ defaults: { dpi: '900', formats, withProof: false, strictInspection: false } }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      )) as typeof fetch
    try {
      await act(async () => {
        await hydrateExportDefaults()
      })
    } finally {
      globalThis.fetch = prev
    }
    // 先验落点：取回的格式确实进了摘要
    expect(summary()).toContain(text)
    expect(summary().includes('ppi')).toBe(raster)
  })

  it('收起时也报得出网格状态；页面尺寸的组头不再复述下面那两个框', () => {
    // 「辅助显示」收着：摘要里要带网格间距，不能只说一个「网格」
    expect(disclosure('辅助显示').getAttribute('aria-expanded')).toBe('false')
    const gridSize = useUiStore.getState().gridSize
    expect(disclosure('辅助显示').textContent).toContain(`网格 ${gridSize} mm`)
    // 页面尺寸的组头右侧原来挂着「150.0 × 100.0 mm」——与 24px 下面的 W / H 框
    // 是同一对数，还是两种格式（打磨 L9 删掉）。判据钉住「组头里不再有那个数」，
    // 同时确认它并没有连着从可编辑的框里一起消失
    const page = useDocumentStore.getState().doc.page
    const section = [...container.querySelectorAll('section')].find((s) =>
      s.querySelector('h3')?.textContent?.includes('页面尺寸'),
    )!
    expect(section.querySelector('header')!.textContent).toBe('页面尺寸')
    const values = [...section.querySelectorAll('input')].map((i) => i.value)
    expect(values).toContain(String(page.w))
  })

  it('开关行：与数值行同一张行网格（控件从同一条竖线起排），整行可点', () => {
    act(() => fold('canvas-snap').click())
    const rows = [...container.querySelectorAll<HTMLLabelElement>('[data-toggle-row]')]
    const guides = rows.find((r) => r.textContent?.includes('对齐参考线'))
    expect(guides).toBeDefined()
    expect(guides!.tagName).toBe('LABEL')
    // 列模板与 Row 的 grid 同出 `ui/Field.ROW_GRID_COLS`（标签 --insp-label · 控件 · 20px 状态槽）
    expect(guides!.className).toContain(ROW_GRID_COLS)
    const label = guides!.querySelector('span')!
    const before = useUiStore.getState().snapToGuides
    act(() => label.click())
    expect(useUiStore.getState().snapToGuides).toBe(!before)
  })
})
