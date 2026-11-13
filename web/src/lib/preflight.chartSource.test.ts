/**
 * 来源与未应用编辑的主语是当前完整请求的后端成功响应。
 * 基础数据采自真实 HTTP/owned worker：冻结 INPUT 后删原 SVG，render 仍 200；
 * manifest 这里只保留该响应的 line 投影，字号/图例能力不在本组冒称资格。
 */
import { describe, expect, it } from 'vitest'
import type { ChartSourceRef, FigureDocument, PanelChartState, PanelObject } from '@/types/document'
import {
  emptyRender,
  exactPanelRender,
  renderKeyOf,
  renderRequestIdentity,
  type PanelRender,
} from '@/store/renderStore'
import { chartRequestIdentity } from './chartSource'
import { literal } from '@/i18n'
import type { Manifest, PanelInfo } from './api'
import { buildOriginalSpec, buildSpec, runSpec } from './preflight'
import { loadProfile } from './profile'

const scene = 'b47a8d7e67dd3b5da31dc9c7bdc2db0a95dbaaf7d4878d5869964177efaa103e'
const chart: PanelChartState = {
  model: {
    schema: 'tavotto.chart-model/1', rev: 1,
    axes: [{ id: 'ax1', rect: [10, 10, 80, 80], xlim: [0, 1], ylim: [0, 1],
      xscale: 'linear', yscale: 'linear', invert_x: false, invert_y: false, kind: 'numeric' }],
    series: [{ id: 's1', axes: 'ax1', kind: 'line', node: 'curve', x: 'x', y: 'y',
      style: { color: '#0000ff', linewidth: 3, label: 'Series A' } }],
    dataset: { columns: { x: [0, 1], y: [0.25, 0.25] } },
    data_digest: '977531c48efa3068889df31859033220e1bf29925cff6a04840753d498992ca6',
    scene_digest: scene, engine: { matplotlib: '3.11.2', numpy: '2.5.4', tavotto: '0.20.0' },
  },
  binding: { model_rev: 1, scene_digest: scene,
    entries: [{ object: 'series:s1', node: 'curve' }, { object: 'axes:ax1', node: 'old_axis' }] },
}
const source: ChartSourceRef = {
  schema: 1, sourceId: 'chart.svg', bytesSha256: scene, sizeBytes: 381, sceneDigest: scene,
}
const manifest: Manifest = {
  stem: 'chart', size_mm: [35.28, 35.28], elements: [{
    gid: 'axes_0.lines_0', role: 'line', label: '曲线 “Series A”',
    bbox: [0.1, 0.6799999999999999, 0.8, 0.04], draggable: false,
    editable: [{ prop: 'color', type: 'color', value: '#e32636', value_original: '#0000ff' }],
  }],
}
const revision = '66073b15e6e87de6b1e36f28c6e36d33c16266f43db8dc5bf03c6e534f81db2a'
const mm = 100 * 25.4 / 72
const assets = { 'chart.svg': { id: 'chart.svg' } as PanelInfo }
const profile = loadProfile()
type Render = { byKey: Record<string, PanelRender>; latest: Record<string, string> }

function panel(extra: Partial<PanelObject> = {}): PanelObject {
  return {
    id: 'p1', type: 'panel', fileId: 'chart.svg', fileKind: 'svg',
    chart: structuredClone(chart), chartSource: structuredClone(source),
    overrides: [{ gid: 'axes_0.lines_0', prop: 'color', value: '#e32636' }],
    nativeW: mm, nativeH: mm, x: 0, y: 0, w: mm, h: mm, ...extra,
  }
}

function response(p: PanelObject, extra: Partial<PanelRender> = {}): Render {
  const key = renderKeyOf(p)
  return {
    byKey: { [key]: {
      ...emptyRender, fileId: p.fileId, status: 'ready', manifest,
      chartRequest: chartRequestIdentity(p.chart, p.chartSource),
      ...(p.chart ? { chartRevision: revision } : {}),
      lastPatches: renderRequestIdentity(p.overrides, p.chart, p.chartSource), ...extra,
    } },
    latest: { [p.fileId]: key },
  }
}

function inputs(p: PanelObject, render: Render, library: Record<string, PanelInfo> = {}) {
  return [
    buildSpec({ objects: [p], page: { w: 85, h: 85, margin: 0 } } as FigureDocument, library, render),
    buildOriginalSpec(p, library, render, { widthMm: mm, heightMm: mm }, ['pdf']),
  ]
}

describe('计算图表预检消费真实当前请求身份', () => {
  it('已验证附件不需要磁盘原 SVG：两范围都不再报缺素材或未应用编辑', () => {
    const p = panel(), render = response(p)
    expect(exactPanelRender(render, p)).not.toBeNull()
    for (const spec of inputs(p, render)) {
      expect(spec.panels[0].missing).toBe(false)
      expect(spec.panels[0].unapplied_overrides).toBe(0)
      const ids = runSpec(spec, profile).map(issue => issue.id)
      expect(ids).not.toContain('missing-asset')
      expect(ids).not.toContain('unapplied-override')
    }
  })

  it('磁盘原件仍在且 chart 没有附件时，同一完整请求也没有未应用误报', () => {
    const p = panel({ chartSource: undefined })
    for (const spec of inputs(p, response(p), assets)) {
      expect(spec.panels[0].missing).toBe(false)
      expect(spec.panels[0].unapplied_overrides).toBe(0)
    }
  })

  it('仅 chart 的旧成功渲染不能证明已经删除的磁盘原件仍可用', () => {
    const p = panel({ chartSource: undefined })
    for (const spec of inputs(p, response(p))) expect(spec.panels[0].missing).toBe(true)
  })

  it.each(['未请求', '仍在等待'] as const)('%s 的 chart/ref 不能凭字段存在放行', state => {
    const p = panel()
    const render = state === '未请求' ? { byKey: {}, latest: {} }
      : response(p, { manifest: null, chartRevision: undefined, status: 'rendering', lastPatches: null })
    expect(exactPanelRender(render, p)).toBeNull()
    for (const spec of inputs(p, render)) {
      expect(spec.panels[0].missing).toBe(true)
      expect(spec.panels[0].unapplied_overrides).toBe(1)
    }
  })

  it.each(['坏引用', '换附件', '换模型', '换绑定', '另一副本编辑'] as const)('%s 不得借旧 latest 响应作正证', change => {
    const p = panel(), render = response(p)
    if (change === '坏引用') p.chartSource = { schema: 9 } as unknown as ChartSourceRef
    if (change === '换附件') p.chartSource = { ...source, bytesSha256: 'f'.repeat(64) }
    if (change === '换模型') p.chart!.model.axes[0].xlim = [0, 2]
    if (change === '换绑定') p.chart!.binding.entries[0].node = 'other_curve'
    if (change === '另一副本编辑') {
      p.id = 'p2'
      p.overrides[0].value = '#008000'
    }
    expect(exactPanelRender(render, p)).toBeNull()
    for (const spec of inputs(p, render)) {
      expect(spec.panels[0].missing).toBe(true)
      expect(spec.panels[0].unapplied_overrides).toBe(1)
    }
  })

  it.each([
    { stale: true },
    { manifest: null },
    { chartRevision: undefined },
    { chartRequest: 'different accepted source request' },
    { lastPatches: 'different full patch request' },
  ] satisfies Partial<PanelRender>[])('不完整/过期 exact 条目不能代替来源证人 %j', incomplete => {
    const p = panel(), render = response(p, incomplete)
    expect(exactPanelRender(render, p)).toBeNull()
    for (const spec of inputs(p, render)) expect(spec.panels[0].missing).toBe(true)
  })

  it('真实 chart 终态失败的 stale/error 仍报出来，不能拿先前 manifest 当来源通过', () => {
    const p = panel()
    const render = response(p, {
      status: 'error', stale: true, code: 'chart_source_mismatch', error: literal('source mismatch'),
    })
    expect(exactPanelRender(render, p)).toBeNull()
    for (const spec of inputs(p, render)) {
      expect(spec.panels[0].missing).toBe(true)
      const ids = runSpec(spec, profile).map(issue => issue.id)
      expect(ids).toContain('missing-asset')
      expect(ids).toContain('stale-render')
      expect(ids).toContain('render-error')
    }
  })

  it('同一请求补发 rendering 时已确认的 exact 结果沿用既有资格', () => {
    const p = panel(), render = response(p, { status: 'rendering' })
    expect(exactPanelRender(render, p)).not.toBeNull()
    for (const spec of inputs(p, render)) expect(spec.panels[0].missing).toBe(false)
  })

  it('无 chart/ref 的普通面板维持裸 patches 身份和磁盘缺失判定', () => {
    const p = panel({ chart: undefined, chartSource: undefined, fileKind: 'pdf' })
    expect(renderRequestIdentity(p.overrides)).toBe(JSON.stringify(p.overrides))
    for (const spec of inputs(p, response(p), assets)) {
      expect(spec.panels[0].missing).toBe(false)
      expect(spec.panels[0].unapplied_overrides).toBe(0)
    }
    for (const spec of inputs(p, response(p))) expect(spec.panels[0].missing).toBe(true)
  })

  it('无 chart 的 runtime 不需要磁盘素材，已应用/未应用编辑仍分别报告', () => {
    const p = panel({ chart: undefined, chartSource: undefined, fileKind: 'runtime', fileId: 'runtime:opaque' })
    for (const spec of inputs(p, response(p))) {
      expect(spec.panels[0].missing).toBe(false)
      expect(spec.panels[0].unapplied_overrides).toBe(0)
    }
    for (const spec of inputs(p, { byKey: {}, latest: {} })) {
      expect(spec.panels[0].missing).toBe(false)
      expect(spec.panels[0].unapplied_overrides).toBe(1)
    }
  })
})
