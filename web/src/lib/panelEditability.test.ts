import { beforeEach, describe, expect, it, vi } from 'vitest'
import { canEditPanel } from './panelEditability'
import { engineRender, type Manifest } from './api'
import { exactPanelManifest, useRenderStore } from '@/store/renderStore'
import chartVectors from '../../../tests/golden/chart_model_vectors.json'
import type { PanelChartState, PanelObject } from '@/types/document'

vi.mock('./api', async original => ({ ...await original<typeof import('./api')>(), engineRender: vi.fn() }))
const manifest: Manifest = { stem: 'chart', size_mm: [80, 60], elements: [] }
const panel = (): PanelObject => {
  const v = chartVectors.vectors.find(v => v.expect === 'ok')!
  const chart = structuredClone({ model: v.model, binding: v.binding }) as unknown as PanelChartState
  return { id: 'p', type: 'panel', fileId: 'chart.svg', fileKind: 'svg', x: 0, y: 0, w: 80, h: 60,
    nativeW: 80, nativeH: 60, overrides: [], chart }
}
beforeEach(() => { useRenderStore.getState().clear(); vi.mocked(engineRender).mockReset() })

describe('图内编辑入口资格', () => {
  it('Python与普通静态图沿用旧资格；chart模型本身不冒充已配对对象', () => {
    const p = panel()
    expect(canEditPanel(p)).toBe(false)
    expect(canEditPanel({ ...p, chart: undefined, script: 'plot.py' })).toBe(true)
    expect(canEditPanel({ ...p, chart: undefined })).toBe(false)
  })

  it('真实render状态机收下配对manifest后可进入，等待同chart新patch仍保留入口但无当前几何', async () => {
    const p = panel()
    vi.mocked(engineRender).mockResolvedValue({ rev: 1, manifest, svg: '<svg/>', chart_revision: 'paired' })
    await useRenderStore.getState().render(p.fileId, p.overrides, undefined, undefined, undefined, undefined, 'foreground', p.chart)
    expect(canEditPanel(p)).toBe(true)
    const changed = { ...p, overrides: [{ gid: 'axes_0', prop: 'xlim', value: [0, 20] }] }
    expect(canEditPanel(changed)).toBe(true)
    expect(exactPanelManifest(useRenderStore.getState(), changed)).toBeNull()
    changed.chart = structuredClone(p.chart!)
    changed.chart.model.axes[0].xlim[1] += 20
    expect(canEditPanel(changed)).toBe(false)
  })

  it('源文件被markStale后旧配对不授予新进入资格', async () => {
    const p = panel()
    vi.mocked(engineRender).mockResolvedValue({ rev: 1, manifest, svg: '<svg/>', chart_revision: 'paired' })
    await useRenderStore.getState().render(p.fileId, p.overrides, undefined, undefined, undefined, undefined, 'foreground', p.chart)
    useRenderStore.getState().markStale([p.fileId])
    expect(canEditPanel(p)).toBe(false)
  })
})
