/**
 * 编辑预览的三档表示法（ADR 0022 / issue #181）。
 *
 * 一句话判据：**画法可以换，能编辑的东西一个都不许少。**
 *
 * `raster` 档最容易被做错成「图太大所以不能编辑了」——而 #181 的用户要的
 * 恰恰是编辑这张图。所以这里每一条 raster 用例都同时断言两件事：画布上
 * 挂的是位图，**且命中层还在**。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { PanelView } from './PanelView'
import { renderKeyOf, useRenderStore, type PanelRender } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'
import { VECTOR_PREVIEW, type PreviewMetadata } from '@/lib/previewBudget'
import type { Manifest } from '@/lib/api'
import type { PanelObject } from '@/types/document'

const previewPng = vi.fn()

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  enginePreviewPng: (id: string, patches: unknown[], bucket: number) => {
    previewPng(id, patches, bucket)
    return Promise.resolve(new Blob(['png']))
  },
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
  overrides: [{ gid: 'title', prop: 'fontsize', value: 9 }],
} as unknown as PanelObject

/** 命中层要有东西可命中，否则「命中层还在」是句空话 */
const MANIFEST = {
  stem: 'Fig1',
  size_mm: [100, 80],
  elements: [
    { gid: 'figure', role: 'figure', bbox: [0, 0, 1, 1], editable: [] },
    {
      gid: 'axes_0.title',
      role: 'title',
      bbox: [0.3, 0.02, 0.4, 0.08],
      editable: [{ prop: 'fontsize', type: 'number', value: 9 }],
      draggable: true,
      anchor: [0.5, 0.06],
    },
  ],
} as unknown as Manifest

const RASTER: PreviewMetadata = {
  mode: 'raster',
  reason: 'svg_hard_limit',
  svg_bytes: 126_132_735,
  rasterized_artist_count: 0,
}

const HYBRID: PreviewMetadata = {
  mode: 'hybrid',
  reason: 'complexity_budget',
  svg_bytes: 900_000,
  rasterized_artist_count: 3,
}

let container: HTMLDivElement
let root: Root

beforeEach(() => {
  previewPng.mockClear()
  URL.createObjectURL = vi.fn(() => 'blob:mock/1')
  URL.revokeObjectURL = vi.fn()
  useRenderStore.getState().clear()
  useUiStore.setState({ elementPanelId: PANEL.id }) // 编辑态：三档的区别只在这里显现
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
})

function seed(extra: Partial<PanelRender>) {
  useRenderStore.getState().patch(renderKeyOf(PANEL), {
    fileId: PANEL.fileId,
    manifest: MANIFEST,
    rev: 3,
    status: 'ready',
    lastPatches: JSON.stringify(PANEL.overrides),
    preview: VECTOR_PREVIEW,
    ...extra,
  })
}

async function mount() {
  await act(async () => {
    root.render(<PanelView obj={PANEL} />)
  })
}

const inlineSvg = () => container.querySelector('[data-element-svg]')
const hitLayer = () => container.querySelector('[data-authority="ready"]')

describe('PanelView：三档预览表示法', () => {
  it('vector：编辑态内联 SVG（今天的行为，一字不改）', async () => {
    seed({ svg: '<svg id="v"/>' })
    await mount()

    expect(inlineSvg()?.innerHTML).toContain('id="v"')
    expect(hitLayer()).not.toBeNull()
    expect(container.querySelector('img')).toBeNull()
  })

  it('hybrid：有 SVG 就照旧内联（混合产物仍然是一份 SVG）', async () => {
    seed({ svg: '<svg id="h"/>', preview: HYBRID })
    await mount()

    expect(inlineSvg()?.innerHTML).toContain('id="h"')
    expect(hitLayer()).not.toBeNull()
  })

  it('raster：画布走位图，**命中层照旧在**', async () => {
    seed({ svg: null, preview: RASTER })
    await mount()

    // 一个 dangerouslySetInnerHTML 都没有——这正是不变量 3 要的结果
    expect(inlineSvg()).toBeNull()
    const img = container.querySelector('img')
    expect(img?.getAttribute('src')).toBe('blob:mock/1')
    // 位图按**这个面板自己的 patches** 出（状态中立的既有链路）
    expect(previewPng).toHaveBeenCalledWith('Fig1.pdf', PANEL.overrides, expect.any(Number))

    // 「显示降级 ≠ 关闭语义编辑」：命中层在、且拿得到几何权威
    expect(hitLayer()).not.toBeNull()
    expect(container.querySelector('[data-authority="syncing"]')).toBeNull()
  })

  it('退回窗口里表示法跟着 SVG 走：raster 面板不闪一下矢量图', async () => {
    // 用户刚改完一个值：**自己这一版还没画出来**（新键，没有 manifest），
    // 画布退回该文件最近画好的那份——而那份是 raster。
    //
    // `mergeRender` 里表示法不跟着 SVG 走的话，这里拿到的是自己那份的默认值
    // （vector）：PanelView 于是既不内联 SVG（根本没有）、又不取引擎位图，
    // 退到磁盘原图——用户看到的是**没有任何编辑的那张图**闪一下。
    const other = { ...PANEL, overrides: [] } as unknown as PanelObject
    useRenderStore.getState().patch(renderKeyOf(other), {
      fileId: PANEL.fileId,
      manifest: MANIFEST,
      rev: 3,
      status: 'ready',
      lastPatches: '[]',
      svg: null,
      preview: RASTER,
    })
    useRenderStore.setState((s) => ({ latest: { ...s.latest, [PANEL.fileId]: renderKeyOf(other) } }))
    // 自己那一版只排了队，还没有结果
    useRenderStore.getState().patch(renderKeyOf(PANEL), {
      fileId: PANEL.fileId,
      status: 'rendering',
      wantPatches: JSON.stringify(PANEL.overrides),
    })
    await mount()

    expect(inlineSvg()).toBeNull()
    // 位图照旧在取（画布继续显示上一张 raster 预览），没有退到磁盘原图
    expect(previewPng).toHaveBeenCalled()
  })

  it('raster 面板不许拿同文件另一个变体的矢量 SVG 冒充自己', async () => {
    const other = { ...PANEL, overrides: [] } as unknown as PanelObject
    useRenderStore.getState().patch(renderKeyOf(other), {
      fileId: PANEL.fileId,
      manifest: MANIFEST,
      rev: 1,
      status: 'ready',
      lastPatches: '[]',
      svg: '<svg id="stale"/>',
      preview: VECTOR_PREVIEW,
    })
    useRenderStore.setState((s) => ({ latest: { ...s.latest, [PANEL.fileId]: renderKeyOf(other) } }))
    seed({ svg: null, preview: RASTER })
    await mount()

    expect(container.innerHTML).not.toContain('id="stale"')
    expect(inlineSvg()).toBeNull()
  })

  it('raster：画布说得出「挂的是这一版自己的图」', async () => {
    seed({ svg: null, preview: RASTER })
    await mount()

    // fallback = 挂着**别人**的图（几何交互停摆）；raster = 挂着自己的，只是画法不同。
    // 诊断与 e2e 都读这个属性，报错就是报错。
    expect(container.querySelector('[data-display]')?.getAttribute('data-display')).toBe('raster')
  })

  it('raster：角标解释一次，不弹对话框、不责怪用户', async () => {
    seed({ svg: null, preview: RASTER })
    await mount()

    const badge = [...container.querySelectorAll('span')].find((el) => el.title)
    expect(badge?.textContent).toBe('低内存编辑预览')
    expect(badge?.title).toContain('导出质量')
  })

  it('老后端不返回 preview：行为与从前逐字节相同', async () => {
    // `EMPTY.preview` 就是 VECTOR_PREVIEW——这条钉的是「加字段协议没有把
    // 旧路径改掉」，而不是某个新分支好用
    seed({ svg: '<svg id="legacy"/>' })
    await mount()

    expect(inlineSvg()?.innerHTML).toContain('id="legacy"')
  })
})
