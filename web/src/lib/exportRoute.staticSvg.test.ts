import { describe, expect, it } from 'vitest'
import vectors from '../../../tests/golden/static_svg_route_vectors.json'
import { panelRouteOf, unavailablePanels, type ExportRoute, type PanelRoute } from './exportRoute'

describe('static SVG routes are backend facts', () => {
  it.each(vectors.vectors)('$name', ({ args, expected }) => {
    const scope = args.scope as 'canvas' | 'original'
    const route: ExportRoute = { scope, formats: args.formats, panels: { 'ordinary.svg': expected as PanelRoute } }
    expect(panelRouteOf(route, scope, 'ordinary.svg', args.formats)).toEqual(expected)
    expect(unavailablePanels(route, args.formats)).toEqual(scope === 'canvas' && expected.source === 'unavailable' ? ['ordinary.svg'] : [])
    expect(panelRouteOf(route, scope === 'canvas' ? 'original' : 'canvas', 'ordinary.svg', args.formats)).toBeNull()
    expect(panelRouteOf(route, scope, 'ordinary.svg', ['unknown-format'])).toBeNull()
    const ambiguous = { ...route, panels: { ...route.panels, 'ordinary.svg#2': expected as PanelRoute } }
    expect(panelRouteOf(ambiguous, scope, 'ordinary.svg', args.formats)).toBeNull()
  })
})
