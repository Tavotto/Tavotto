/**
 * Panel placement must preserve fractions at the HTML → SVG boundary.
 *
 * Old WebKit can snap an SVG's layout paint origin before the canvas zoom. A
 * translated zero-origin panel avoids that early snap without changing the
 * document or screen-space selection math. These are DOM/geometry contracts;
 * jsdom cannot reproduce WebKit's painted pixels. The Safari A/B fixture is
 * the independent pixel check, not getBoundingClientRect in this suite.
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { literal } from '@/i18n'
import type { Manifest } from '@/lib/api'
import { toExportObjects } from '@/lib/exportPayload'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useMountedSvgStore } from '@/store/mountedSvgStore'
import { useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore, worldToMm } from '@/store/viewportStore'
import { seedExactRender } from '@/test/renderFixtures'
import { emptyProject, type PanelObject, type ShapeObject } from '@/types/document'
import { ObjectView } from './ObjectView'
import { OverlaySvg } from './OverlaySvg'

const BOX = [0.52, 0.82, 0.38, 0.09] as const
const MANIFEST: Manifest = {
  stem: 'Synthetic', size_mm: [86, 132],
  elements: [
    { gid: 'figure', role: 'figure', label: 'Figure', bbox: [0, 0, 1, 1], editable: [], draggable: false },
    { gid: 'axes_0', role: 'axes', label: 'Inset', bbox: [...BOX],
      editable: [{ prop: 'position', type: 'rect', value: [BOX[0], 1 - BOX[1] - BOX[3], BOX[2], BOX[3]] }],
      draggable: false, resizable: true },
  ],
}
const SVG = '<svg viewBox="0 0 86 132" width="100%" height="100%" preserveAspectRatio="none"><g id="axes_0"><rect x="44.72" y="108.24" width="32.68" height="11.88"/></g></svg>'

const panel = (extra: Partial<PanelObject> = {}): PanelObject => ({
  id: 'fractional-panel', type: 'panel', fileId: 'synthetic.pdf', fileKind: 'pdf',
  nativeW: 86, nativeH: 132, script: 'synthetic.py',
  x: worldToMm(185.95), y: worldToMm(39.3), w: 51.6, h: 79.2,
  overrides: [{ gid: 'axes_0', prop: 'position', value: [0.52, 0.09, 0.38, 0.09] }],
  ...extra,
})

let container: HTMLDivElement
let root: Root

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 200 })))
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  useInteractionStore.getState().end()
  useRenderStore.getState().clear()
  useMountedSvgStore.getState().set('fractional-panel', null)
  useUiStore.setState({ tool: 'select', editingTextId: null, cropTargetId: null, elementPanelId: null, selectedGids: [] })
  useSelectionStore.getState().set([])
  useViewportStore.setState({ zoom: 1, panX: 13.25, panY: -7.75, originX: 0, originY: 0, viewW: 1200, viewH: 900, spaceDown: false })
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.unstubAllGlobals()
})

async function mount(p: PanelObject, overlay = false) {
  await act(async () => {
    await useDocumentStore.getState().switchDocument(emptyProject(), 'fractional-origin-test')
    useDocumentStore.getState().commit(literal('fixture'), d => { d.objects.push(p) })
    useAssetStore.setState({ panels: [], byId: {} })
    seedExactRender(p, MANIFEST, { svg: SVG })
    useUiStore.setState({ elementPanelId: p.id, selectedGids: ['axes_0'] })
    root.render(<><ObjectView obj={p} />{overlay && <OverlaySvg />}</>)
  })
  return container.querySelector(`[data-object-id="${p.id}"]`) as HTMLDivElement
}

function translation(node: HTMLElement): [number, number] {
  const match = /^translate\(([-\d.e+]+)px, ([-\d.e+]+)px\)$/.exec(node.style.transform)
  expect(match, 'panel origin must be a fractional CSS translation').not.toBeNull()
  return [Number(match![1]), Number(match![2])]
}

describe('fractional panel origin', () => {
  const cases = [0, 0.4].flatMap(phase => [1, 2, 4, 8].flatMap(zoom => [1, 2].map(dpr => ({ phase, zoom, dpr }))))

  it.each(cases)('keeps overlay and translated content in one coordinate system: %j', async ({ phase, zoom, dpr }) => {
    vi.stubGlobal('devicePixelRatio', dpr)
    useViewportStore.setState({ zoom })
    const p = panel({ y: worldToMm(39.3 + phase) })
    const before = JSON.stringify(p)
    const node = await mount(p, true)
    expect(node.style.left).toBe('0px')
    expect(node.style.top).toBe('0px')
    const [x, y] = translation(node)
    expect(x).toBeCloseTo(mmToWorld(p.x), 12)
    expect(y).toBeCloseTo(mmToWorld(p.y), 12)
    expect(parseFloat(node.style.width)).toBeCloseTo(mmToWorld(p.w), 12)
    expect(parseFloat(node.style.height)).toBeCloseTo(mmToWorld(p.h), 12)

    // Actual overlay handles are the independent consumer of document geometry.
    // Test their unsnapped centers, not the intentionally pixel-snapped outline.
    const handle = container.querySelector('[data-element-handle="nw"]')!
    expect(handle).not.toBeNull()
    const hx = Number(handle.getAttribute('x')) + Number(handle.getAttribute('width')) / 2
    const hy = Number(handle.getAttribute('y')) + Number(handle.getAttribute('height')) / 2
    expect(hx * dpr).toBeCloseTo((13.25 + (x + BOX[0] * mmToWorld(p.w)) * zoom) * dpr, 9)
    expect(hy * dpr).toBeCloseTo((-7.75 + (y + BOX[1] * mmToWorld(p.h)) * zoom) * dpr, 9)
    expect(JSON.stringify(p)).toBe(before)
    expect(useDocumentStore.getState().doc.objects[0]).toEqual(p)
    expect(toExportObjects(useDocumentStore.getState().doc.objects)).toEqual(toExportObjects([p]))
    expect(container.querySelector('[data-authority="ready"]')).not.toBeNull()
  })

  const contentCases = ([0, 90, 180, 270] as const).flatMap(rotation => [false, true].flatMap(flipH => [false, true].map(flipV => ({ rotation, flipH, flipV }))))
  it.each(contentCases)('keeps quarter-turn/flip/crop inside the translated panel: %j', async transform => {
    const p = panel({ ...transform, crop: { x: 0.1, y: 0.15, w: 0.7, h: 0.65 }, opacity: 0.8 })
    const before = JSON.stringify(p)
    const exported = toExportObjects([p])
    const node = await mount(p)
    expect(node.style.left).toBe('0px')
    expect(node.style.top).toBe('0px')
    expect(translation(node)).toEqual([mmToWorld(p.x), mmToWorld(p.y)])
    const svgWrap = container.querySelector('[data-element-svg]') as HTMLDivElement
    const content = svgWrap.parentElement!
    const swapped = transform.rotation === 90 || transform.rotation === 270
    const cw = mmToWorld(swapped ? p.h : p.w), ch = mmToWorld(swapped ? p.w : p.h)
    expect(parseFloat(content.style.width)).toBeCloseTo(cw, 12)
    expect(parseFloat(content.style.height)).toBeCloseTo(ch, 12)
    expect(parseFloat(svgWrap.style.width)).toBeCloseTo(cw / 0.7, 12)
    expect(parseFloat(svgWrap.style.height)).toBeCloseTo(ch / 0.65, 12)
    expect(parseFloat(svgWrap.style.left)).toBeCloseTo(-0.1 / 0.7 * cw, 12)
    expect(parseFloat(svgWrap.style.top)).toBeCloseTo(-0.15 / 0.65 * ch, 12)
    const inner = [transform.rotation ? `rotate(${transform.rotation}deg)` : '', transform.flipH || transform.flipV ? `scale(${transform.flipH ? -1 : 1}, ${transform.flipV ? -1 : 1})` : ''].filter(Boolean).join(' ')
    expect(content.style.transform).toBe(inner)
    expect(content.style.opacity).toBe('0.8')
    expect(JSON.stringify(p)).toBe(before)
    expect(toExportObjects(useDocumentStore.getState().doc.objects)).toEqual(exported)
  })

  it('preserves negative positions and locking without adding a wrapper', async () => {
    const p = panel({ x: -12.345, y: -6.789, locked: true })
    const node = await mount(p)
    expect(node.style.left).toBe('0px')
    expect(node.style.top).toBe('0px')
    expect(translation(node)).toEqual([mmToWorld(p.x), mmToWorld(p.y)])
    expect(node.style.pointerEvents).toBe('none')
    expect(node.parentElement).toBe(container)
  })

  it('leaves non-panel rotation and positioning unchanged', async () => {
    const shape: ShapeObject = { id: 'rotated-shape', type: 'shape', shape: 'rect', x: 12.345, y: 6.789, w: 30, h: 20, strokePt: 1, color: '#111', fill: null, rotationDeg: 37 }
    await act(async () => root.render(<ObjectView obj={shape} />))
    const node = container.querySelector('[data-object-id="rotated-shape"]') as HTMLDivElement
    expect(parseFloat(node.style.left)).toBeCloseTo(mmToWorld(shape.x), 12)
    expect(parseFloat(node.style.top)).toBeCloseTo(mmToWorld(shape.y), 12)
    expect(node.style.transform).toBe('rotate(37deg)')
  })
})
