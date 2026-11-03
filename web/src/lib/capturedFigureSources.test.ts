import { describe, expect, it } from 'vitest'
import type { CapturedFigureDescriptor } from '@/lib/api'
import type { PanelObject } from '@/types/document'
import { capturedFigureSources, executedFigureSources } from './capturedFigureSources'

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

  it('maps retained exact artifacts back to legacy IDs without inferring a sibling format', () => {
    expect(capturedFigureSources([], [
      panel(String.raw`sub\Fig1.png`, { script: null }), panel('sub/Fig1.pdf'),
      panel('elsewhere/Fig1.png'), panel('runtime:other', { fileKind: 'runtime' }),
    ], ['sub/Fig1.png'])).toEqual(['sub/Fig1.png', String.raw`sub\Fig1.png`])
  })

  it('preserves directory identity and accepts persisted Windows separators', () => {
    expect(capturedFigureSources([captured({ original_artifact: null })], [
      panel('sub\\Fig1.pdf'), panel('elsewhere/Fig1.pdf'), panel('sub/Fig1.pdf', { script: null }),
    ])).toEqual(['runtime:opaque', 'sub\\Fig1.pdf'])
  })
})

describe('executed Figure output ownership', () => {
  const runtime = (id: string, runConfig?: string, script = 'plot.py'): PanelObject => panel(id, {
    fileKind: 'runtime', source: {
      script, entry: '__main__', stem: 'same-stem', runConfig, captureSource: 'pyplot',
      fingerprint: 'old', sizeMm: [80, 60],
    },
  })
  it('retires omitted/renamed runtime outputs of exactly the executed owner, without interpreting IDs', () => {
    expect(executedFigureSources({
      script: 'plot.py', run_config: 'rc-one', source_ids: ['runtime:new-name'],
    }, [
      runtime('runtime:old-A', 'rc-one'), runtime('runtime:old-B', 'rc-one'),
      runtime('runtime:same-stem-config-two', 'rc-two'), runtime('runtime:no-args'),
      runtime('runtime:other-script', 'rc-one', 'other.py'),
      panel('runtime:unknown-owner', { fileKind: 'runtime' }),
      panel('same-stem.pdf'), panel('external.pdf', { script: null }),
    ])).toEqual(['runtime:new-name', 'runtime:old-A', 'runtime:old-B'])
  })
  it('uses backend static bindings; old captures identify runtime IDs but cannot claim a rebound disk file', () => {
    expect(executedFigureSources({
      script: 'plot.py', run_config: 'rc-one', source_ids: ['out/old-B.pdf'],
    }, [panel(String.raw`out\old-B.pdf`), panel('out/old-B.png'), panel('sub/Fig1.pdf'), panel('sub/Fig1.svg')], [
      captured({ run_config: 'rc-one' }), captured({ asset_id: 'runtime:other', run_config: 'rc-two', original_artifact: 'other.pdf' }),
    ])).toEqual(['out/old-B.pdf', 'runtime:opaque', String.raw`out\old-B.pdf`])
  })
  it('normalizes script paths while keeping no-argument and configured ownership separate', () => {
    expect(executedFigureSources({
      script: 'sub/plot.py', run_config: null, source_ids: [],
    }, [runtime('runtime:old-B', undefined, String.raw`sub\plot.py`), runtime('runtime:configured', 'rc-one', 'sub/plot.py')]))
      .toEqual(['runtime:old-B'])
  })
})
