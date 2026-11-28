/**
 * preflight 的「未应用 override」判据用**完整请求身份**（matchesRequestIdentity）：
 * chart / chartSource / figureOrigin / renderProfile 都参与身份——本层把 `lastPatches`
 * 改成完整身份后，裸比 `JSON.stringify(overrides)` 的旧比较会把已画好的 chart 面板
 * 永远报成未应用（Problems 面板与导出摘要同一条链，评审 P2）。
 */
import { describe, expect, it } from 'vitest'
import { buildOriginalSpec } from './preflight'
import { renderKeyOf, renderRequestIdentity, type PanelRender } from '@/store/renderStore'
import chartVectors from '../../../tests/golden/chart_model_vectors.json'
import type { PanelChartState, PanelObject } from '@/types/document'

const v = chartVectors.vectors.find(v => v.expect === 'ok')!
const chart = structuredClone({ model: v.model, binding: v.binding }) as unknown as PanelChartState
const overrides = [{ gid: 'axes_0.title', prop: 'fontsize', value: 12 }]
const panel = {
  id: 'p1', type: 'panel', fileId: 'chart.svg', fileKind: 'svg',
  x: 0, y: 0, w: 100, h: 80, nativeW: 100, nativeH: 80, overrides, chart,
  chartFrame: { version: 1, renderProfile: 'native-manifest/2', originPt: [-8, -6] },
} as unknown as PanelObject
const manifest = { stem: 'chart', size_mm: [100, 80], elements: [] }
const size = { widthMm: 100, heightMm: 80 }
const key = renderKeyOf(panel)

const renderWith = (lastPatches: string) =>
  ({ byKey: { [key]: { fileId: 'chart.svg', manifest, rev: 1, status: 'ready', stale: false, lastPatches, chartRevision: 'rev' } as unknown as PanelRender }, latest: {} })

describe('preflight 的未应用判据按完整请求身份', () => {
  it('exact 渲染在册（完整身份一致）的 chart 面板不再报 unapplied', () => {
    const identity = renderRequestIdentity(panel.overrides, panel.chart, undefined, undefined, 'native-manifest/2')
    const spec = buildOriginalSpec(panel, {}, renderWith(identity), size, ['pdf'])
    expect(spec.panels[0]!.unapplied_overrides).toBe(0)
  })

  it('渲染在册的还是别的变体（身份不一致）时照报条数', () => {
    const spec = buildOriginalSpec(panel, {}, renderWith(JSON.stringify(overrides)), size, ['pdf'])
    expect(spec.panels[0]!.unapplied_overrides).toBe(overrides.length)
  })
})
