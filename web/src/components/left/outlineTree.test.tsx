/**
 * 大纲（2026-10 界面审计 A3）：画布 → 图 → 图内元素，一棵树。
 *
 * 钉几件事：
 * 1. 结构：顶层是全部画布，只有当前画布展开（里面是图层树）；点别的画布 = 打开它；
 * 2. 同步：点大纲里的图行与点画布上的图是同一份选区（`selectionStore`），谁改了另一边都跟着；
 * 3. 图内元素不在大纲里（独立的「图内」抽屉）；
 * 4. 左轨 6 项 + 设置，没有点开是弹窗的「状态」。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { literal } from '@/i18n'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type CanvasObject, type PanelObject } from '@/types/document'
import { LeftRail } from './LeftRail'
import { OutlineTree } from './OutlineTree'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
Element.prototype.scrollIntoView ??= function scrollIntoView() {}

const panel = (id: string, fileId: string, script?: string): PanelObject =>
  ({
    id,
    type: 'panel',
    fileId,
    fileKind: 'pdf',
    nativeW: 80,
    nativeH: 60,
    overrides: [],
    x: id === 'p1' ? 0 : 90,
    y: 0,
    w: 80,
    h: 60,
    script,
  }) as PanelObject

let host: HTMLDivElement
let root: Root

async function mount(node: React.ReactNode) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<TooltipProvider>{node}</TooltipProvider>)
  })
  await act(async () => {})
}

const canvasRows = () => [...host.querySelectorAll<HTMLElement>('[data-canvas-row]')]
const layerRow = (id: string) => host.querySelector<HTMLElement>(`[data-layer="${id}"]`)
const levelOf = (li: Element | null) => Number(li?.getAttribute('aria-level'))

beforeEach(async () => {
  document.body.innerHTML = ''
  localStorage.clear()
  useUiStore.setState({ leftTab: 'outline', leftOpen: true, selectedGids: [], elementPanelId: null })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_outline')
  useDocumentStore.getState().commit(literal('准备'), (d) => {
    d.objects = [panel('p1', 'Fig1.pdf', 'fig1.py'), panel('p2', 'Fig2.pdf')] as CanvasObject[]
  })
  const first = useDocumentStore.getState().activeCanvasId
  useDocumentStore.getState().addCanvas()
  useDocumentStore.getState().switchCanvas(first)
  useAssetStore.setState({
    byId: { 'Fig1.pdf': { id: 'Fig1.pdf', mtime: 1 }, 'Fig2.pdf': { id: 'Fig2.pdf', mtime: 1 } },
  } as never)
  useSelectionStore.getState().clear()
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

describe('结构', () => {
  it('顶层是全部画布，只有当前画布展开，里面是图层树', async () => {
    await mount(<OutlineTree />)
    expect(canvasRows()).toHaveLength(2)
    expect(host.querySelector('[data-tree="outline"]')).not.toBeNull()
    expect(host.querySelectorAll('[data-tree="layers"]')).toHaveLength(1)
    const active = canvasRows().find((r) => r.getAttribute('aria-expanded') === 'true')!
    expect(active.getAttribute('aria-current')).toBe('true')
    expect(canvasRows().filter((r) => r.getAttribute('aria-expanded') === 'true')).toHaveLength(1)
    // 层级：画布 1 · 图 2
    expect(levelOf(active)).toBe(1)
    expect(levelOf(layerRow('p1'))).toBe(2)
  })

  it('点别的画布 = 打开它，展开的换成它', async () => {
    await mount(<OutlineTree />)
    const other = canvasRows().find((r) => r.getAttribute('aria-current') !== 'true')!
    await act(async () => other.click())
    expect(useDocumentStore.getState().activeCanvasId).toBe(other.dataset.canvasRow)
    expect(
      canvasRows().find((r) => r.getAttribute('aria-expanded') === 'true')?.dataset.canvasRow,
    ).toBe(other.dataset.canvasRow)
  })

  it('当前画布可以折叠 / 展开（箭头）', async () => {
    await mount(<OutlineTree />)
    const active = canvasRows().find((r) => r.getAttribute('aria-current') === 'true')!
    const chevron = active.querySelector<HTMLButtonElement>('button')!
    await act(async () => chevron.click())
    expect(active.getAttribute('aria-expanded')).toBe('false')
    expect(host.querySelector('[data-tree="layers"]')).toBeNull()
    await act(async () => chevron.click())
    expect(host.querySelector('[data-tree="layers"]')).not.toBeNull()
  })
})

describe('选中两边同步', () => {
  it('画布上选中一张图 → 大纲里那一行亮；点大纲里的行 → 选区跟着变', async () => {
    await mount(<OutlineTree />)
    await act(async () => useSelectionStore.getState().set(['p2']))
    expect(layerRow('p2')!.getAttribute('aria-selected')).toBe('true')
    expect(layerRow('p1')!.getAttribute('aria-selected')).toBe('false')
    await act(async () => {
      layerRow('p1')!.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }))
    })
    expect(useSelectionStore.getState().ids).toEqual(['p1'])
  })
})

describe('图内元素不挂在大纲里', () => {
  it('选中可编辑的图：大纲里没有元素树（图内元素是左轨独立的「图内」抽屉）', async () => {
    await mount(<OutlineTree />)
    await act(async () => useSelectionStore.getState().set(['p1']))
    expect(host.querySelector('[data-tree="elements"]')).toBeNull()
  })
})

describe('左轨', () => {
  it('6 项 + 设置：画布与图层合成「大纲」，「图内」独立，没有点开是弹窗的状态入口', async () => {
    await mount(<LeftRail />)
    const rails = [...host.querySelectorAll<HTMLElement>('[data-rail]')].map((b) => b.dataset.rail)
    expect(rails).toEqual(['workspace', 'assets', 'outline', 'elements', 'style', 'problems', 'settings'])
  })
})
