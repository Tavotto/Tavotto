import { describe, expect, it } from 'vitest'
import {
  adoptionGuardOf, adoptionOf, artifactRequestFields, artifactValidationIssue, clearAdoption, FIGSIZE_SOURCE_POLICY,
  isAdoptedPanel, isSourceBackedFigure, isSourceBackedZeroEdit, originalArtifactOverrides, refuseArtifactOperation,
  SCRIPT_BACKED_SOURCE_POLICY, sourceBackedGuard, verdictFromResponse, verifiedSource, ArtifactValidationError,
  type SourceBackedFigure,
} from './artifactValidation'
import { migrateToProject, emptyProject, type PanelObject } from '@/types/document'

const FRAME = { gid: 'figure', prop: 'frame', value: 'figsize' }
const TITLE = { gid: 'axes_0.title', prop: 'fontsize', value: 14 }
const SHA = 'c'.repeat(64)
const exact: SourceBackedFigure = {
  version: 1, policy: SCRIPT_BACKED_SOURCE_POLICY, sourceId: 'plot.png', bytesSha256: 'a'.repeat(64), sizeBytes: 10,
  status: 'exact', reconstructionSha256: SHA,
}
const reconstructed: SourceBackedFigure = { ...exact, status: 'reconstructed', confirmedAt: '2026-10-20T10:00:00.000Z' }
const strict = { version: 1 as const, requiredFrame: 'figsize' as const, sourceId: 'plot.png', bytesSha256: 'a'.repeat(64), sizeBytes: 10 }
const base = { id: 'p', type: 'panel' as const, fileId: 'plot.png', fileKind: 'raster' as const, x: 0, y: 0, w: 10, h: 10, nativeW: 10, nativeH: 10 }
const panel = (extra: Partial<PanelObject> = {}): PanelObject =>
  ({ ...base, overrides: [FRAME], sourceBackedFigure: reconstructed, ...extra }) as PanelObject
const strictPanel = (extra: Partial<PanelObject> = {}): PanelObject =>
  ({ ...base, overrides: [FRAME], artifactValidation: strict, ...extra }) as PanelObject

describe('sourceBackedFigure guard', () => {
  it('accepts exact and reconstructed shapes', () => {
    expect(isSourceBackedFigure(exact)).toBe(true)
    expect(isSourceBackedFigure(reconstructed)).toBe(true)
    expect(artifactValidationIssue(panel())).toBeNull()
  })
  it.each([
    ['unknown version', { ...exact, version: 2 }],
    ['other policy', { ...exact, policy: FIGSIZE_SOURCE_POLICY }],
    ['strict-shaped field leaking in', { ...exact, requiredFrame: 'figsize' }],
    ['unknown status', { ...exact, status: 'needs_confirmation' }],
    ['reconstructed without a confirmation time', { ...exact, status: 'reconstructed' }],
    ['empty confirmation time', { ...reconstructed, confirmedAt: '' }],
    ['bad digest', { ...exact, reconstructionSha256: 'zz' }],
    ['uppercase digest', { ...exact, reconstructionSha256: 'C'.repeat(64) }],
    ['zero size', { ...exact, sizeBytes: 0 }],
    ['fractional size', { ...exact, sizeBytes: 1.5 }],
    ['no source id', { ...exact, sourceId: undefined }],
    ['null', null],
    ['array', []],
    ['string', 'exact'],
  ])('rejects %s and never treats it as legacy', (_name, bad) => {
    expect(isSourceBackedFigure(bad)).toBe(false)
    const p = panel({ sourceBackedFigure: bad as never })
    expect(artifactValidationIssue(p)).toBe('invalid_guard')
    expect(isAdoptedPanel(p)).toBe(true)
    expect(() => artifactRequestFields(p)).toThrow(ArtifactValidationError)
    expect(() => refuseArtifactOperation(p)).toThrow(ArtifactValidationError)
  })
  it('a panel carrying both guards is invalid, not strict and not source-backed', () => {
    const both = panel({ artifactValidation: strict })
    expect(adoptionOf(both)).toEqual({ kind: 'invalid' })
    expect(artifactValidationIssue(both)).toBe('invalid_guard')
  })
  it('keeps the saved identity and frame discipline of the strict guard', () => {
    expect(artifactValidationIssue(panel({ fileId: 'other.png' }))).toBe('source_changed')
    expect(artifactValidationIssue(panel({ overrides: [] }))).toBe('frame_changed')
    expect(artifactValidationIssue(panel({ overrides: [{ ...FRAME, value: 'tight' }] }))).toBe('frame_changed')
  })
  it('one verdict answers which selection context a panel is in', () => {
    expect(adoptionOf({ ...base, overrides: [] }).kind).toBe('none')
    expect(adoptionOf(strictPanel()).kind).toBe('strict')
    expect(adoptionOf(panel()).kind).toBe('source')
    expect(adoptionGuardOf({ ...base, overrides: [] })).toBeUndefined()
    expect(adoptionGuardOf(panel())).toEqual(reconstructed)
    expect(adoptionGuardOf(panel({ sourceBackedFigure: null as never }))).toBeNull()
  })
})

describe('request fields and the original/edit split', () => {
  it('zero edits send the policy and the source identity but no confirmation', () => {
    expect(artifactRequestFields(panel())).toEqual({
      source_policy: SCRIPT_BACKED_SOURCE_POLICY,
      expected_source: { bytes_sha256: 'a'.repeat(64), size_bytes: 10 },
    })
  })
  it('a real edit carries the confirmed reconstruction digest; strict panels never do', () => {
    expect(artifactRequestFields(panel({ overrides: [FRAME, TITLE] }))).toEqual({
      source_policy: SCRIPT_BACKED_SOURCE_POLICY,
      expected_source: { bytes_sha256: 'a'.repeat(64), size_bytes: 10 },
      confirmed_reconstruction_sha256: SHA,
    })
    expect(artifactRequestFields(strictPanel({ overrides: [FRAME, TITLE] })))
      .toEqual({ source_policy: FIGSIZE_SOURCE_POLICY, expected_source: { bytes_sha256: 'a'.repeat(64), size_bytes: 10 } })
  })
  it('exact status confirms with the admitted digest too', () => {
    expect(artifactRequestFields(panel({ sourceBackedFigure: exact, overrides: [FRAME, TITLE] })).confirmed_reconstruction_sha256).toBe(SHA)
  })
  it('only a structural frame is "unedited": original export copies bytes, real edits export the redraw', () => {
    expect(originalArtifactOverrides(panel())).toEqual([])
    expect(isSourceBackedZeroEdit(panel())).toBe(true)
    const edited = panel({ overrides: [FRAME, TITLE] })
    expect(originalArtifactOverrides(edited)).toEqual([FRAME, TITLE])
    expect(isSourceBackedZeroEdit(edited)).toBe(false)
    expect(isSourceBackedZeroEdit(panel({ overrides: [] }))).toBe(false) // invalid guard never claims "original pixels"
    expect(isSourceBackedZeroEdit({ ...base, overrides: [FRAME] } as PanelObject)).toBe(false)
  })
  it('write-back, rebuild, sync and auto-fix are refused for both contexts', () => {
    for (const p of [panel(), strictPanel()]) {
      expect(() => refuseArtifactOperation(p)).toThrowError(expect.objectContaining({ reason: 'writeback_not_supported' }))
    }
    expect(() => refuseArtifactOperation({ ...base, overrides: [] } as PanelObject)).not.toThrow()
  })
  it('clearAdoption removes both guards', () => {
    const p = panel({ artifactValidation: strict })
    clearAdoption(p)
    expect(isAdoptedPanel(p)).toBe(false)
  })
})

describe('admission response parsing', () => {
  const body = { status: 'needs_confirmation', reconstruction_sha256: SHA, metrics: { differing_pixels: 12, differing_ratio: 0.01, total_pixels: 1200, bbox: [1, 2, 3, 4] } }
  it('reads the verdict', () => {
    expect(verdictFromResponse(body)).toEqual({
      status: 'needs_confirmation', reconstructionSha256: SHA,
      metrics: { differingPixels: 12, differingRatio: 0.01, totalPixels: 1200, bbox: [1, 2, 3, 4] },
    })
    expect(verdictFromResponse({ ...body, status: 'exact', metrics: { ...body.metrics, bbox: null } }).metrics.bbox).toBeNull()
  })
  it.each([undefined, null, {}, { ...body, status: 'reconstructed' }, { ...body, reconstruction_sha256: 'x' },
    { ...body, metrics: undefined }, { ...body, metrics: { ...body.metrics, bbox: [1, 2, 3] } }])('refuses a malformed verdict %#', (bad) => {
    expect(() => verdictFromResponse(bad)).toThrowError(expect.objectContaining({ reason: 'unsupported_response' }))
  })
  it('the source echo must carry the requested policy', () => {
    const src = { render_policy: SCRIPT_BACKED_SOURCE_POLICY, source_id: 'plot.png', origin: 'static', kind: 'png', bytes_sha256: 'a'.repeat(64), size_bytes: 10 }
    expect(verifiedSource(src, 'plot.png', SCRIPT_BACKED_SOURCE_POLICY).bytesSha256).toBe('a'.repeat(64))
    expect(() => verifiedSource(src, 'plot.png', FIGSIZE_SOURCE_POLICY)).toThrow(ArtifactValidationError)
    expect(() => verifiedSource(src, 'plot.png', SCRIPT_BACKED_SOURCE_POLICY, { bytesSha256: 'b'.repeat(64), sizeBytes: 10 }))
      .toThrowError(expect.objectContaining({ reason: 'source_changed' }))
  })
  it('exact has no confirmation time, a confirmed redraw always has one', () => {
    const v = verdictFromResponse(body)
    const src = { sourceId: 'plot.png', bytesSha256: 'a'.repeat(64), sizeBytes: 10 }
    expect(sourceBackedGuard(src, v)).toEqual(exact)
    expect(sourceBackedGuard(src, v, reconstructed.confirmedAt)).toEqual(reconstructed)
    expect(isSourceBackedFigure(sourceBackedGuard(src, v, 'now'))).toBe(true)
  })
})

describe('persistence and old readers', () => {
  const doc = () => {
    const d = emptyProject()
    d.canvases[0].objects.push(panel())
    return d
  }
  it('save → reopen keeps the guard byte for byte and invents nothing', () => {
    const reopened = migrateToProject(JSON.parse(JSON.stringify(doc())))!
    const p = reopened.canvases[0].objects[0] as PanelObject
    expect(p.sourceBackedFigure).toEqual(reconstructed)
    expect(p.overrides).toEqual([FRAME])
    expect(Object.hasOwn(p, 'artifactValidation')).toBe(false)
    expect(JSON.stringify(migrateToProject(JSON.parse(JSON.stringify(reopened)))!)).toBe(JSON.stringify(reopened))
  })
  it('an old project never gains the field, and a legacy-frame panel is not adopted', () => {
    const d = emptyProject()
    d.canvases[0].objects.push({ ...base, overrides: [] } as PanelObject)
    const p = migrateToProject(JSON.parse(JSON.stringify(d)))!.canvases[0].objects[0] as PanelObject
    expect(Object.hasOwn(p, 'sourceBackedFigure')).toBe(false)
    expect(isAdoptedPanel(p)).toBe(false)
  })
  it('an old reader that only knows artifactValidation sees no strict guard, so it never uses the new field as one', () => {
    const p = migrateToProject(JSON.parse(JSON.stringify(doc())))!.canvases[0].objects[0] as PanelObject
    // 旧前端的判据就是这一条：它在场才算严格 guard
    expect(Object.hasOwn(p, 'artifactValidation')).toBe(false)
    expect(p.fileId).toBe('plot.png')
    expect(p.overrides).toEqual([FRAME])
  })
})

describe('canvas export: every instance of the same source carries its own confirmation', () => {
  it('two edited instances with different confirmed digests and a zero-edit one', async () => {
    const { toExportObjects } = await import('./exportPayload')
    const other = { ...reconstructed, reconstructionSha256: 'd'.repeat(64) }
    const objs = [
      panel({ id: 'a', overrides: [FRAME, TITLE] }),
      panel({ id: 'b', overrides: [FRAME, TITLE], sourceBackedFigure: other }),
      panel({ id: 'c' }),
    ]
    const out = toExportObjects(objs) as unknown as { confirmed_reconstruction_sha256?: string; source_policy?: string }[]
    expect(out.map((o) => o.confirmed_reconstruction_sha256)).toEqual([SHA, 'd'.repeat(64), undefined])
    expect(out.every((o) => o.source_policy === SCRIPT_BACKED_SOURCE_POLICY)).toBe(true)
  })
})
