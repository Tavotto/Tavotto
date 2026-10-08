import { act } from 'react'
import { flushSync } from 'react-dom'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { trackPointer } from '@/canvas/interactions'
import { ElementTree } from '@/components/left/ElementTree'
import { LayerTree } from '@/components/left/LayerTree'
import { TooltipProvider } from '@/components/ui/Tooltip'
import type { Manifest } from '@/lib/api'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore, type WorkspaceLayout } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'
import { seedExactRender } from '@/test/renderFixtures'
import { emptyProject, type PanelObject } from '@/types/document'
import { useKeyboard } from './useKeyboard'
import { useSelectionRouting } from './useSelectionRouting'

globalThis.IS_REACT_ACT_ENVIRONMENT = true
Element.prototype.scrollIntoView ??= function () {}

const panel: PanelObject = {
  id: 'p1', type: 'panel', fileId: 'Synthetic.pdf', fileKind: 'pdf', script: 'synthetic.py',
  x: 0, y: 0, w: 80, h: 60, nativeW: 80, nativeH: 60, overrides: [],
}
const gids = ['axes_0.lines_0', 'axes_0.scatter_0', 'axes_0.title']
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
    <button data-scrim onClick={() => {
      const ui = useUiStore.getState()
      if (ui.leftOpen) ui.toggleLeft()
      if (ui.rightOpen) ui.toggleRight()
    }} />
    {leftOpen && <aside data-left-drawer>
      {tab === 'assets'
        ? <button data-asset onPointerDown={() => useSelectionStore.getState().set([panel.id])} />
        : tab === 'layers' ? <LayerTree /> : <ElementTree />}
      <button data-explicit onClick={() => useUiStore.getState().setRightTab('properties')} />
    </aside>}
    <div data-canvas-stage><span data-canvas-hit onPointerDown={() => {
      useSelectionStore.getState().set([panel.id])
      if (useUiStore.getState().elementPanelId !== panel.id) {
        useUiStore.getState().setElementPanel(panel.id)
      }
      useUiStore.getState().setSelectedGid(gids[0])
    }} /><span data-canvas-same /><span data-canvas-drag
      onPointerDown={() => useInteractionStore.getState().begin('element')} />
      <span data-canvas-track onPointerDown={e => {
        useInteractionStore.getState().begin('element')
        trackPointer(e, {
          onMove: () => {},
          onEnd: () => useInteractionStore.getState().end(),
        })
      }} /></div>
  </TooltipProvider>
}

function KeyboardHarness() {
  useKeyboard()
  return <Harness />
}

let host: HTMLDivElement
let root: Root
beforeEach(async () => {
  localStorage.clear()
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'tree-routing-test')
  useDocumentStore.setState(s => ({ doc: { ...s.doc, objects: [panel] } }))
  seedExactRender(panel, manifest)
  useSelectionStore.getState().clear()
  useInteractionStore.getState().end()
  useViewportStore.setState({ spaceDown: false })
  useUiStore.setState({ layout: 'medium', leftOpen: true, leftTab: 'layers',
    rightOpen: false, rightTab: 'properties', rightPinned: false,
    elementPanelId: null, selectedGids: [], tool: 'select' })
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
  act(() => document.dispatchEvent(new PointerEvent('pointerup', {
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
    it.each([false, true])(`${layout}: asset insertion yields to properties (existing selection: %s)`, existing => {
      if (existing) act(() => {
        useDocumentStore.setState(s => ({ doc: { ...s.doc, objects: [panel, { ...panel, id: 'p2' }] } }))
        useSelectionStore.getState().set(['p2'])
      })
      act(() => {
        useUiStore.getState().setLayout(layout)
        useUiStore.getState().setLeftTab('assets')
      })
      pointer('[data-asset]')
      expect(useSelectionStore.getState().ids).toEqual(['p1'])
      expect(useUiStore.getState().leftOpen).toBe(false)
      expect(useUiStore.getState().rightOpen).toBe(true)
      expect(useUiStore.getState().rightTab).toBe('properties')
    })
  }

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

  it.each(['medium', 'narrow'] as const)('%s canvas reselection preserves manually closed properties', layout => {
    act(() => {
      useUiStore.getState().setLayout(layout)
      useUiStore.setState({ leftOpen: false })
      useSelectionStore.getState().set([panel.id])
    })
    expect(useUiStore.getState().rightOpen).toBe(true)
    act(() => useUiStore.getState().toggleRight())
    pointer('[data-canvas-same]')
    expect(useSelectionStore.getState().ids).toEqual([panel.id])
    expect(useUiStore.getState().rightOpen).toBe(false)
    expect(useUiStore.getState().leftOpen).toBe(false)
  })

  it.each(['medium', 'narrow'] as const)('%s tree handoff is consumed before properties are manually closed', layout => {
    openElements(layout)
    pointer(treeRow(gids[0]))
    pointer('[data-canvas-hit]')
    expect(useUiStore.getState().rightOpen).toBe(true)
    act(() => useUiStore.getState().toggleRight())
    pointer('[data-canvas-same]')
    expect(useUiStore.getState().selectedGids).toEqual([gids[0]])
    expect(useUiStore.getState().rightOpen).toBe(false)
    expect(useUiStore.getState().leftOpen).toBe(false)
  })

  it('dismissing the narrow tree overlay still permits a canvas handoff', () => {
    openElements('narrow')
    pointer(treeRow(gids[0]))
    pointer('[data-scrim]')
    act(() => node('[data-scrim]').click())
    expect(useUiStore.getState().leftOpen).toBe(false)
    pointer('[data-canvas-hit]')
    expect(useUiStore.getState().rightOpen).toBe(true)
  })

  it('dismissing explicit narrow properties cancels the previous tree handoff', () => {
    openElements('narrow')
    pointer(treeRow(gids[0]))
    pointer('[data-explicit]')
    act(() => node('[data-explicit]').click())
    expect(useUiStore.getState().rightOpen).toBe(true)
    pointer('[data-scrim]')
    act(() => node('[data-scrim]').click())
    pointer('[data-canvas-same]')
    expect(useUiStore.getState().selectedGids).toEqual([gids[0]])
    expect(useUiStore.getState().rightOpen).toBe(false)
    expect(useUiStore.getState().leftOpen).toBe(false)
  })

  it('native right-sidebar dismissal cancels the wide tree handoff without a DOM event', () => {
    openElements('wide')
    pointer(treeRow(gids[0]))
    expect(useUiStore.getState().rightOpen).toBe(true)
    // menu-toggle-right invokes this store action directly from the native menu.
    act(() => useUiStore.getState().toggleRight())
    pointer('[data-canvas-hit]')
    expect(useUiStore.getState().selectedGids).toEqual([gids[0]])
    expect(useUiStore.getState().leftOpen).toBe(true)
    expect(useUiStore.getState().rightOpen).toBe(false)
  })

  it('native right-sidebar dismissal cancels a pending wide gesture handoff', () => {
    openElements('wide')
    pointer(treeRow(gids[0]))
    act(() => useInteractionStore.getState().begin('element'))
    pointer('[data-canvas-hit]')
    act(() => useUiStore.getState().toggleRight())
    act(() => useInteractionStore.getState().end())
    expect(useUiStore.getState().selectedGids).toEqual([gids[0]])
    expect(useUiStore.getState().leftOpen).toBe(true)
    expect(useUiStore.getState().rightOpen).toBe(false)
  })

  it.each(['pan', 'draw'] as const)('%s gesture leaves the tree workflow until selection changes', gesture => {
    openElements()
    pointer(treeRow(gids[0]))
    act(() => {
      if (gesture === 'pan') useViewportStore.setState({ spaceDown: true })
      else useUiStore.getState().setTool('line')
    })
    pointer('[data-canvas-hit]')
    expectTreeSelection([gids[0]])
  })

  it('keeps the tree when capture flushes before the target starts tracking', () => {
    openElements()
    pointer(treeRow(gids[0]))
    const captured: Array<{ kind: string; leftOpen: boolean; rightOpen: boolean }> = []
    const observeCapture = (e: Event) => {
      if (e.target !== node('[data-canvas-drag]')) return
      // Flush the capture request before React's target handler has begun tracking.
      flushSync(() => root.render(<Harness />))
      const ui = useUiStore.getState()
      captured.push({ kind: useInteractionStore.getState().kind, leftOpen: ui.leftOpen, rightOpen: ui.rightOpen })
    }
    document.addEventListener('pointerdown', observeCapture, true)
    try {
      act(() => node('[data-canvas-drag]').dispatchEvent(new PointerEvent('pointerdown', {
        bubbles: true, button: 0, pointerId: 9,
      })))
    } finally {
      document.removeEventListener('pointerdown', observeCapture, true)
    }
    expect(captured).toEqual([{ kind: 'none', leftOpen: true, rightOpen: false }])
    expect(useInteractionStore.getState().kind).toBe('element')
    expectTreeSelection([gids[0]])
    act(() => document.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerId: 9 })))
    expectTreeSelection([gids[0]])
    act(() => useInteractionStore.getState().end())
    expect(useUiStore.getState().rightOpen).toBe(true)
    expect(useUiStore.getState().leftOpen).toBe(false)
  })

  it('cancelling an unreleased canvas pointer preserves the next tree handoff', () => {
    openElements()
    pointer(treeRow(gids[0]))
    act(() => node('[data-canvas-same]').dispatchEvent(new PointerEvent('pointerdown', {
      bubbles: true, button: 0, pointerId: 9,
    })))
    expectTreeSelection([gids[0]])
    act(() => document.dispatchEvent(new PointerEvent('pointercancel', { bubbles: true, pointerId: 9 })))
    expectTreeSelection([gids[0]])
    pointer('[data-canvas-same]')
    act(() => document.dispatchEvent(new PointerEvent('pointerup', { bubbles: true })))
    expect(useUiStore.getState().rightOpen).toBe(true)
    expect(useUiStore.getState().leftOpen).toBe(false)
  })

  it('native right-sidebar dismissal cancels an unreleased wide canvas pointer', () => {
    openElements('wide')
    pointer(treeRow(gids[0]))
    act(() => node('[data-canvas-same]').dispatchEvent(new PointerEvent('pointerdown', {
      bubbles: true, button: 0, pointerId: 9,
    })))
    act(() => useUiStore.getState().toggleRight())
    act(() => document.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerId: 9 })))
    expect(useUiStore.getState().rightOpen).toBe(false)
    expect(useUiStore.getState().leftOpen).toBe(true)
  })

  it.each(['medium', 'narrow'] as const)('%s: Escape cancels the held handoff without a pointercancel event', layout => {
    act(() => root.render(<KeyboardHarness />))
    openElements(layout)
    pointer(treeRow(gids[0]))
    act(() => node('[data-canvas-track]').dispatchEvent(new PointerEvent('pointerdown', {
      bubbles: true, button: 0, pointerId: 9,
    })))
    try {
      expect(useInteractionStore.getState().kind).toBe('element')
      expectTreeSelection([gids[0]])
      act(() => window.dispatchEvent(new KeyboardEvent('keydown', {
        key: 'Escape', bubbles: true, cancelable: true,
      })))
      expect(useInteractionStore.getState().kind).toBe('none')
      expectTreeSelection([gids[0]])
    } finally {
      act(() => document.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerId: 9 })))
    }
    expectTreeSelection([gids[0]])
    pointer('[data-canvas-same]')
    expect(useUiStore.getState().rightOpen).toBe(true)
    expect(useUiStore.getState().leftOpen).toBe(false)
  })

  it.each(['move', 'resize', 'element'] as const)('%s defers sidebar routing until tracking ends', kind => {
    openElements()
    pointer(treeRow(gids[0]))
    act(() => useInteractionStore.getState().begin(kind))
    pointer('[data-canvas-hit]')
    expectTreeSelection([gids[0]])
    act(() => useInteractionStore.getState().end())
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
