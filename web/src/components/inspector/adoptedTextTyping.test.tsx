/**
 * QA-WIN-35：来源驱动采用的 PNG，属性框逐字输入只剩最后一个字符。
 * 每个字提交一次 override → 新变体 manifest 回来前，已采用面板不退「文件级」旧渲染，
 * 属性表以前会卸成构建提示，回来时重挂的文字框全选，下一个字替换前一个。
 * 修法：属性表只读显示退到**同一采用 guard** 的上一版 manifest（几何仍只认精确 manifest）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { TooltipProvider } from '@/components/ui/Tooltip'
import { literal } from '@/i18n'
import { engineRender } from '@/lib/api'
import type { EditableField, Manifest, ManifestElement } from '@/lib/api'
import { useDocumentStore } from '@/store/documentStore'
import { useInspectorPrefs } from '@/store/inspectorPrefs'
import { exactPanelManifest, renderKeyOf, sameGuardFallbackManifest, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { SCRIPT_BACKED_SOURCE_POLICY } from '@/lib/artifactValidation'
import { ElementInspector } from './ElementInspector'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: vi.fn(),
}))
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const f = (prop: string, type: EditableField['type'], value: unknown, extra = {}): EditableField =>
  ({ prop, type, value, ...extra }) as EditableField
const titleEl = {
  gid: 'axes_0.title', role: 'title', label: '标题', bbox: [0.4, 0.05, 0.2, 0.04], draggable: true,
  editable: [f('text', 'text', 'Map'), f('fontsize', 'number', 12, { min: 3, max: 36, step: 0.5, unit: 'pt' })],
} as unknown as ManifestElement
const manifest = { stem: 'p', size_mm: [80, 60], elements: [titleEl] } as unknown as Manifest

const SHA = (c: string) => c.repeat(64)
const guard = (c: string) => ({
  version: 1, policy: SCRIPT_BACKED_SOURCE_POLICY, sourceId: 'plot.png',
  bytesSha256: SHA('a'), sizeBytes: 10, status: 'reconstructed', reconstructionSha256: SHA(c),
  confirmedAt: '2026-11-03T00:00:00Z',
})
const panelOf = (c: string, overrides: PanelObject['overrides'] = [FRAME]): PanelObject =>
  ({
    id: 'p1', type: 'panel', x: 0, y: 0, w: 80, h: 60, fileId: 'plot.png', fileKind: 'raster',
    nativeW: 80, nativeH: 60, script: 'figure.py', overrides, sourceBackedFigure: guard(c),
  }) as unknown as PanelObject
const FRAME = { gid: 'figure', prop: 'frame', value: 'figsize' }
const typed = (v: string): PanelObject['overrides'] => [FRAME, { gid: 'axes_0.title', prop: 'text', value: v }]

/** 与后端同形的采用渲染响应：artifact_source 过 verifiedSource，生产路径只写 byKey、不写 latest */
const okResponse = () => ({
  rev: 1, manifest, svg: '<svg xmlns="http://www.w3.org/2000/svg"/>', warnings: [],
  artifact_source: {
    render_policy: SCRIPT_BACKED_SOURCE_POLICY, origin: 'static', kind: 'png',
    source_id: 'plot.png', bytes_sha256: SHA('a'), size_bytes: 10,
  },
})
/** 走真实的 store.render（带采用 guard）完成一次成功渲染 */
const renderAdopted = (p: PanelObject) =>
  useRenderStore.getState().render(p.fileId, p.overrides, undefined, undefined, undefined, p.sourceBackedFigure as never)

let root: Root
let host: HTMLDivElement
function Harness() {
  const panel = useDocumentStore((s) => s.doc.objects.find((o) => o.id === 'p1')) as PanelObject
  return <TooltipProvider><ElementInspector panel={panel} /></TooltipProvider>
}
const textField = () => host.querySelector<HTMLElement>('[data-inspector-gid="axes_0.title"] input[type="text"], [data-inspector-gid="axes_0.title"] textarea, [data-inspector-gid="axes_0.title"] [contenteditable]')

beforeEach(async () => {
  localStorage.clear()
  document.body.innerHTML = ''
  useInspectorPrefs.setState({ moreOpen: {}, advancedOpen: {} })
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_adopted_typing')
  useDocumentStore.getState().commit(literal('加面板'), (d) => { d.objects.push(panelOf('1')) })
  vi.mocked(engineRender).mockReset()
  vi.mocked(engineRender).mockResolvedValueOnce(okResponse() as never)
  await renderAdopted(panelOf('1'))
  useUiStore.setState({ elementPanelId: 'p1', selectedGids: ['axes_0.title'] })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<Harness />))
})
afterEach(async () => { await act(async () => root.unmount()); host.remove() })

describe('已采用 PNG：新变体 manifest 暂缺时属性表不卸文字框', () => {
  it('提交一次 override、新变体尚无 manifest：同一个输入节点仍在，没有换成构建提示', async () => {
    const before = textField()
    expect(before, '前提：标题文字框应渲染出来').not.toBeNull()
    await act(async () => {
      useDocumentStore.getState().commit(literal('输入'), (d) => {
        ;(d.objects[0] as PanelObject).overrides = typed('C')
      })
    })
    // 新变体：请求在途（永不返回）、无 manifest；生产路径从不写 latest
    vi.mocked(engineRender).mockReturnValue(new Promise(() => {}) as never)
    await act(async () => { void renderAdopted(useDocumentStore.getState().doc.objects[0] as PanelObject) })
    expect(useRenderStore.getState().latest['plot.png']).toBeUndefined()
    expect(useRenderStore.getState().byKey[renderKeyOf(useDocumentStore.getState().doc.objects[0] as PanelObject)]?.manifest ?? null).toBeNull()
    expect(textField()).toBe(before)
    // 第二个字追加：仍是同一个输入节点
    await act(async () => {
      useDocumentStore.getState().commit(literal('输入'), (d) => {
        ;(d.objects[0] as PanelObject).overrides = typed('CD')
      })
    })
    expect(textField()).toBe(before)
  })

  it('几何权威不放松：新变体无精确 manifest', () => {
    const next = panelOf('1', typed('C'))
    expect(sameGuardFallbackManifest(useRenderStore.getState(), next), '前提：属性表有退路').toBe(manifest)
    expect(exactPanelManifest(useRenderStore.getState(), next)).toBeNull()
  })

  it('来源隔离：guard 不同（重新采用 / 换了重画摘要）不退', () => {
    const s = useRenderStore.getState()
    expect(sameGuardFallbackManifest(s, panelOf('1', typed('C')))).toBe(manifest)
    expect(sameGuardFallbackManifest(s, panelOf('2', typed('C')))).toBeNull()
  })

  it('未采用的面板不走这条（走原有的文件级退路）', () => {
    const plain = { ...panelOf('1'), sourceBackedFigure: undefined } as unknown as PanelObject
    delete (plain as unknown as Record<string, unknown>).sourceBackedFigure
    expect(sameGuardFallbackManifest(useRenderStore.getState(), plain)).toBeNull()
  })
})
