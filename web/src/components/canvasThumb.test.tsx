/**
 * 版本列表的缩略图与画布列表的缩略图是**同一份绘制组件**（fu-thumbs）。
 *
 * 「复用」这件事光看 import 判不出来：谁都可以在版本抽屉里另画一份长得差不多
 * 的 SVG，代码评审也未必看得出来，而两份实现会各自演进到用户能看出差别为止。
 * 所以判据落在**产物**上——同样的内容，两处画出来的 DOM 逐字相同。
 *
 * **两侧必须是两条真实路径**，不能都直接 render 那个组件：那样的话对比的是
 * 同一份实现的两次调用，任何改动都同时改两边，diff 恒等为空，而空看起来就像
 * 「没有差别」。所以左边跑 `CanvasList`（喂内存里的 `CanvasObject`），右边跑
 * `VersionDrawer`（喂列表端点发来的草图），中间隔着组件挑选、页面尺寸传递、
 * 草图字段三件真会写错的事。
 *
 * 这条判据顺带把「草图带够了没有」钉住了：右边只有 type/x/y/w/h/fileId/
 * fileKind/text，左边是完整的文档对象——产物相同才说明草图没漏东西。
 *
 * 它**不覆盖**组件自己画得对不对（改了组件两边一起变）。那一档在
 * `left/canvasList.test.tsx` 与 `VersionDialog.test.tsx` 的内容判据里。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchVersions: vi.fn(),
  fetchVersionDoc: vi.fn(),
}))

import { fetchVersionDoc, fetchVersions, type LayoutVersionMeta } from '@/lib/api'
import { literal } from '@/i18n'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { CanvasList } from '@/components/left/CanvasList'
import { VersionDrawer } from '@/components/VersionDialog'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type CanvasObject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
globalThis.fetch = vi.fn(async () => new Response('{}', { status: 200 })) as typeof fetch

const PAGE = { w: 150, h: 100 }

/** 内存里的文档对象（画布列表那一侧喂的东西） */
const docObjects = [
  {
    id: 'p1',
    type: 'panel',
    fileId: 'a.pdf',
    fileKind: 'pdf',
    nativeW: 40,
    nativeH: 30,
    overrides: [{ gid: 'g1', prop: 'pos_frac', value: [0.1, 0.2] }],
    x: 4,
    y: 6,
    w: 40,
    h: 30,
  },
  {
    id: 't1',
    type: 'text',
    text: '第一张版上的说明',
    x: 5,
    y: 50,
    w: 30,
    h: 6,
    sizePt: 8,
    bold: false,
    color: '#000000',
    align: 'left',
  },
] as CanvasObject[]

/**
 * 同样这两个对象在**后端草图**里的样子（`app.py::_version_sketch`）：
 * 没有 overrides、没有 nativeW/H、没有字号颜色，只有画得出来所需的那几个字段。
 */
const version: LayoutVersionMeta = {
  id: 'v1',
  name: '09-06 21:30',
  ts: 1_757_000_000_000,
  auto: true,
  description: '',
  objects: 2,
  page: PAGE,
  canvasId: 'c_t',
  canvasName: 'Figure 1',
  sketch: {
    page: PAGE,
    objects: [
      { type: 'panel', x: 4, y: 6, w: 40, h: 30, fileId: 'a.pdf', fileKind: 'pdf' },
      { type: 'text', x: 5, y: 50, w: 30, h: 6, text: '第一张版上的说明' },
    ],
  },
}

let container: HTMLDivElement
let root: Root

async function render(node: React.ReactNode) {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => {
    root.render(<TooltipProvider>{node}</TooltipProvider>)
  })
  await act(async () => {
    await Promise.resolve()
  })
  const svg = container.querySelector('[data-canvas-thumb]')
  expect(svg, '这一侧根本没画缩略图').not.toBeNull()
  return svg as SVGElement
}

async function teardown() {
  if (root) await act(async () => root.unmount())
  container?.remove()
}

beforeEach(async () => {
  document.body.innerHTML = ''
  localStorage.clear()
  vi.clearAllMocks()
  vi.mocked(fetchVersions).mockResolvedValue([version])
  vi.mocked(fetchVersionDoc).mockResolvedValue({ ...version, doc: emptyProject().canvases[0] as never })
  useAssetStore.setState({ byId: { 'a.pdf': { id: 'a.pdf', mtime: 7 } } } as never)
  useUiStore.setState({ versionsOpen: true })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_thumbpair')
  useDocumentStore.getState().commit(literal('准备'), (d) => {
    d.page = { ...d.page, ...PAGE }
    d.objects = docObjects
  })
})

afterEach(async () => {
  await teardown()
  useUiStore.setState({ versionsOpen: false })
})

describe('两处缩略图是同一份组件', () => {
  it('同样的内容，画布列表与版本列表画出来的 DOM 逐字相同', async () => {
    const fromCanvas = (await render(<CanvasList />)).outerHTML
    await teardown()
    const fromVersion = (await render(<VersionDrawer />)).outerHTML
    expect(fromVersion).toBe(fromCanvas)
  })

  it('面板挂的是素材库同一条预览链路（草图只带素材身份，URL 由组件拼）', async () => {
    const svg = await render(<VersionDrawer />)
    const href = svg.querySelector('[data-thumb-panel]')?.getAttribute('href') ?? ''
    expect(href).toContain('a.pdf')
    // mtime 来自素材库、不在草图里 —— 草图带上它才是把 /api/panels 的职责抄一份
    expect(href).toContain('m=7')
  })
})
