/**
 * 配色的画布预览落到 PanelView 上（ADR 0210 §六）：平时挂位图的面板，预览期间改挂**这一版**确切的矢量 SVG
 * 并把颜色画上去；关掉预览回到位图；这一版没有矢量 SVG（raster 档）的不假装，记成画不了。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { PanelView } from './PanelView'
import { renderKeyOf, useRenderStore, type PanelRender } from '@/store/renderStore'
import { useMountedPngStore } from '@/store/mountedPngStore'
import { useViewportStore } from '@/store/viewportStore'
import { useNativeSessionStore } from '@/store/nativeSessionStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { useUiStore } from '@/store/uiStore'
import {
  clearPalettePreview,
  holdPalettePreview,
  palettePreviewGen,
  palettePreviewMissed,
  resetPalettePreview,
  showPalettePreview,
} from '@/store/palettePreviewStore'
import { VECTOR_PREVIEW, type PreviewMetadata } from '@/lib/previewBudget'
import type { Manifest } from '@/lib/api'
import type { PanelObject } from '@/types/document'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  enginePreviewPngSnapshot: async () => ({ url: URL.createObjectURL(new Blob(['png'])), manifest: MANIFEST }),
}))

const PANEL: PanelObject = {
  id: 'p1',
  type: 'panel',
  x: 0,
  y: 0,
  w: 100,
  h: 80,
  fileId: 'Fig1.pdf',
  fileKind: 'pdf',
  nativeW: 100,
  nativeH: 80,
  script: 'fig.py',
  // 有图内修改：不在图内编辑时画布挂的是引擎位图
  overrides: [{ gid: 'axes_0.title', prop: 'fontsize', value: 9 }],
} as unknown as PanelObject

const MANIFEST = {
  stem: 'Fig1',
  size_mm: [100, 80],
  elements: [
    { gid: 'figure', role: 'figure', bbox: [0, 0, 1, 1], editable: [] },
    { gid: 'axes_0.lines_0', role: 'line', bbox: [0.1, 0.1, 0.8, 0.8], editable: [], draggable: false },
  ],
} as unknown as Manifest

const SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 283 226"><g id="axes_0.lines_0"><path d="M0 0L10 10" style="fill: none; stroke: #1f77b4"/></g></svg>'

const RASTER: PreviewMetadata = { mode: 'raster', reason: 'svg_hard_limit', svg_bytes: 1, rasterized_artist_count: 0 }

let container: HTMLDivElement
let root: Root

beforeEach(() => {
  resetPalettePreview()
  useMountedPngStore.setState({ byPanel: {} })
  useViewportStore.setState({ zoom: 1 })
  URL.createObjectURL = vi.fn(() => 'blob:mock/1')
  URL.revokeObjectURL = vi.fn()
  useRenderStore.getState().clear()
  useNativeSessionStore.setState({ sessions: {} })
  useRuntimeAssetStore.setState({ byId: {} })
  useUiStore.setState({ elementPanelId: null })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  resetPalettePreview()
})

function seed(extra: Partial<PanelRender>) {
  const key = renderKeyOf(PANEL)
  useRenderStore.getState().patch(key, {
    fileId: PANEL.fileId,
    manifest: MANIFEST,
    rev: 3,
    status: 'ready',
    lastPatches: JSON.stringify(PANEL.overrides),
    wantPatches: JSON.stringify(PANEL.overrides),
    preview: VECTOR_PREVIEW,
    ...extra,
  })
  useRenderStore.setState((s) => ({ latest: { ...s.latest, [PANEL.fileId]: key } }))
}

async function mount() {
  await act(async () => {
    root.render(<PanelView obj={PANEL} />)
  })
}

const inlineSvg = () => container.querySelector('[data-element-svg]')
const stroke = () =>
  (container.querySelector('[data-element-svg] [id="axes_0.lines_0"] path') as SVGElement | null)?.style.getPropertyValue(
    'stroke',
  ) ?? null

const preview = () =>
  act(async () => {
    showPalettePreview([
      { panelId: PANEL.id, patches: [{ gid: 'axes_0.lines_0', role: 'line', prop: 'color', value: '#ff0000' }] },
    ])
  })

describe('PanelView 与配色预览', () => {
  it('平时挂位图；预览时改挂这一版的矢量 SVG 并画上预览色；关掉回到位图', async () => {
    seed({ svg: SVG })
    await mount()
    expect(inlineSvg()).toBeNull()

    await preview()
    expect(inlineSvg()).not.toBeNull()
    expect(stroke()).toMatch(/ff0000|255, 0, 0/)
    expect(palettePreviewMissed()).toBe(0)

    await act(async () => clearPalettePreview())
    expect(inlineSvg()).toBeNull()
  })

  it('选用后挂着等图：文档换了键、新图还没到时仍停在预览色上；新一版到了预览退场', async () => {
    seed({ svg: SVG })
    await mount()
    await preview()
    await act(async () => holdPalettePreview())
    const written = {
      ...PANEL,
      overrides: [...PANEL.overrides, { gid: 'axes_0.lines_0', prop: 'color', value: '#ff0000' }],
    } as PanelObject
    await act(async () => {
      root.render(<PanelView obj={written} />)
    })
    // 新的这一版还没画出来：挂的仍是刚才画着预览的那份，没有先闪回旧色
    expect(inlineSvg()).not.toBeNull()
    expect(stroke()).toMatch(/ff0000|255, 0, 0/)

    const key = renderKeyOf(written)
    await act(async () => {
      useRenderStore.getState().patch(key, {
        fileId: PANEL.fileId,
        manifest: MANIFEST,
        rev: 4,
        status: 'ready',
        svg: SVG.replace('#1f77b4', '#ff0000').replace('viewBox', 'data-new="1" viewBox'),
        lastPatches: JSON.stringify(written.overrides),
        wantPatches: JSON.stringify(written.overrides),
        preview: VECTOR_PREVIEW,
      })
      useRenderStore.setState((st) => ({ latest: { ...st.latest, [PANEL.fileId]: key } }))
    })
    expect(palettePreviewGen(PANEL.id)).toBe(0)
  })

  it('raster 档（这一版刻意没有矢量 SVG）：不拿别的图冒充，记成画不了', async () => {
    seed({ svg: null, preview: RASTER })
    await mount()
    await preview()
    expect(inlineSvg()).toBeNull()
    expect(palettePreviewMissed()).toBe(1)
  })
})
