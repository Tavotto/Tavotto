import { describe, expect, it } from 'vitest'

import type { ArtifactManifestSummary } from './api'
import {
  CHECK_NAMES,
  inspectionRollup,
  inspectionState,
  knownAdvisories,
  noticeTone,
  ptPair,
  textSizeNote,
} from './artifactInspection'

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

describe('noticeTone', () => {
  it('有失败项就是 bad；只有未核验项仍是中性 ok', () => {
    expect(noticeTone({ failed: ['Fig1.png'], unknown: [] })).toBe('bad')
    expect(noticeTone({ failed: [], unknown: ['Fig1.svg'] })).toBe('ok')
    expect(noticeTone({ failed: [], unknown: [] })).toBe('ok')
  })
})

describe('成品实测字号 text_size（ADR 0203）', () => {
  const m = (verdict: string, ts: ArtifactManifestSummary['text_size']): ArtifactManifestSummary => ({
    ...base,
    checks: { integrity: 'verified', size: 'verified', text_size: verdict as 'failed' },
    text_size: ts,
  })

  it('是闭集里的一项（界面按它取翻译）', () => {
    expect(CHECK_NAMES).toContain('text_size')
  })

  it('failed：带两个显示用的数', () => {
    expect(textSizeNote(m('failed', { reason: 'below_min', min_pt: 4.9, floor_pt: 5 }))).toEqual({
      kind: 'below',
      min: '4.9',
      floor: '5',
    })
  })

  it('只有失败才说话：量不全 / 通过 / 没规范 / 老服务端都不说', () => {
    expect(textSizeNote(m('unknown', { reason: 'unmeasured_runs', min_pt: 9, floor_pt: 6 }))).toBeNull()
    expect(textSizeNote(m('verified', { reason: 'ok', min_pt: 7, floor_pt: 6 }))).toBeNull()
    expect(textSizeNote(m('not_applicable', { reason: 'no_threshold', min_pt: 4.9, floor_pt: null }))).toBeNull()
    expect(textSizeNote(m('unknown', { reason: 'budget', min_pt: null, floor_pt: null }))).toBeNull()
    expect(textSizeNote(m('failed', undefined))).toBeNull()
    expect(textSizeNote(null)).toBeNull()
  })

  it('没有规范（not_applicable）不让原本全 verified 的 PDF 降成未核验', () => {
    const s = inspectionState(m('not_applicable', { reason: 'no_threshold', min_pt: 4.9, floor_pt: null }))
    expect(s.tone).toBe('verified')
    expect(s.unknown).toEqual([])
  })

  it('failed 也压住整份的语气：可选项失败照样红', () => {
    expect(inspectionState(m('failed', { reason: 'below_min', min_pt: 4.9, floor_pt: 5 })).tone).toBe('failed')
  })

  it('数字不说「5 pt 低于 5 pt」：四舍五入后不再更小就多给一位', () => {
    expect(ptPair(4.9, 5)).toEqual({ min: '4.9', floor: '5' })
    expect(ptPair(4.99, 5)).toEqual({ min: '4.99', floor: '5' })
    expect(ptPair(5.99, 6)).toEqual({ min: '5.99', floor: '6' })
    expect(ptPair(3.675, 5.5)).toEqual({ min: '3.7', floor: '5.5' })
    expect(ptPair(6, 7.25)).toEqual({ min: '6', floor: '7.25' })
  })
})

describe('knownAdvisories', () => {
  it('只留前端认得的码，顺序照服务端；老服务端没有这个键 = 没有留意', () => {
    expect(knownAdvisories(base)).toEqual([])
    expect(knownAdvisories(null)).toEqual([])
    const m = {
      ...base,
      advisories: [{ code: 'later_code' }, { code: 'scripted_raster', panels: ['a.png'] }, { code: 'type3_fonts' }],
    }
    expect(knownAdvisories(m).map((a) => a.code)).toEqual(['scripted_raster', 'type3_fonts'])
  })

  it('留意不改变核验语气（不是失败）', () => {
    const m = { ...base, checks: { integrity: 'verified', size: 'verified' } as const, advisories: [{ code: 'type3_fonts' }] }
    expect(inspectionState(m).tone).toBe('verified')
  })
})
