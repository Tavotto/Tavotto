/**
 * 「适应」在快速编辑里框的是**那张图本身的矩形**（含原点），不是 (0,0) 到它右下角（Codex #833 P2）。
 *
 * 主语：视口补间的**终点**（`zoom / panX / panY`，减弱动效下补间一拍到位），量的是那张图此刻落在屏幕上的
 * 中心与比例——不是「调了哪个函数」。面板放在正偏移 (10,20) 与负偏移 (-15,-10) 两处：旧的右下角取景在前者
 * 左上多出空白（中心偏右下），在后者把图裁在视野外（中心偏左上），两者都会让中心断言红。
 * 入口逐个走：⌘1（keydown）、系统菜单「适应」（`runMenuAction`）、舞台双击（真实 dblclick 落在框外）、
 * 命令面板 / 缩放菜单 / 画布菜单都是同一个 `fitStage`（命令面板另有 `CommandPalette.test.tsx` 看护）。
 * 对照组：排版里同样的入口照旧框页面。
 */
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { CanvasStage } from './CanvasStage'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { runMenuAction } from '@/hooks/menuActions'
import { useKeyboard } from '@/hooks/useKeyboard'
import type { PanelInfo } from '@/lib/api'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useProjectStore } from '@/store/projectStore'
import { useSelectionStore } from '@/store/selectionStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { useWorkspaceStore } from '@/store/workspace'
import { fitStage } from '@/store/zoomToSelection'
import { emptyProject, type PanelObject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = NoopResizeObserver as unknown as typeof ResizeObserver

const VIEW_W = 800
const VIEW_H = 600
const PAD = 72

const info: PanelInfo = {
  id: 'a.pdf', name: 'a', folder: '.', kind: 'pdf', native_w_mm: 80, native_h_mm: 60, mtime: 1,
}

let root: Root | null = null
let host: HTMLDivElement | null = null

function Harness() {
  useKeyboard()
  return createElement(TooltipProvider, null, createElement(CanvasStage))
}

async function setup(rect: { x: number; y: number; w: number; h: number }) {
  useAssetStore.setState({ panels: [info], byId: { 'a.pdf': info } })
  await useDocumentStore.getState().switchDocument(emptyProject(), `d_stage_fit_${rect.x}_${rect.y}`)
  const panel: PanelObject = {
    id: 'p1', type: 'panel', fileId: 'a.pdf', fileKind: 'pdf', nativeW: 80, nativeH: 60, overrides: [], ...rect,
  }
  useDocumentStore.getState().silent((d) => {
    d.objects.push(panel)
  })
  useSelectionStore.setState({ ids: [] })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  act(() => root!.render(createElement(Harness)))
}

/**
 * jsdom 量不出尺寸（舞台每次切模式重报一次 0）：在模式定下来之后手动报一个确定的舞台，再把视口挪到
 * 一个与任何取景都无关的落点——下一次「适应」的终点才是它自己算出来的
 */
const scramble = () =>
  act(() => {
    const vp = useViewportStore.getState()
    vp.setViewRect({ left: 0, top: 0, width: VIEW_W, height: VIEW_H })
    vp.setFitBottomClear(0)
    vp.setView({ zoom: 0.5, panX: 3, panY: 7 })
  })

/** 视口终点下，一块 mm 矩形在舞台上的中心与比例；与期望的「刚好装下、居中」逐项比 */
function expectFramed(rect: { x: number; y: number; w: number; h: number }) {
  const { zoom, panX, panY } = useViewportStore.getState()
  const wPx = mmToWorld(rect.w)
  const hPx = mmToWorld(rect.h)
  const want = Math.min((VIEW_W - PAD) / wPx, (VIEW_H - PAD) / hPx)
  expect(zoom).toBeCloseTo(want, 6)
  expect(panX + mmToWorld(rect.x + rect.w / 2) * zoom).toBeCloseTo(VIEW_W / 2, 4)
  expect(panY + mmToWorld(rect.y + rect.h / 2) * zoom).toBeCloseTo(VIEW_H / 2, 4)
}

const entries: [string, () => void][] = [
  [
    '⌘1',
    () =>
      act(() => {
        window.dispatchEvent(new KeyboardEvent('keydown', { key: '1', ctrlKey: true, bubbles: true, cancelable: true }))
      }),
  ],
  ['系统菜单「适应」', () => act(() => runMenuAction('menu-zoom-fit'))],
  ['fitStage（命令面板 / 缩放菜单 / 画布菜单 / 工具条）', () => act(() => fitStage())],
  [
    '舞台双击（落在取景框外的灰色工作区）',
    () => {
      const stage = document.querySelector<HTMLElement>('[data-canvas-stage]')!
      // 视口右下角那一像素：scramble 之后（50%、几乎不平移）它在任何一块框的右下外侧
      act(() => {
        stage.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, clientX: VIEW_W - 1, clientY: VIEW_H - 1 }))
      })
    },
  ],
]

beforeEach(() => {
  // 减弱动效：补间一拍到位，量的就是终点
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: q.includes('reduce'),
    media: q,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    onchange: null,
    dispatchEvent: () => false,
  }))
  useWorkspaceStore.getState().clear()
  // 系统菜单只在项目打开着时放行画布动作
  useProjectStore.setState({ phase: 'open' } as never)
})

afterEach(() => {
  act(() => root?.unmount())
  root = null
  host?.remove()
  host = null
  useWorkspaceStore.getState().clear()
  useProjectStore.setState({ phase: 'none' } as never)
  vi.unstubAllGlobals()
})

describe.each([
  ['正偏移', { x: 10, y: 20, w: 40, h: 30 }],
  ['负偏移（拖出页面左上角）', { x: -15, y: -10, w: 40, h: 30 }],
])('快速编辑里「适应」框那张图本身（%s）', (_name, rect) => {
  it.each(entries)('%s', async (_entry, run) => {
    await setup(rect)
    act(() => useWorkspaceStore.getState().enterFastEdit('p1'))
    scramble()
    run()
    expectFramed(rect)
  })
})

describe('排版里「适应」照旧框页面（对照组）', () => {
  it.each(entries)('%s', async (_entry, run) => {
    await setup({ x: -15, y: -10, w: 40, h: 30 })
    const page = useDocumentStore.getState().doc.page
    scramble()
    run()
    expectFramed({ x: 0, y: 0, w: page.w, h: page.h })
  })
})
