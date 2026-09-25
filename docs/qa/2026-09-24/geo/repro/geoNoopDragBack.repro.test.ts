/**
 * GEO-07 / GEO-10 复现探针（QA 2026-09-24，不进测试集——暴露产品缺陷时会红）。
 * 运行：复制到 web/src/canvas/ 下，`cd web && NODE_OPTIONS=--no-experimental-webstorage npx vitest run src/canvas/geoNoopDragBack.repro.test.ts`
 *
 * A. 「拖出去又拖回原处松手」：终点位移为 0，规范 GEO-07 要求「无位移时不生成 override、不冻结自动布局、
 *    不增加历史或渲染」。trackPointer 的 `moved` 只记「途中超过过 2px」，startElementDrag 随后照写
 *    `anchor + 0`——一条等于原值的 pos_frac 把标题 / 轴标签从 matplotlib 自动布局里钉死（对齐路径由
 *    layoutBoxes 的 no-op 判据挡住，拖动路径没有这一道）。
 * B. 单条 `setOverride` 是 filter + push：给已有的 override 写新值会把它挪到数组末尾。细则
 *    display-fallback-vs-geometry-authority.md 写明「override 的 upsert 原地改值，不许
 *    filter(...)+push(...)：override 数组的 JSON 就是变体键」。批量 setOverrides 用的是原地 upsert。
 */
import { beforeEach, expect, it, vi } from 'vitest'

import { literal } from '@/i18n'
import type { EngineRenderOptions, Manifest, ManifestElement } from '@/lib/api'
import { setOverride } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { flushPreviewFrame, resetPreview } from '@/store/svgPreviewStore'
import { useUiStore } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'
import { seedExactRender } from '@/test/renderFixtures'
import { emptyProject, type PanelObject } from '@/types/document'
import fixture from './__fixtures__/geoReference.json'
import { startElementDrag } from './interactions'

const engineRender = vi.fn()
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: (id: string, patches: unknown[], opts?: EngineRenderOptions) =>
    engineRender(id, patches, opts),
}))

type Fx = { size_mm: [number, number]; elements: ManifestElement[]; svg: string }
const G1 = (fixture as unknown as Record<string, Fx>).G1
const W = 5.0 * 25.4
const H = 3.2 * 25.4
const PX = 96 / 25.4
const manifest = { stem: 'G1', size_mm: G1.size_mm, elements: G1.elements } as unknown as Manifest
const title = G1.elements.find((e) => e.gid === 'axes_0.title_left')!

async function setup(overrides: PanelObject['overrides']) {
  engineRender.mockReset()
  engineRender.mockReturnValue(new Promise(() => {}))
  resetPreview()
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700 })
  useUiStore.setState({ tool: 'select', snapEnabled: false, elementPanelId: 'p1', selectedGids: [] })
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_geo_noop')
  const panel = {
    id: 'p1', type: 'panel', x: 0, y: 0, w: W, h: H, fileId: 'G1.pdf', fileKind: 'pdf',
    nativeW: W, nativeH: H, script: 'geo_library.py', overrides,
  } as unknown as PanelObject
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(panel)
  })
  useDocumentStore.setState({ past: [], future: [] })
  seedExactRender(panel, manifest, { svg: G1.svg })
  document.body.innerHTML = `<div data-element-svg="p1">${G1.svg}</div>`
}
const live = () => useDocumentStore.getState().doc.objects[0] as PanelObject
const fire = (type: string, x: number, y: number) =>
  window.dispatchEvent(new MouseEvent(type, { clientX: x, clientY: y }))

beforeEach(() => setup([]))

it('A. 拖出去 30px 再拖回原点松手：不应写 override、不应进历史、不应渲染', () => {
  startElementDrag(
    { clientX: 100, clientY: 100, button: 0, stopPropagation() {} } as unknown as React.PointerEvent,
    live(),
    title,
    { width: W * PX, height: H * PX },
  )
  for (const [x, y] of [[110, 100], [130, 100], [115, 100], [100, 100]]) {
    fire('pointermove', x, y)
    flushPreviewFrame()
  }
  fire('pointerup', 100, 100)
  console.log('[GEO-07 probe A] overrides =', JSON.stringify(live().overrides), 'past =', useDocumentStore.getState().past.length, 'engineRender =', engineRender.mock.calls.length)
  expect(live().overrides).toEqual([])
  expect(useDocumentStore.getState().past).toHaveLength(0)
  expect(engineRender).not.toHaveBeenCalled()
})

it('B. 给已有 override 写新值：应当原地改值，数组顺序不变', async () => {
  await setup([
    { gid: 'axes_0.title_left', prop: 'pos_frac', value: [0.2, 0.1] },
    { gid: 'axes_0.title_left', prop: 'color', value: '#ff0000' },
  ])
  const keyBefore = renderKeyOf(live())
  setOverride('p1', 'axes_0.title_left', 'pos_frac', [0.2, 0.1], 'none') // 同一个值
  const order = live().overrides.map((o) => o.prop)
  console.log('[GEO-07 probe B] order after same-value write =', JSON.stringify(order), 'key changed =', renderKeyOf(live()) !== keyBefore)
  expect(order).toEqual(['pos_frac', 'color'])
  expect(renderKeyOf(live())).toBe(keyBefore)
})
