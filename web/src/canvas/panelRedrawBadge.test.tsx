/**
 * 画布角标「位图原件 · 用脚本重画」（T-005）：单选一张有脚本、没动过的位图原件时才出；
 * 按钮交给 `redrawWithScript`（点了才跑脚本，打开项目 / 渲染画布都不跑）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const redraw = vi.hoisted(() => vi.fn(async () => true))
const fetchFacts = vi.hoisted(() => vi.fn())
vi.mock('@/lib/api', async (orig) => {
  const real = await orig<typeof import('@/lib/api')>()
  return { ...real, fetchRuntimeStatus: vi.fn(), fetchRuntimeAssets: vi.fn(), engineRender: vi.fn(), fetchPanelFontFacts: fetchFacts }
})
vi.mock('@/store/actions', async (orig) => ({
  ...(await orig<typeof import('@/store/actions')>()),
  redrawWithScript: redraw,
}))

import { t } from '@/i18n'
import { engineRender } from '@/lib/api'
import { PanelView } from '@/canvas/PanelView'
import { useRenderStore } from '@/store/renderStore'
import { useAssetStore } from '@/store/assetStore'
import { usePdfFontFacts } from '@/store/pdfFontFacts'
import { setCurrentProjectId } from '@/lib/session'
import type { PanelInfo } from '@/lib/api'
import { useSelectionStore } from '@/store/selectionStore'
import type { PanelObject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const png = (extra: Partial<PanelObject> = {}): PanelObject => ({
  id: 'p1', type: 'panel', fileId: 'plot.png', fileKind: 'raster', script: 'figure.py', figureFrame: 1,
  nativeW: 127, nativeH: 81.28, pxW: 360, pxH: 230, x: 10, y: 10, w: 63, h: 47, overrides: [], ...extra,
})
const label = (key: string) => t(`panelBadge.${key}`, { ns: 'workspace' })

let host: HTMLDivElement
let root: Root
beforeEach(() => {
  useRenderStore.getState().clear()
  useSelectionStore.getState().clear()
  redraw.mockClear()
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
})

const show = (p: PanelObject) => act(() => root.render(<PanelView obj={p} />))
const select = (...ids: string[]) => act(() => useSelectionStore.getState().set(ids))
const button = () => host.querySelector<HTMLButtonElement>('[data-badge-redraw]')

describe('位图原件角标', () => {
  it('没选中不说；单选时说「位图原件」并给「用脚本重画」', () => {
    show(png())
    expect(button()).toBeNull()
    select('p1')
    show(png())
    expect(host.querySelector('[data-panel-badge]')?.textContent).toContain(label('bitmapOriginal'))
    expect(button()?.textContent).toBe(label('redraw'))
    expect(host.querySelector('[data-badge-hint]')?.getAttribute('title')).toBe(label('redrawHint'))
  })

  it('点按钮交给 redrawWithScript；画出角标本身不跑脚本', () => {
    select('p1')
    show(png())
    expect(engineRender).not.toHaveBeenCalled()
    act(() => button()!.click())
    expect(redraw).toHaveBeenCalledWith('p1')
  })

  it('多选时不给', () => {
    select('p1', 'p2')
    show(png())
    expect(button()).toBeNull()
  })

  it.each<[string, Partial<PanelObject>]>([
    ['没脚本', { script: null }],
    ['带编辑', { overrides: [{ gid: 'axes_0.title', prop: 'text', value: 'x' }] }],
    ['矢量原件', { fileId: 'plot.pdf', fileKind: 'pdf' }],
  ])('%s：不给', (_name, extra) => {
    select('p1')
    show(png(extra))
    expect(button()).toBeNull()
  })
})

describe('PDF 原件角标（Type 3 / 缺字）', () => {
  const pdf = (extra: Partial<PanelObject> = {}) => png({ fileId: 'plot.pdf', fileKind: 'pdf', pxW: undefined, pxH: undefined, ...extra })
  const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)) })
  beforeEach(() => {
    setCurrentProjectId('A')
    useAssetStore.setState({ byId: { 'plot.pdf': { id: 'plot.pdf', kind: 'pdf', mtime: 1, script: 'figure.py' } as unknown as PanelInfo } })
    usePdfFontFacts.getState().clear()
    fetchFacts.mockReset().mockResolvedValue({ id: 'plot.pdf', readable: true, type3: true, missing_glyphs: false })
  })

  it('没选中不问；单选时问一次，Type 3 就给按钮', async () => {
    show(pdf())
    await flush()
    expect(fetchFacts).not.toHaveBeenCalled()
    select('p1')
    show(pdf())
    await flush()
    expect(fetchFacts).toHaveBeenCalledTimes(1)
    expect(host.querySelector('[data-panel-badge]')?.textContent).toContain(label('pdfType3'))
    expect(host.querySelector('[data-badge-hint]')?.getAttribute('title')).toBe(label('pdfType3Hint'))
    act(() => button()!.click())
    expect(redraw).toHaveBeenCalledWith('p1')
    expect(engineRender).not.toHaveBeenCalled()
  })

  it('有缺字时说缺字', async () => {
    fetchFacts.mockResolvedValue({ id: 'plot.pdf', readable: true, type3: true, missing_glyphs: true })
    select('p1')
    show(pdf())
    await flush()
    expect(host.querySelector('[data-panel-badge]')?.textContent).toContain(label('pdfMissingGlyphs'))
    expect(host.querySelector('[data-badge-hint]')?.getAttribute('title')).toBe(label('pdfMissingGlyphsHint'))
  })

  it('字体没问题 / 带编辑：不给', async () => {
    fetchFacts.mockResolvedValue({ id: 'plot.pdf', readable: true, type3: false, missing_glyphs: false })
    select('p1')
    show(pdf())
    await flush()
    expect(button()).toBeNull()
    usePdfFontFacts.getState().clear()
    fetchFacts.mockClear()
    show(pdf({ overrides: [{ gid: 'axes_0.title', prop: 'text', value: 'x' }] }))
    await flush()
    expect(fetchFacts).not.toHaveBeenCalled()
    expect(button()).toBeNull()
  })
})
