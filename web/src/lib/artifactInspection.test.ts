import { describe, expect, it } from 'vitest'

import type { ArtifactManifestSummary } from './api'
import { inspectionRollup, inspectionState } from './artifactInspection'

const base: ArtifactManifestSummary = {
  manifest_version: 1,
  format: 'pdf',
  sha256: 'x',
  bytes: 1,
  policy: 'standard',
  verdict: 'accepted',
  required: ['integrity', 'size'],
  checks: {},
  notes: [],
}

describe('inspectionState', () => {
  it('没有 manifest = 整份未核验，不是通过', () => {
    expect(inspectionState(null).tone).toBe('unknown')
    expect(inspectionState(undefined).tone).toBe('unknown')
  })

  it('全部可判项 verified 才是 verified；not_applicable 不进任何列表', () => {
    const s = inspectionState({
      ...base,
      checks: { integrity: 'verified', size: 'verified', text_layer: 'not_applicable' },
    })
    expect(s).toEqual({ tone: 'verified', failed: [], unknown: [], verified: ['integrity', 'size'] })
  })

  it('一项 unknown 就不是 verified（未核验不画绿）', () => {
    const s = inspectionState({
      ...base,
      checks: { integrity: 'verified', size: 'verified', clipping: 'unknown' },
    })
    expect(s.tone).toBe('unknown')
    expect(s.unknown).toEqual(['clipping'])
    expect(s.verified).toEqual(['integrity', 'size'])
  })

  it('failed 压过 unknown，且列出失败项', () => {
    const s = inspectionState({
      ...base,
      checks: { integrity: 'verified', dpi_tag: 'failed', raster_density: 'unknown' },
    })
    expect(s.tone).toBe('failed')
    expect(s.failed).toEqual(['dpi_tag'])
    expect(s.unknown).toEqual(['raster_density'])
  })

  it('一项 verified 都没有（全 not_applicable）不算通过', () => {
    const s = inspectionState({ ...base, checks: { text_layer: 'not_applicable' } })
    expect(s.tone).toBe('unknown')
  })

  it('verdict=rejected 的 manifest 永远不是 verified', () => {
    const s = inspectionState({
      ...base,
      verdict: 'rejected',
      checks: { integrity: 'verified', size: 'verified' },
    })
    expect(s.tone).toBe('unknown')
  })
})

describe('inspectionRollup', () => {
  it('按文件名点名失败与未核验的文件；核过的不出现；没有 manifest 的算未核验', () => {
    const r = inspectionRollup([
      { path: '/a/b/Fig1.pdf', manifest: { ...base, checks: { integrity: 'verified', size: 'verified' } } },
      { path: 'C:\\out\\Fig1.png', manifest: { ...base, checks: { integrity: 'verified', dpi_tag: 'failed' } } },
      { path: '/a/b/Fig1.svg', manifest: { ...base, checks: { integrity: 'unknown' } } },
      { path: '/a/b/Fig1.tiff' },
    ])
    expect(r).toEqual({ failed: ['Fig1.png'], unknown: ['Fig1.svg', 'Fig1.tiff'] })
  })
})
