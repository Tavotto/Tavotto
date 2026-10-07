import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ElementTree } from '@/components/left/ElementTree'
import { LayerTree } from '@/components/left/LayerTree'
import { TooltipProvider } from '@/components/ui/Tooltip'
import type { Manifest } from '@/lib/api'
import { useDocumentStore } from '@/store/documentStore'
import { useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore, type WorkspaceLayout } from '@/store/uiStore'
import { seedExactRender } from '@/test/renderFixtures'
import { emptyProject, type PanelObject } from '@/types/document'
import { useSelectionRouting } from './useSelectionRouting'

globalThis.IS_REACT_ACT_ENVIRONMENT = true
Element.prototype.scrollIntoView ??= function () {}

const panel: PanelObject = {
  id: 'p1', type: 'panel', fileId: 'Synthetic.pdf', fileKind: 'pdf', script: 'synthetic.py',
  x: 0, y: 0, w: 80, h: 60, nativeW: 80, nativeH: 60, overrides: [],
}
const gids = ['axes_0.lines_0', 'axes_0.collections_0', 'axes_0.title']
const manifest = {
  stem: 'Synthetic', size_mm: [80, 60],
  elements: [
    ['figure', 'figure'], ['axes_0', 'axes'],
    [gids[0], 'line'], [gids[1], 'scatter'], [gids[2], 'title'],
  ].map(([gid, role]) => ({ gid, role, label: gid, bbox: [0.1, 0.1, 0.3, 0.3],
    draggable: true, editable: [] })),
} as unknown as Manifest

function Harness() {
  useSelectionRouting()
  const leftOpen = useUiStore(s => s.leftOpen)
  const tab = useUiStore(s => s.leftTab)
  return <TooltipProvider>
    <input data-outside />
    {leftOpen && <aside data-left-drawer>
      {tab === 'layers' ? <LayerTree /> : <ElementTree />}
      <button data-explicit onClick={() => useUiStore.getState().setRightTab('properties')} />
    </aside>}
    <div data-canvas-stage><span data-canvas-hit onPointerDown={() => {
      useSelectionStore.getState().set([panel.id])
      if (useUiStore.getState().elementPanelId !== panel.id) {
        useUiStore.getState().setElementPanel(panel.id)
      }
      useUiStore.getState().setSelectedGid(gids[0])
    }} /></div>
  </TooltipProvider>
}

let host: HTMLDivElement
let root: Root
beforeEach(async () => {
  localStorage.clear()
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), null)
  useDocumentStore.setState(s => ({ doc: { ...s.doc, objects: [panel] } }))
  seedExactRender(panel, manifest)
  useSelectionStore.getState().clear()
  useUiStore.setState({ layout: 'medium', leftOpen: true, leftTab: 'layers',
    rightOpen: false, rightTab: 'properties', rightPinned: false,
    elementPanelId: null, selectedGids: [] })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<Harness />))
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
})

function node(selector: string): HTMLElement {
  const el = host.querySelector<HTMLElement>(selector)
  if (!el) throw new Error(`Missing test target: ${selector}`)
  return el
}
function pointer(selector: string, modifiers: PointerEventInit = {}) {
  act(() => node(selector).dispatchEvent(new PointerEvent('pointerdown', {
    bubbles: true, button: 0, ...modifiers,
  })))
}
function treeRow(gid: string) { return `[data-el="${gid}"]` }
function openElements(layout: WorkspaceLayout = 'medium') {
  act(() => {
    useSelectionStore.getState().set([panel.id])
    useUiStore.getState().setLayout(layout)
  })
  act(() => useUiStore.getState().setLeftTab('elements'))
  node('[data-outside]').focus()
}
function expectTreeSelection(gids: string[]) {
  expect(useUiStore.getState().selectedGids).toEqual(gids)
  expect(useUiStore.getState().leftOpen).toBe(true)
  expect(useUiStore.getState().rightOpen).toBe(false)
}

describe('selection routing keeps the active drawer workflow', () => {
  it('first object selection from the layer tree keeps the medium drawer', () => {
    node('[data-outside]').focus()
    pointer('[data-layer="p1"]')
    expect(useSelectionStore.getState().ids).toEqual(['p1'])
    expect(useUiStore.getState().leftOpen).toBe(true)
    expect(useUiStore.getState().rightOpen).toBe(false)
  })

  it.each(['shiftKey', 'ctrlKey', 'metaKey'] as const)('layer tree %s preserves additive selection', modifier => {
    act(() => useDocumentStore.setState(s => ({
      doc: { ...s.doc, objects: [panel, { ...panel, id: 'p2', x: 90 }] },
    })))
    pointer('[data-layer="p1"]')
    pointer('[data-layer="p2"]', { [modifier]: true })
    expect(useSelectionStore.getState().ids).toEqual(['p1', 'p2'])
    pointer('[data-layer="p1"]', { [modifier]: true })
    expect(useSelectionStore.getState().ids).toEqual(['p2'])
    expect(useUiStore.getState().leftOpen).toBe(true)
  })

  for (const layout of ['medium', 'narrow'] as const) {
    for (const gid of gids) {
      it(`${layout}: pointerdown selects ${gid} while external focus is stale`, () => {
        openElements(layout)
        pointer(treeRow(gid), { pointerType: layout === 'narrow' ? 'touch' : 'mouse' })
        expectTreeSelection([gid])
      })
    }
  }

  it('keyboard focus and arrow navigation preserve the tree', () => {
    openElements()
    act(() => node(treeRow('figure')).focus())
    expectTreeSelection(['figure'])
    act(() => node(treeRow('figure')).dispatchEvent(new KeyboardEvent('keydown', {
      bubbles: true, key: 'ArrowDown',
    })))
    expectTreeSelection(['axes_0'])
  })

  it.each(['shiftKey', 'ctrlKey', 'metaKey'] as const)('%s adds and removes without focus reselecting', modifier => {
    openElements()
    pointer(treeRow(gids[0]))
    pointer(treeRow(gids[1]), { [modifier]: true })
    expectTreeSelection([gids[0], gids[1]])
    pointer(treeRow(gids[0]), { [modifier]: true })
    expectTreeSelection([gids[1]])
  })

  it('clicking the same canvas selection opens properties despite tree focus', () => {
    openElements()
    act(() => {
      useUiStore.getState().setElementPanel(panel.id)
      useUiStore.getState().setSelectedGid(gids[0])
    })
    act(() => useUiStore.getState().setLeftTab('elements'))
    act(() => node(treeRow(gids[0])).focus())
    pointer('[data-canvas-hit]')
    expect(useUiStore.getState().selectedGids).toEqual([gids[0]])
    expect(useUiStore.getState().rightOpen).toBe(true)
    expect(useUiStore.getState().leftOpen).toBe(false)
  })

  it('wide windows show properties beside the tree', () => {
    openElements('wide')
    pointer(treeRow(gids[0]))
    expect(useUiStore.getState().leftOpen).toBe(true)
    expect(useUiStore.getState().rightOpen).toBe(true)
  })

  it('explicit properties editing still takes precedence', () => {
    openElements()
    pointer(treeRow(gids[0]))
    act(() => node('[data-explicit]').click())
    expect(useUiStore.getState().rightOpen).toBe(true)
    expect(useUiStore.getState().leftOpen).toBe(false)
  })

  it('clearing selection hides unpinned properties', () => {
    pointer('[data-canvas-hit]')
    expect(useUiStore.getState().rightOpen).toBe(true)
    act(() => {
      useSelectionStore.getState().clear()
      useUiStore.getState().setElementPanel(null)
    })
    expect(useUiStore.getState().rightOpen).toBe(false)
  })
})
