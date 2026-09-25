/**
 * GEO-03 复现探针（QA 2026-09-24，不进测试集——暴露产品缺陷时会红）。
 *
 * 图内多选同时选中 Axes 与它自己的孩子（标题），整组平移：
 *   * 提交：Axes 写 position（+Δ），标题写 pos_frac（anchor+Δ，figure 锚定）→ 引擎里标题只走一次；
 *   * 预览：startElementGroupMove 对**每个成员**各 previewTransform 一次，而标题的 <g> 嵌在
 *     <g id="axes_0"> 里——祖先一次 + 自己一次 = 屏幕上走两倍。startAxesDrag 明确写了
 *     「后代不单独平移：它们嵌在宿主的 <g> 里，已经跟着动了」，整组平移没有这条。
 *
 * 判据（主语：拖动中那一帧、标题节点在 SVG 用户坐标里的**累计**平移 = 自己 + 所有祖先的 translate）：
 * 累计平移应当 == Δ（一次），不是 2Δ。
 */
import { beforeEach, expect, it, vi } from 'vitest'

import { literal } from '@/i18n'
import type { EngineRenderOptions, Manifest, ManifestElement } from '@/lib/api'
import { alignEntries } from '@/lib/elementGeom'
import { useDocumentStore } from '@/store/documentStore'
import { useRenderStore } from '@/store/renderStore'
import { flushPreviewFrame, resetPreview } from '@/store/svgPreviewStore'
import { useUiStore } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'
import { seedExactRender } from '@/test/renderFixtures'
import { emptyProject, type PanelObject } from '@/types/document'
import fixture from './__fixtures__/geoReference.json'
import { startElementGroupMove } from './interactions'

const engineRender = vi.fn()
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: (id: string, patches: unknown[], opts?: EngineRenderOptions) =>
    engineRender(id, patches, opts),
}))

type Fx = { size_mm: [number, number]; elements: ManifestElement[]; svg: string }
const G2 = (fixture as unknown as Record<string, Fx>).G2
const W = 6.4 * 25.4
const H = 3.0 * 25.4
const PX = 96 / 25.4
const manifest = { stem: 'G2', size_mm: G2.size_mm, elements: G2.elements } as unknown as Manifest
const panel = {
  id: 'p1', type: 'panel', x: 0, y: 0, w: W, h: H, fileId: 'G2.pdf', fileKind: 'pdf',
  nativeW: W, nativeH: H, script: 'geo_library.py', overrides: [],
} as unknown as PanelObject

beforeEach(async () => {
  engineRender.mockReset()
  engineRender.mockReturnValue(new Promise(() => {}))
  resetPreview()
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700 })
  useUiStore.setState({ tool: 'select', snapEnabled: false, elementPanelId: 'p1', selectedGids: [] })
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_geo_parent_child')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(panel)
  })
  seedExactRender(panel, manifest, { svg: G2.svg })
  document.body.innerHTML = `<div data-element-svg="p1">${G2.svg}</div>`
})

/** 节点自己 + 所有祖先上 `translate(a,b)` 前缀的累计值（预览只写这一种前缀） */
function cumulativeTranslate(gid: string): [number, number] {
  let n: Element | null = document.querySelector(`[data-element-svg="p1"] [id="${gid}"]`)
  let x = 0
  let y = 0
  while (n && n.tagName.toLowerCase() !== 'svg') {
    const m = /^translate\(([^,]+),([^)]+)\)/.exec(n.getAttribute('transform') ?? '')
    if (m) {
      x += Number(m[1])
      y += Number(m[2])
    }
    n = n.parentElement
  }
  return [x, y]
}

it('选中 Axes + 它自己的标题一起平移：标题在预览里只应走一次', () => {
  const live = useDocumentStore.getState().doc.objects[0] as PanelObject
  const entries = alignEntries(live, manifest, ['axes_0', 'axes_0.title'])
  expect(entries.map((e) => e.key).sort()).toEqual(['axes_0', 'axes_0.title'])
  // 标题确实嵌在 axes_0 的 <g> 里（这是前提，不成立的话这条探针量的就不是这件事）
  const title = document.querySelector('[data-element-svg="p1"] [id="axes_0.title"]')!
  expect(title.closest('[id="axes_0"]')).not.toBeNull()

  startElementGroupMove(
    { clientX: 0, clientY: 0, button: 0, stopPropagation() {} } as unknown as React.PointerEvent,
    live,
    entries,
    { width: W * PX, height: H * PX },
  )
  const ds = [40, 20]
  for (let i = 1; i <= 10; i++) {
    window.dispatchEvent(new MouseEvent('pointermove', { clientX: (ds[0] * i) / 10, clientY: (ds[1] * i) / 10 }))
    flushPreviewFrame()
  }
  const vbW = 460.8
  const vbH = 216
  const once = [(ds[0] / (W * PX)) * vbW, (ds[1] / (H * PX)) * vbH]
  const got = cumulativeTranslate('axes_0.title')
  // 记录实测值，便于在日志里直接看到「两倍」
  console.log(`[GEO-03 probe] 期望累计平移 ${once.map((v) => v.toFixed(4))}，实测 ${got.map((v) => v.toFixed(4))}`)
  expect(got[0]).toBeCloseTo(once[0], 6)
  expect(got[1]).toBeCloseTo(once[1], 6)
  window.dispatchEvent(new MouseEvent('pointerup', { clientX: ds[0], clientY: ds[1] }))
  // 提交侧：两条 override，标题的 pos_frac 只加了一次 Δ（引擎里 figure 锚定，只走一次）
  const ov = (useDocumentStore.getState().doc.objects[0] as PanelObject).overrides
  console.log('[GEO-03 probe] overrides', JSON.stringify(ov))
})
