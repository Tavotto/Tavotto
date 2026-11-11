/**
 * 导出前的 EPS 半透明提醒（`EpsOpacityPrompt` 接在 `ExportDialog` 的 `start()` 上）。
 * 单图带标注 + 选了 EPS + 标注里有半透明 → 点导出先问；两种选择各发各的请求；
 * 其余情形不出提示、请求不变。
 */
import { act } from 'react'
import { literal } from '@/i18n'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  postTelemetryEvent: vi.fn(() => Promise.resolve({ accepted: true })),
  fetchExportDefaultsRemote: vi.fn(() => Promise.resolve(undefined)),
  enginePreviewPng: vi.fn(() => Promise.resolve(new Blob(['png']))),
  fetchRuntimeAssets: vi.fn(),
  fetchRuntimePreviewForState: vi.fn(() => Promise.resolve(null)),
}))

import { ExportDialog } from '@/components/ExportDialog'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { renderKey, useRenderStore } from '@/store/renderStore'
import { resetExportState } from '@/store/exportStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useWorkspaceStore } from '@/store/workspace'
import { emptyProject, type CanvasObject, type PanelObject } from '@/types/document'

globalThis.IS_REACT_ACT_ENVIRONMENT = true
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}

const panel: PanelObject = {
  id: 'p1',
  type: 'panel',
  fileId: 'Fig1.pdf',
  fileKind: 'pdf',
  nativeW: 80,
  nativeH: 60,
  overrides: [],
  x: 30,
  y: 20,
  w: 80,
  h: 60,
}

const shape = (over: Partial<CanvasObject> = {}): CanvasObject =>
  ({
    id: 's1',
    type: 'shape',
    shape: 'rect',
    x: 40,
    y: 30,
    w: 20,
    h: 10,
    strokePt: 1,
    color: '#ff0000',
    fill: '#ff0000',
    ...over,
  }) as CanvasObject

let container: HTMLDivElement
let root: Root
let exportBodies: Record<string, any>[]

const manifest = {
  stem: 'Fig1',
  size_mm: [80, 60],
  elements: [],
}

function stubFetch() {
  exportBodies = []
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const json = (body: unknown) =>
      new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
    if (url.includes('/api/export/start') || url.endsWith('/api/export')) {
      exportBodies.push(JSON.parse(String(init?.body ?? '{}')))
      return json({ job_id: 'j1', status: 'done', outputs: [], warnings: [], conflicts: [], export_dir: '/out', error: null })
    }
    if (url.includes('/api/export/validate')) {
      const req = JSON.parse(String(init?.body ?? '{}'))
      return json({
        ok: true,
        conflicts: [],
        writable: true,
        route: {
          scope: req.scope,
          formats: req.formats,
          // 这张图能重画出 EPS（后端 offers.eps）
          panels: { 'Fig1.pdf': { source: 'disk', embeds_disk_bitmap: false, formats: {}, offers: { eps: { how: 'redraw' } } } },
        },
      })
    }
    return json({ figures_dir: '/figs', panels: [] })
  }) as typeof fetch
}

const text = () => document.body.textContent ?? ''
const button = (label: string) =>
  [...document.body.querySelectorAll('button')].find((b) => b.textContent?.includes(label))
const formatBox = (name: string) =>
  document.body.querySelector<HTMLInputElement>(`[data-export-formats] [data-export-format="${name}"] input[type="checkbox"]`)!
const prompt = () => document.body.querySelector('[data-dialog="eps-opacity"]')
const choice = (c: 'pdf' | 'opaque' | 'cancel') => document.body.querySelector(`[data-eps-opacity="${c}"]`)!
const click = async (el: Element) => {
  await act(async () => {
    ;(el as HTMLElement).click()
    await new Promise<void>((r) => setTimeout(r, 0))
  })
}
const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 400)) })

/** 单图档：图在 (30,20)、一个标注；EPS 勾上（其余格式用默认），route 说 EPS 给得出 */
async function open(annotations: CanvasObject[], formats: string[] = ['pdf', 'eps']) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_eps_opacity')
  useDocumentStore.getState().commit(literal('准备'), (d) => {
    d.page = { w: 180, h: 120 }
    d.objects = [{ ...panel }, ...annotations]
  })
  useAssetStore.setState({ byId: { 'Fig1.pdf': { id: 'Fig1.pdf', mtime: 1 } } } as never)
  const key = renderKey('Fig1.pdf', [])
  useRenderStore.setState({
    byKey: {
      [key]: {
        fileId: 'Fig1.pdf', rev: 1, manifest, svg: null, status: 'ready', error: null, code: '', module: '',
        traceback: '', warnings: [], timings: {}, stale: false, lastPatches: '[]', wantPatches: '[]', previewDpi: null,
      },
    } as never,
    latest: { 'Fig1.pdf': key },
    tracked: {},
    building: {},
  })
  useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
  useUiStore.getState().setExportOpen(true)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <ExportDialog />
      </TooltipProvider>,
    )
  })
  await settle() // route 回来，EPS 才可勾
  // 把格式勾成想要的
  for (const f of ['pdf', 'png', 'eps']) {
    if (formatBox(f).checked === formats.includes(f)) continue
    await click(formatBox(f))
    await settle() // 改了格式，route 随之重取；没取回来 EPS 是灰的，点不动
  }
}

beforeEach(() => {
  localStorage.clear()
  resetExportState()
  useWorkspaceStore.setState({ mode: 'layout', activePanelId: null })
  useSelectionStore.getState().clear()
  stubFetch()
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.restoreAllMocks()
})

describe('选了 EPS 且标注里有半透明', () => {
  it('点开始导出：先出提示（一句话 + 三个出口），这一刻不发请求', async () => {
    await open([shape({ fillOpacity: 0.5 })])
    await click(button('开始导出')!)
    expect(prompt(), '出提示').toBeTruthy()
    expect(prompt()!.textContent).toContain('EPS 不支持半透明')
    expect(prompt()!.textContent).toContain('这张图有 1 个半透明标注')
    expect(choice('pdf').textContent).toBe('改导出 PDF')
    expect(choice('opaque').textContent).toBe('EPS 按不透明导出')
    expect(choice('cancel').textContent).toBe('取消')
    expect(exportBodies, '还没选，什么都没发').toHaveLength(0)
  })

  it('改导出 PDF：EPS 换成 PDF（PDF 已勾就只是去掉 EPS），请求里没有 eps_opaque，对话框里的勾选跟着变', async () => {
    await open([shape({ fillOpacity: 0.5 })], ['pdf', 'png', 'eps'])
    await click(button('开始导出')!)
    await click(choice('pdf'))
    expect(exportBodies).toHaveLength(1)
    expect(exportBodies[0].formats).toEqual(['pdf', 'png'])
    expect('eps_opaque' in exportBodies[0]).toBe(false)
    expect(formatBox('eps').checked).toBe(false)
    expect(prompt()?.getAttribute('data-state')).not.toBe('open')
  })

  it('只勾了 EPS 时「改导出 PDF」= 换成 PDF', async () => {
    await open([shape({ fillOpacity: 0.5 })], ['eps'])
    await click(button('开始导出')!)
    await click(choice('pdf'))
    expect(exportBodies[0].formats).toEqual(['pdf'])
  })

  it('EPS 按不透明导出：格式不变、带 eps_opaque: true，标注自己的半透明仍原样发（其余格式保留）', async () => {
    await open([shape({ fillOpacity: 0.5 })], ['pdf', 'eps'])
    await click(button('开始导出')!)
    await click(choice('opaque'))
    expect(exportBodies).toHaveLength(1)
    expect(exportBodies[0].formats).toEqual(['pdf', 'eps'])
    expect(exportBodies[0].eps_opaque).toBe(true)
    expect(exportBodies[0].original.objects[0].fill_opacity).toBe(0.5)
  })

  it('取消：不导出，也不改格式', async () => {
    await open([shape({ fillOpacity: 0.5 })], ['pdf', 'eps'])
    await click(button('开始导出')!)
    await click(choice('cancel'))
    expect(exportBodies).toHaveLength(0)
    expect(formatBox('eps').checked).toBe(true)
  })

  it('箭头整体不透明度也算；个数按半透明标注数', async () => {
    const arrow = {
      id: 'a1', type: 'arrow', x: 40, y: 30, w: 20, h: 5, start: { rx: 0, ry: 0.5 }, end: { rx: 1, ry: 0.5 },
      strokePt: 1, color: '#000', head: 'end', strokeOpacity: 0.5,
    } as CanvasObject
    await open([shape({ fillOpacity: 0.5 }), arrow, shape({ id: 's2' })])
    await click(button('开始导出')!)
    expect(prompt()!.textContent).toContain('这张图有 2 个半透明标注')
  })
})

describe('不该提醒的时候', () => {
  it('标注都不透明：不出提示，请求里没有 eps_opaque', async () => {
    await open([shape()])
    await click(button('开始导出')!)
    expect(prompt()).toBeNull()
    expect(exportBodies).toHaveLength(1)
    expect(exportBodies[0].formats).toContain('eps')
    expect('eps_opaque' in exportBodies[0]).toBe(false)
  })

  it('有半透明但没选 EPS：不出提示，请求原样', async () => {
    await open([shape({ fillOpacity: 0.5 })], ['pdf'])
    await click(button('开始导出')!)
    expect(prompt()).toBeNull()
    expect(exportBodies).toHaveLength(1)
    expect(exportBodies[0].formats).toEqual(['pdf'])
    expect('eps_opaque' in exportBodies[0]).toBe(false)
    expect(exportBodies[0].original.objects[0].fill_opacity).toBe(0.5)
  })

  it('半透明的标注是隐藏的（不会进导出）：不出提示', async () => {
    await open([shape({ fillOpacity: 0.5, hidden: true })])
    await click(button('开始导出')!)
    expect(prompt()).toBeNull()
    expect(exportBodies).toHaveLength(1)
    expect(text()).not.toContain('EPS 不支持半透明')
  })

  it('没有标注：不出提示', async () => {
    await open([])
    await click(button('开始导出')!)
    expect(prompt()).toBeNull()
    expect(exportBodies).toHaveLength(1)
  })
})
