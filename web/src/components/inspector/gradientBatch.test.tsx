/**
 * 多选两条渐变（正向 / 反向 y 轴）：两端颜色共享，`screen_start` 各用各自的引擎事实，
 * 不能把第一个成员的写给所有成员（否则第二条渐变的颜色整体翻转）。
 */
import { literal } from '@/i18n'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MATPLOTLIB_SVG } from '@/lib/__fixtures__/matplotlibSvg'
import type { EditableField, EngineRenderOptions, Manifest, ManifestElement } from '@/lib/api'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDocumentStore } from '@/store/documentStore'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { resetPreview, setHistoryMode } from '@/store/svgPreviewStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { ElementInspector } from './ElementInspector'

const engineRender = vi.fn()
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: (id: string, patches: unknown[], opts?: EngineRenderOptions) => engineRender(id, patches, opts),
}))
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const f = (prop: string, type: EditableField['type'], value: unknown, extra = {}): EditableField =>
  ({ prop, type, value, ...extra }) as EditableField

const gradEl = (n: number, screenStart: 'high' | 'low'): ManifestElement =>
  ({
    gid: `axes_0.fill_${n}`,
    role: 'fill',
    label: `渐变 ${n}`,
    bbox: [0.1, 0.1, 0.3, 0.3],
    draggable: false,
    editable: [
      f('gradient', 'gradient', {
        stops: ['#ff0000', '#0000ff'],
        direction: 'vertical',
        screen_start: screenStart,
        // 各自配上了自己子图里的图例色块（ADR 0167 修订）：多选改色时各带各的
        legend: {
          candidates: [{ ref: [4, n], gid: `axes_${n}.fill_4`, stops: ['#ff0000', '#0000ff'] }],
          linked: [4, n],
          auto: [4, n],
        },
      }),
    ],
  }) as unknown as ManifestElement

const manifest: Manifest = {
  rev: 1,
  size_mm: [101.6, 76.2],
  elements: [gradEl(0, 'high'), gradEl(1, 'low')],
} as unknown as Manifest

const panelOf = (): PanelObject =>
  ({
    id: 'p1',
    type: 'panel',
    x: 0,
    y: 0,
    w: 101.6,
    h: 76.2,
    fileId: 'Fig1.pdf',
    fileKind: 'pdf',
    nativeW: 101.6,
    nativeH: 76.2,
    script: 'fig.py',
    overrides: [],
  }) as unknown as PanelObject

const livePanel = (): PanelObject => {
  const p = useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1')
  if (p?.type !== 'panel') throw new Error('测试面板没了')
  return p
}
const overrideOf = (gid: string) => livePanel().overrides.find((o) => o.gid === gid && o.prop === 'gradient')?.value

let root: Root
let host: HTMLDivElement

function Harness() {
  const panel = useDocumentStore((s) => s.doc.objects.find((o) => o.id === 'p1')) as PanelObject
  return (
    <TooltipProvider>
      <ElementInspector panel={panel} />
    </TooltipProvider>
  )
}

beforeEach(async () => {
  engineRender.mockReset()
  engineRender.mockResolvedValue({
    rev: 2,
    manifest,
    svg: MATPLOTLIB_SVG,
    warnings: [],
  })
  resetPreview()
  setHistoryMode('gesture')
  localStorage.clear()
  document.body.innerHTML = ''
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_grad_batch')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(panelOf())
  })
  useRenderStore.getState().patch(renderKeyOf(panelOf()), {
    fileId: 'Fig1.pdf',
    manifest,
    svg: MATPLOTLIB_SVG,
    rev: 1,
    status: 'ready',
    lastPatches: '[]',
  })
  useRenderStore.setState({ latest: { 'Fig1.pdf': renderKeyOf(panelOf()) } })
  useDocumentStore.setState({ past: [], future: [] })
  useUiStore.setState({
    elementPanelId: 'p1',
    selectedGids: ['axes_0.fill_0', 'axes_0.fill_1'],
  })
  host = document.createElement('div')
  document.body.appendChild(host)
  const svgHost = document.createElement('div')
  svgHost.setAttribute('data-element-svg', 'p1')
  svgHost.innerHTML = MATPLOTLIB_SVG
  document.body.appendChild(svgHost)
  root = createRoot(host)
  await act(async () => {
    root.render(<Harness />)
  })
})

afterEach(async () => {
  await act(async () => {
    root?.unmount()
  })
  resetPreview()
  useUiStore.setState({ selectedGids: [] })
})

describe('多选渐变：screen_start 逐成员保留', () => {
  it('改上端颜色：两条渐变共享新 stops，各自带自己的 screen_start', async () => {
    const colors = Array.from(host.querySelectorAll('[data-gradient-stops] input[type="color"]')) as HTMLInputElement[]
    expect(colors.length).toBe(2)
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    await act(async () => {
      setter.call(colors[0], '#00ff00')
      colors[0].dispatchEvent(new Event('input', { bubbles: true }))
      colors[0].dispatchEvent(new Event('change', { bubbles: true }))
    })
    expect(overrideOf('axes_0.fill_0')).toEqual({
      stops: ['#00ff00', '#0000ff'],
      screen_start: 'high',
      legend: [4, 0],
    })
    expect(overrideOf('axes_0.fill_1')).toEqual({
      stops: ['#00ff00', '#0000ff'],
      screen_start: 'low',
      legend: [4, 1],
    })
  })
})
