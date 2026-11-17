import { afterEach, describe, expect, it } from 'vitest'
import { epsAvailability } from '@/lib/exportRequest'
import { embedsDiskBitmap } from '@/lib/exportRoute'
import { fixRoute } from '@/lib/issueFix'
import type { ValidationIssue } from '@/lib/validation'
import { setCurrentProjectId } from '@/lib/session'
import { emptyProject, type FigureDocument, type FigureOrigin, type PanelObject } from '@/types/document'
import { isJustBakedBaseline } from './actions'
import { useAssetStore } from './assetStore'
import { isNativePanelIssue } from './issueFixActions'

const origin: FigureOrigin = {
  version: 1,
  snapshot: {
    version: 1, sourceId: 'History.pdf', patchHash: 'P', receiptId: 'A', snapshotId: 'archive-A',
    executionOwner: { script: 'plot.py', run_config: null },
  },
  patches: [{ gid: 'line', prop: 'linewidth', value: 2 }],
}
const q = [{ gid: 'line', prop: 'linewidth', value: 12 }]
const panel = (withOrigin = true): PanelObject => ({
  id: 'p', type: 'panel', fileId: 'History.pdf', fileKind: 'pdf', x: 0, y: 0, w: 80, h: 60,
  nativeW: 80, nativeH: 60, figureFrame: 1, script: null, overrides: structuredClone(q),
  ...(withOrigin ? { figureOrigin: structuredClone(origin) } : {}),
})
const issue = { ruleCode: 'legend-frame', objectRef: { objectId: 'p', gid: 'legend' } } as unknown as ValidationIssue
const doc = (value: PanelObject) => ({ ...emptyProject().canvases[0], objects: [value] }) as unknown as FigureDocument

afterEach(() => { useAssetStore.setState({ byId: {} }); setCurrentProjectId(null) })

describe('historical sources at the entry points', () => {
  it('routes engine fixes by the historical owner, not the missing current script', () => {
    expect(fixRoute(issue, doc(panel()))).toBe('engine')
    // Without an explicit origin a scriptless panel has no engine authority.
    expect(fixRoute(issue, doc(panel(false)))).toBeNull()
    // A current script still routes as before.
    expect(fixRoute(issue, doc({ ...panel(false), script: 'plot.py' }))).toBe('engine')
  })

  it('never treats a historical panel as the current baked baseline', () => {
    useAssetStore.setState({
      byId: {
        'History.pdf': { id: 'History.pdf', baked_overrides: q, baked_current: true } as never,
      },
    })
    expect(isJustBakedBaseline(panel())).toBe(false)
    // The same facts still apply to an ordinary panel with the same overrides.
    expect(isJustBakedBaseline(panel(false))).toBe(true)
  })

  it('does not let the current same-name asset decide a historical profile', () => {
    const assets = { 'History.pdf': { profile: 'native' } }
    expect(isNativePanelIssue(issue, doc(panel()), assets as never)).toBe(false)
    // Without an origin the current asset profile still gates a script-backed fix.
    expect(isNativePanelIssue(issue, doc({ ...panel(false), script: 'plot.py' }), assets as never)).toBe(true)
  })

  it('offers EPS and vector geometry from the historical execution', () => {
    const raster: PanelObject = {
      ...panel(),
      fileId: 'History.png',
      fileKind: 'raster',
      overrides: [],
      figureOrigin: {
        ...structuredClone(origin),
        snapshot: { ...origin.snapshot, sourceId: 'History.png' },
      },
    }
    expect(epsAvailability('original', 'History.png', null, false, null, raster)).toEqual({ ok: true, reason: 'none' })
    expect(epsAvailability('canvas', 'History.png', null, true, null, raster)).toEqual({ ok: false, reason: 'figure_not_ready' })
    expect(embedsDiskBitmap(raster, {}, 'original', ['eps'], null)).toBe(false)
    expect(embedsDiskBitmap(raster, {}, 'canvas', ['pdf'], null)).toBe(false)
    // The same raster panel without an origin still embeds the disk bitmap.
    expect(embedsDiskBitmap({ ...raster, figureOrigin: undefined }, {}, 'canvas', ['pdf'], null)).toBe(true)
  })
})
