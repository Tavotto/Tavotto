import { describe, expect, it } from 'vitest'
import type { CapturedFigureDescriptor } from '@/lib/api'
import type { PanelObject } from '@/types/document'
import { capturedFigureSources } from './capturedFigureSources'

const captured = (over: Partial<CapturedFigureDescriptor> = {}): CapturedFigureDescriptor => ({
  asset_id: 'runtime:opaque', script: 'plot.py', entry: '__main__', stem: 'sub/Fig1',
  capture_source: 'savefig', execution_profile: 'safe', original_artifact: 'sub/Fig1.pdf',
  size_mm: [80, 60], source_fingerprint: 'sha256:source', can_writeback_artifact: true,
  can_writeback_source: true, ...over,
})
const panel = (fileId: string, over: Partial<PanelObject> = {}): PanelObject => ({
  id: fileId, type: 'panel', fileId, fileKind: 'pdf', script: 'plot.py',
  x: 0, y: 0, w: 80, h: 60, nativeW: 80, nativeH: 60, overrides: [], ...over,
})

describe('captured Figure source identities', () => {
  it('includes the exact original artifact without needing a currently live panel', () => {
    expect(capturedFigureSources([captured()], [])).toEqual(['runtime:opaque', 'sub/Fig1.pdf'])
  })
  it('includes every captured opaque ID and matching disk source, once each', () => {
    expect(capturedFigureSources([captured(), captured()], [
      panel('sub/Fig1.pdf'), panel('sub/Fig1.png', { fileKind: 'raster' }), panel('sub/Fig1.pdf'),
      panel('Fig1.pdf'), panel('sub/Fig2.pdf'), panel('sub/Fig1.svg', { script: 'other.py' }),
    ])).toEqual(['runtime:opaque', 'sub/Fig1.pdf', 'sub/Fig1.png'])
  })

  it('never decodes runtime IDs or invalidates a sibling run configuration by stem', () => {
    expect(capturedFigureSources([captured({ run_config: 'rc_new' })], [
      panel('runtime:other-config', { fileKind: 'runtime' }), panel('sub/Fig1.pdf'), panel('sub/Fig1.png'),
    ])).toEqual(['runtime:opaque', 'sub/Fig1.pdf'])
    expect(capturedFigureSources([captured({ run_config: 'rc_new', original_artifact: null })], [
      panel('sub/Fig1.pdf'),
    ])).toEqual(['runtime:opaque'])
  })

  it('preserves directory identity and accepts persisted Windows separators', () => {
    expect(capturedFigureSources([captured({ original_artifact: null })], [
      panel('sub\\Fig1.pdf'), panel('elsewhere/Fig1.pdf'), panel('sub/Fig1.pdf', { script: null }),
    ])).toEqual(['runtime:opaque', 'sub\\Fig1.pdf'])
  })
})
