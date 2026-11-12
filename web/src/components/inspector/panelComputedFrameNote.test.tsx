/**
 * /2 计算图表的「采用渲染外框」（N5 第 6 步）：精确渲染的外框比文档里持久的那一版
 * 新时出现一条提示，采用只动外框与落位（一条历史），数据与单位不动；外框一致时不出现。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { ComputedRenderFrame } from '@/lib/chartFrame'
import { chartRequestIdentity } from '@/lib/chartSource'
import { useDocumentStore } from '@/store/documentStore'
import { useRenderStore } from '@/store/renderStore'
import { seedExactRender } from '@/test/renderFixtures'
import { emptyProject, type PanelChartState, type PanelObject } from '@/types/document'
import chartVectors from '../../../../tests/golden/chart_model_vectors.json'
import { PanelComputedFrameNote } from './PanelSection'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const PT_MM = 25.4 / 72
const FRAME: ComputedRenderFrame = {
  version: 1, source: 'computed-svg', source_viewport_pt: [100, 100],
  rect_pt: [-21, -1, 122, 109], edit_basis: 'source-viewport-pt',
}

const chartOf = (): PanelChartState => {
  const v = chartVectors.vectors.find((v) => v.expect === 'ok')!
  return structuredClone({ model: v.model, binding: v.binding }) as unknown as PanelChartState
}

const chartPanel = (originPt: [number, number], native: [number, number]): PanelObject => ({
  id: 'p1',
  type: 'panel',
  fileId: 'chart.svg',
  fileKind: 'svg',
  script: null,
  nativeW: native[0],
  nativeH: native[1],
  x: 10,
  y: 10,
  w: 40,
  h: 30,
  overrides: [],
  chart: chartOf(),
  chartFrame: { version: 1, renderProfile: 'native-manifest/2', originPt },
})

let host: HTMLDivElement
let root: Root
beforeEach(() => {
  useRenderStore.getState().clear()
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
})

async function mount(panel: PanelObject) {
  const doc = emptyProject()
  doc.canvases[0].objects = [panel]
  await act(async () => {
    await useDocumentStore.getState().switchDocument(doc, 'd-computed-frame-note')
  })
  const p = useDocumentStore.getState().doc.objects[0] as PanelObject
  seedExactRender(
    p,
    {
      stem: 'chart',
      size_mm: [FRAME.rect_pt[2] * PT_MM, FRAME.rect_pt[3] * PT_MM],
      elements: [],
      render_frame: FRAME,
    },
    {
      chartRevision: 'r1',
      chartRequest: chartRequestIdentity(p.chart, p.chartSource, p.chartFrame?.renderProfile) ?? '',
    },
  )
  await act(async () => root.render(<PanelComputedFrameNote panel={p} />))
  return useDocumentStore.getState().doc.objects[0] as PanelObject
}

describe('采用渲染外框（/2）', () => {
  it('持久外框与精确渲染一致时不出现', async () => {
    await mount(chartPanel([-21, -1], [122 * PT_MM, 109 * PT_MM]))
    expect(host.querySelector('[data-frame-adopt="computed"]')).toBeNull()
  })

  it('外框变了：提示出现，采用后 originPt 与 native 尺寸更新、内容页位置不动', async () => {
    const before = await mount(chartPanel([0, 0], [100 * PT_MM, 100 * PT_MM]))
    const button = host.querySelector<HTMLButtonElement>('[data-frame-adopt="computed"]')
    expect(button).not.toBeNull()
    const overridesBefore = structuredClone(before.overrides)
    const historyBefore = useDocumentStore.getState().past.length
    // 采用前：外框 = S viewport，S 中心的页位置
    const sCenterBefore = [before.x + 0.5 * before.w, before.y + 0.5 * before.h]
    await act(async () => button!.click())
    const after = useDocumentStore.getState().doc.objects[0] as PanelObject
    expect(after.chartFrame).toEqual({ version: 1, renderProfile: 'native-manifest/2', originPt: [-21, -1] })
    expect(after.nativeW).toBeCloseTo(122 * PT_MM, 9)
    expect(after.nativeH).toBeCloseTo(109 * PT_MM, 9)
    // 科学内容与布局锚点不动（面板框随外框补偿，S 中心的页位置前后重合）
    expect(after.overrides).toEqual(overridesBefore)
    const sCenterAfter = [
      after.x + ((0.5 * 100 + 21) / 122) * after.w,
      after.y + ((0.5 * 100 + 1) / 109) * after.h,
    ]
    expect(sCenterAfter[0]).toBeCloseTo(sCenterBefore[0], 9)
    expect(sCenterAfter[1]).toBeCloseTo(sCenterBefore[1], 9)
    expect(useDocumentStore.getState().past.length).toBe(historyBefore + 1)
  })
})
